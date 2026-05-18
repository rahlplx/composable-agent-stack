"""Z.ai Execution Memory — SQLite Compression Manager.

PURPOSE: This is the AI agent's OWN memory management system.
It solves the problem of context window overflow during long
execution runs by:

1. Persisting knowledge/context to SQLite (survives session restart)
2. Auto-triggering /compact when context grows too large
3. Merging, deduplicating, and summarizing old entries
4. Protecting critical knowledge from deletion
5. Supporting multiple isolated sessions for parallel work streams

USAGE (by Z.ai during execution):
    mem = CompressionManager(db_path="data/memory.db")
    await mem.initialize()

    # Store execution knowledge
    await mem.store("litellm_config", value=config_dict,
                     category="reference", priority="critical")

    # Auto-compact triggers when session exceeds threshold
    # Manual compact also available
    result = await mem.compact(session_id)

    # Retrieve knowledge later (even after context overflow)
    config = await mem.retrieve("litellm_config", category="reference")

DESIGN DECISIONS:
- SQLite because it's zero-config, single-file, survives restarts
- aiosqlite for async (non-blocking) access from FastAPI
- Priority system: critical > high > medium > low
  - critical entries are NEVER compacted away
  - high entries are preserved with full fidelity
  - medium entries can be summarized
  - low entries can be aggressively compressed
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiosqlite


# ─── Data Models ─────────────────────────────────────────────────────────────

class Priority(str, Enum):
    CRITICAL = "critical"   # Never compact — always preserved verbatim
    HIGH = "high"           # Preserve with full fidelity during compact
    MEDIUM = "medium"       # Can be summarized/merged
    LOW = "low"             # Aggressively compressed


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPACTING = "compacting"   # /compact in progress
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)
    total_bytes: int = 0
    entry_count: int = 0

    def touch(self) -> None:
        self.last_accessed = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "metadata": json.dumps(self.metadata),
            "total_bytes": self.total_bytes,
            "entry_count": self.entry_count,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Session:
        d = dict(row)
        d["status"] = SessionStatus(d["status"])
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        return cls(**d)


@dataclass
class ContextEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    category: str = "general"      # reference, workflow, debug, decision, output
    key: str = ""
    value: Any = None
    priority: Priority = Priority.MEDIUM
    size_bytes: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_summary: bool = False       # True if this entry was created by /compact
    source: str = ""               # Who/what created this entry

    def compute_size(self) -> int:
        self.size_bytes = len(json.dumps(self.value, default=str).encode())
        return self.size_bytes

    def to_dict(self) -> dict:
        return {
            "id": self.id, "session_id": self.session_id,
            "category": self.category, "key": self.key,
            "value": json.dumps(self.value, default=str),
            "priority": self.priority.value,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_summary": self.is_summary,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ContextEntry:
        d = dict(row)
        d["value"] = json.loads(d.get("value", "null"))
        d["priority"] = Priority(d.get("priority", "medium"))
        return cls(**d)


@dataclass
class CompactResult:
    session_id: str = ""
    entries_before: int = 0
    entries_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    compression_ratio: float = 0.0
    critical_preserved: int = 0
    high_preserved: int = 0
    medium_merged: int = 0
    low_compressed: int = 0
    categories_affected: list = field(default_factory=list)

    @property
    def savings_pct(self) -> float:
        if self.bytes_before == 0:
            return 0.0
        return round((1 - self.bytes_after / self.bytes_before) * 100, 1)


@dataclass
class CompactHistoryEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    triggered_by: str = ""         # "auto" or "manual"
    entries_before: int = 0
    entries_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    compression_ratio: float = 0.0
    critical_preserved: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


# ─── SQL Schema ──────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    total_bytes INTEGER NOT NULL DEFAULT 0,
    entry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS context_entries (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_summary INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(session_id, category, key)
);

CREATE TABLE IF NOT EXISTS compact_history (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    triggered_by TEXT NOT NULL DEFAULT 'manual',
    entries_before INTEGER NOT NULL DEFAULT 0,
    entries_after INTEGER NOT NULL DEFAULT 0,
    bytes_before INTEGER NOT NULL DEFAULT 0,
    bytes_after INTEGER NOT NULL DEFAULT 0,
    compression_ratio REAL NOT NULL DEFAULT 0.0,
    critical_preserved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entries_session ON context_entries(session_id);
CREATE INDEX IF NOT EXISTS idx_entries_category ON context_entries(session_id, category);
CREATE INDEX IF NOT EXISTS idx_entries_priority ON context_entries(priority);
CREATE INDEX IF NOT EXISTS idx_sessions_last_accessed ON sessions(last_accessed);
CREATE INDEX IF NOT EXISTS idx_compact_session ON compact_history(session_id);
"""


# ─── Compression Manager ────────────────────────────────────────────────────

class CompressionManager:
    """Z.ai's execution memory manager with auto-compact.

    This is the AI's OWN tool for managing context during long
    execution runs. It persists knowledge to SQLite so it survives
    context window overflow, and auto-triggers /compact to keep
    the working set small.
    """

    def __init__(
        self,
        db_path: str = "data/memory.db",
        compact_threshold_bytes: int = 50_000,
        compact_max_snapshot_bytes: int = 200_000,
        session_ttl_hours: int = 72,
        auto_compact_interval_seconds: int = 60,
    ):
        self.db_path = db_path
        self.compact_threshold_bytes = compact_threshold_bytes
        self.compact_max_snapshot_bytes = compact_max_snapshot_bytes
        self.session_ttl_hours = session_ttl_hours
        self.auto_compact_interval_seconds = auto_compact_interval_seconds

        self._db: Optional[aiosqlite.Connection] = None
        self._initialized = False

    # ── Lifecycle ────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open DB connection and create schema."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        self._initialized = True

    async def shutdown(self) -> None:
        """Close DB connection."""
        if self._db:
            await self._db.close()
            self._db = None
        self._initialized = False

    def _require_init(self) -> None:
        if not self._initialized or self._db is None:
            raise RuntimeError("CompressionManager not initialized. Call initialize() first.")

    # ── Session Management ───────────────────────────────────────────

    async def create_session(self, name: str = "", metadata: dict | None = None) -> Session:
        """Create a new isolated session for a work stream."""
        self._require_init()
        session = Session(name=name, metadata=metadata or {})
        await self._db.execute(
            "INSERT INTO sessions (id, name, status, created_at, last_accessed, metadata, total_bytes, entry_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session.id, session.name, session.status.value,
             session.created_at, session.last_accessed,
             json.dumps(session.metadata), session.total_bytes, session.entry_count)
        )
        await self._db.commit()
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID."""
        self._require_init()
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Session.from_row(row)

    async def list_sessions(self, status: SessionStatus | None = None) -> list[Session]:
        """List all sessions, optionally filtered by status."""
        self._require_init()
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY last_accessed DESC",
                (status.value,)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM sessions ORDER BY last_accessed DESC"
            )
        rows = await cursor.fetchall()
        return [Session.from_row(r) for r in rows]

    async def close_session(self, session_id: str) -> bool:
        """Mark a session as closed."""
        self._require_init()
        await self._db.execute(
            "UPDATE sessions SET status = ?, last_accessed = ? WHERE id = ?",
            (SessionStatus.CLOSED.value, datetime.now(timezone.utc).isoformat(), session_id)
        )
        await self._db.commit()
        return True

    async def _update_session_stats(self, session_id: str) -> None:
        """Recalculate and store session size/entry count."""
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) as total_bytes, COUNT(*) as entry_count "
            "FROM context_entries WHERE session_id = ?",
            (session_id,)
        )
        row = await cursor.fetchone()
        total_bytes = row["total_bytes"]
        entry_count = row["entry_count"]
        await self._db.execute(
            "UPDATE sessions SET total_bytes = ?, entry_count = ?, last_accessed = ? WHERE id = ?",
            (total_bytes, entry_count, datetime.now(timezone.utc).isoformat(), session_id)
        )
        await self._db.commit()

    # ── Context Storage ──────────────────────────────────────────────

    async def store(
        self,
        key: str,
        value: Any,
        session_id: str | None = None,
        category: str = "general",
        priority: Priority = Priority.MEDIUM,
        source: str = "",
    ) -> str:
        """Store a context entry. If session_id is None, uses the active session.

        Categories:
        - "reference"  : Knowledge artifacts (LiteLLM config, architecture refs)
        - "workflow"   : Task definitions, DAGs, dispatch decisions
        - "decision"   : Architecture decisions, trade-off choices
        - "debug"      : Error logs, troubleshooting state
        - "output"     : Generated code, configs, results
        - "general"    : Catch-all

        Priority:
        - CRITICAL: Never compacted (reference configs, state schemas)
        - HIGH: Preserved verbatim (current workflow DAG, active tasks)
        - MEDIUM: Can be summarized (old debug logs, intermediate steps)
        - LOW: Aggressively compressed (raw LLM responses, large payloads)
        """
        self._require_init()

        # Auto-select active session if none specified
        if session_id is None:
            sessions = await self.list_sessions(status=SessionStatus.ACTIVE)
            if not sessions:
                session = await self.create_session(name="default")
                session_id = session.id
            else:
                session_id = sessions[0].id

        entry = ContextEntry(
            session_id=session_id,
            category=category,
            key=key,
            value=value,
            priority=priority,
            source=source,
        )
        entry.compute_size()

        # Upsert: INSERT OR REPLACE to handle overwrites
        await self._db.execute(
            """INSERT OR REPLACE INTO context_entries
               (id, session_id, category, key, value, priority, size_bytes,
                created_at, updated_at, is_summary, source)
               VALUES (
                 COALESCE((SELECT id FROM context_entries WHERE session_id = ? AND category = ? AND key = ?), ?),
                 ?, ?, ?, ?, ?, ?,
                 COALESCE((SELECT created_at FROM context_entries WHERE session_id = ? AND category = ? AND key = ?), ?),
                 ?, ?, ?
               )""",
            (session_id, category, key, entry.id,
             session_id, category, key,
             json.dumps(value, default=str), priority.value, entry.size_bytes,
             session_id, category, key, entry.created_at,
             datetime.now(timezone.utc).isoformat(), 0, source)
        )
        await self._db.commit()
        await self._update_session_stats(session_id)

        # Auto-trigger /compact if threshold exceeded
        session = await self.get_session(session_id)
        if session and session.total_bytes > self.compact_threshold_bytes:
            await self.compact(session_id, triggered_by="auto")

        return entry.id

    async def retrieve(
        self,
        key: str,
        session_id: str | None = None,
        category: str | None = None,
    ) -> Any:
        """Retrieve a context entry's value by key."""
        self._require_init()
        if session_id is None:
            sessions = await self.list_sessions(status=SessionStatus.ACTIVE)
            if not sessions:
                return None
            session_id = sessions[0].id

        if category:
            cursor = await self._db.execute(
                "SELECT * FROM context_entries WHERE session_id = ? AND category = ? AND key = ?",
                (session_id, category, key)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM context_entries WHERE session_id = ? AND key = ?",
                (session_id, key)
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        entry = ContextEntry.from_row(row)
        return entry.value

    async def get_context(
        self,
        session_id: str,
        category: str | None = None,
        priority: Priority | None = None,
    ) -> list[dict]:
        """Get context entries for a session, optionally filtered."""
        self._require_init()
        query = "SELECT * FROM context_entries WHERE session_id = ?"
        params: list = [session_id]

        if category:
            query += " AND category = ?"
            params.append(category)
        if priority:
            query += " AND priority = ?"
            params.append(priority.value)

        query += " ORDER BY updated_at DESC"
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        entries = [ContextEntry.from_row(r) for r in rows]
        return [
            {
                "id": e.id, "key": e.key, "value": e.value,
                "category": e.category, "priority": e.priority.value,
                "size_bytes": e.size_bytes, "is_summary": e.is_summary,
                "source": e.source, "updated_at": e.updated_at,
            }
            for e in entries
        ]

    async def get_session_size(self, session_id: str) -> int:
        """Get total bytes used by a session."""
        self._require_init()
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) as total FROM context_entries WHERE session_id = ?",
            (session_id,)
        )
        row = await cursor.fetchone()
        return row["total"]

    # ── /compact Tool ────────────────────────────────────────────────

    async def compact(self, session_id: str, triggered_by: str = "manual") -> CompactResult:
        """Execute /compact: compress session context.

        Strategy by priority:
        - CRITICAL: Never touched. Preserved verbatim.
        - HIGH: Preserved verbatim but deduplicated (same key = keep latest).
        - MEDIUM: Merged into summaries when >5 entries per category.
                  Most recent 3 entries kept individually.
        - LOW: Aggressively merged. Only summary kept, no individual entries.
        """
        self._require_init()

        # Mark session as compacting
        await self._db.execute(
            "UPDATE sessions SET status = ? WHERE id = ?",
            (SessionStatus.COMPACTING.value, session_id)
        )
        await self._db.commit()

        # Gather stats before
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(size_bytes), 0) as total FROM context_entries WHERE session_id = ?",
            (session_id,)
        )
        before = await cursor.fetchone()
        entries_before = before["cnt"]
        bytes_before = before["total"]

        result = CompactResult(
            session_id=session_id,
            entries_before=entries_before,
            bytes_before=bytes_before,
        )

        # Process by priority level
        # 1. CRITICAL — count, never modify
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM context_entries WHERE session_id = ? AND priority = ?",
            (session_id, Priority.CRITICAL.value)
        )
        result.critical_preserved = (await cursor.fetchone())["cnt"]

        # 2. HIGH — deduplicate (already handled by UNIQUE constraint), count
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM context_entries WHERE session_id = ? AND priority = ?",
            (session_id, Priority.HIGH.value)
        )
        result.high_preserved = (await cursor.fetchone())["cnt"]

        # 3. MEDIUM — merge per category if >5 entries
        cursor = await self._db.execute(
            "SELECT category, COUNT(*) as cnt FROM context_entries "
            "WHERE session_id = ? AND priority = ? GROUP BY category",
            (session_id, Priority.MEDIUM.value)
        )
        categories = await cursor.fetchall()
        medium_merged = 0
        categories_affected = []

        for cat_row in categories:
            cat = cat_row["category"]
            cnt = cat_row["cnt"]
            if cnt > 5:
                categories_affected.append(cat)
                # Fetch all entries in this category
                cursor = await self._db.execute(
                    "SELECT * FROM context_entries WHERE session_id = ? AND category = ? AND priority = ? "
                    "ORDER BY updated_at ASC",
                    (session_id, cat, Priority.MEDIUM.value)
                )
                cat_entries = await cursor.fetchall()

                # Build merged summary value
                merged_values = {}
                for e_row in cat_entries[:-3]:  # all except last 3
                    e = ContextEntry.from_row(e_row)
                    merged_values[e.key] = e.value
                    # Delete old individual entries
                    await self._db.execute(
                        "DELETE FROM context_entries WHERE id = ?", (e.id,)
                    )
                    medium_merged += 1

                # Insert or replace summary entry (may exist from prior compact)
                # First delete any existing summary for this category
                await self._db.execute(
                    "DELETE FROM context_entries WHERE session_id = ? AND category = ? AND key LIKE ? AND is_summary = 1",
                    (session_id, cat, f"_merged_{cat}_summary")
                )
                summary_id = str(uuid.uuid4())
                # Compact the values: for each, keep only essential fields
                compacted_values = {}
                for k, v in merged_values.items():
                    if isinstance(v, dict) and len(v) > 3:
                        # Keep first 3 fields + note
                        items = list(v.items())[:3]
                        compacted_values[k] = {k_: v_ for k_, v_ in items}
                        compacted_values[k]["_truncated"] = True
                    else:
                        compacted_values[k] = v
                summary_value = json.dumps(compacted_values, default=str)
                summary_size = len(summary_value.encode())
                now = datetime.now(timezone.utc).isoformat()
                await self._db.execute(
                    """INSERT INTO context_entries
                       (id, session_id, category, key, value, priority, size_bytes,
                        created_at, updated_at, is_summary, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (summary_id, session_id, cat,
                     f"_merged_{cat}_summary", summary_value,
                     Priority.MEDIUM.value, summary_size,
                     now, now, 1, "compact")
                )

        result.medium_merged = medium_merged

        # 4. LOW — aggressively compress: merge all per category, keep only summary
        cursor = await self._db.execute(
            "SELECT category, COUNT(*) as cnt FROM context_entries "
            "WHERE session_id = ? AND priority = ? GROUP BY category",
            (session_id, Priority.LOW.value)
        )
        low_categories = await cursor.fetchall()
        low_compressed = 0

        for cat_row in low_categories:
            cat = cat_row["category"]
            if cat not in categories_affected:
                categories_affected.append(cat)

            cursor = await self._db.execute(
                "SELECT * FROM context_entries WHERE session_id = ? AND category = ? AND priority = ? "
                "ORDER BY updated_at ASC",
                (session_id, cat, Priority.LOW.value)
            )
            cat_entries = await cursor.fetchall()

            merged_values = {}
            for e_row in cat_entries:
                e = ContextEntry.from_row(e_row)
                merged_values[e.key] = e.value
                await self._db.execute("DELETE FROM context_entries WHERE id = ?", (e.id,))
                low_compressed += 1

            # Delete any existing summary for this category
            await self._db.execute(
                "DELETE FROM context_entries WHERE session_id = ? AND category = ? AND key LIKE ? AND is_summary = 1",
                (session_id, cat, f"_compressed_{cat}_summary")
            )
            # Single summary per category — aggressively compact values
            compacted_values = {}
            for k, v in merged_values.items():
                if isinstance(v, dict) and len(v) > 2:
                    items = list(v.items())[:2]
                    compacted_values[k] = {k_: v_ for k_, v_ in items}
                else:
                    compacted_values[k] = v
            summary_id = str(uuid.uuid4())
            summary_value = json.dumps(compacted_values, default=str)
            summary_size = len(summary_value.encode())
            now = datetime.now(timezone.utc).isoformat()
            await self._db.execute(
                """INSERT INTO context_entries
                   (id, session_id, category, key, value, priority, size_bytes,
                    created_at, updated_at, is_summary, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (summary_id, session_id, cat,
                 f"_compressed_{cat}_summary", summary_value,
                 Priority.LOW.value, summary_size,
                 now, now, 1, "compact")
            )

        result.low_compressed = low_compressed
        result.categories_affected = categories_affected

        await self._db.commit()
        await self._update_session_stats(session_id)

        # Gather stats after
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(size_bytes), 0) as total FROM context_entries WHERE session_id = ?",
            (session_id,)
        )
        after = await cursor.fetchone()
        result.entries_after = after["cnt"]
        result.bytes_after = after["total"]
        if bytes_before > 0:
            result.compression_ratio = round(result.bytes_after / bytes_before, 3)

        # Restore session status
        await self._db.execute(
            "UPDATE sessions SET status = ? WHERE id = ?",
            (SessionStatus.ACTIVE.value, session_id)
        )

        # Record compact history
        history_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO compact_history
               (id, session_id, triggered_by, entries_before, entries_after,
                bytes_before, bytes_after, compression_ratio, critical_preserved, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (history_id, session_id, triggered_by,
             result.entries_before, result.entries_after,
             result.bytes_before, result.bytes_after,
             result.compression_ratio, result.critical_preserved, now)
        )
        await self._db.commit()

        return result

    # ── History & Audit ──────────────────────────────────────────────

    async def get_compact_history(self, session_id: str) -> list[dict]:
        """Get all /compact operations for a session."""
        self._require_init()
        cursor = await self._db.execute(
            "SELECT * FROM compact_history WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── TTL Cleanup ──────────────────────────────────────────────────

    async def cleanup_expired(self) -> int:
        """Remove sessions that haven't been accessed within TTL.

        Returns the number of sessions cleaned up.
        """
        self._require_init()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.session_ttl_hours)).isoformat()
        cursor = await self._db.execute(
            "SELECT id FROM sessions WHERE status != ? AND last_accessed < ?",
            (SessionStatus.ACTIVE.value, cutoff)
        )
        expired = await cursor.fetchall()

        for row in expired:
            sid = row["id"]
            await self._db.execute("DELETE FROM context_entries WHERE session_id = ?", (sid,))
            await self._db.execute("DELETE FROM compact_history WHERE session_id = ?", (sid,))
            await self._db.execute("DELETE FROM sessions WHERE id = ?", (sid,))

        await self._db.commit()
        return len(expired)

    async def _force_expire_session(self, session_id: str, hours_ago: int = 2) -> None:
        """Test helper: force a session's last_accessed into the past and mark inactive."""
        past = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        await self._db.execute(
            "UPDATE sessions SET last_accessed = ?, status = ? WHERE id = ?",
            (past, SessionStatus.CLOSED.value, session_id)
        )
        await self._db.commit()

    # ── Utility ──────────────────────────────────────────────────────

    async def store_reference(
        self, key: str, value: Any, source: str = ""
    ) -> str:
        """Convenience: store a CRITICAL reference (never compacted)."""
        return await self.store(key, value, category="reference",
                                priority=Priority.CRITICAL, source=source)

    async def store_workflow(
        self, key: str, value: Any, source: str = ""
    ) -> str:
        """Convenience: store a HIGH priority workflow entry."""
        return await self.store(key, value, category="workflow",
                                priority=Priority.HIGH, source=source)

    async def store_decision(
        self, key: str, value: Any, source: str = ""
    ) -> str:
        """Convenience: store a HIGH priority architecture decision."""
        return await self.store(key, value, category="decision",
                                priority=Priority.HIGH, source=source)

    async def store_debug(
        self, key: str, value: Any, source: str = ""
    ) -> str:
        """Convenience: store a MEDIUM priority debug entry (compactable)."""
        return await self.store(key, value, category="debug",
                                priority=Priority.MEDIUM, source=source)

    async def store_output(
        self, key: str, value: Any, source: str = ""
    ) -> str:
        """Convenience: store a LOW priority output (aggressively compactable)."""
        return await self.store(key, value, category="output",
                                priority=Priority.LOW, source=source)

    async def snapshot(self, session_id: str | None = None) -> dict:
        """Get a full snapshot of session state for context injection.

        This is what Z.ai calls before generating a response to
        inject the relevant working context into the prompt.
        """
        self._require_init()
        if session_id is None:
            sessions = await self.list_sessions(status=SessionStatus.ACTIVE)
            if not sessions:
                return {"sessions": [], "context": []}
            session_id = sessions[0].id

        session = await self.get_session(session_id)
        if session is None:
            return {"sessions": [], "context": []}

        # Get CRITICAL and HIGH entries verbatim
        critical = await self.get_context(session_id, priority=Priority.CRITICAL)
        high = await self.get_context(session_id, priority=Priority.HIGH)
        medium = await self.get_context(session_id, priority=Priority.MEDIUM)
        low = await self.get_context(session_id, priority=Priority.LOW)

        # For snapshot, include critical+high in full,
        # medium: only keys + summaries, low: skip
        context = []
        for e in critical + high:
            context.append(e)
        for e in medium:
            if e.get("is_summary"):
                context.append(e)
            else:
                context.append({"key": e["key"], "category": e["category"],
                               "size_bytes": e["size_bytes"],
                               "note": "use retrieve() for full value"})

        return {
            "session": {
                "id": session.id, "name": session.name,
                "status": session.status.value,
                "total_bytes": session.total_bytes,
                "entry_count": session.entry_count,
            },
            "context": context,
            "low_entries_count": len(low),
        }
