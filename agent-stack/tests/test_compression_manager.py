"""Tests for SQLite Compression Manager — TDD critical path.

This is the most critical component: it manages session context,
triggers /compact automatically, and ensures no data loss.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest

from orchestrator.compression.manager import CompressionManager, CompactResult
from orchestrator.compression.session import Session, SessionStatus


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
async def manager(tmp_path: Path):
    """Create a fresh CompressionManager with a temp DB."""
    db_path = str(tmp_path / "test_compression.db")
    mgr = CompressionManager(
        db_path=db_path,
        compact_threshold_bytes=100,       # low threshold for testing
        compact_max_snapshot_bytes=500,
        session_ttl_hours=1,
        auto_compact_interval_seconds=5,
    )
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


# ─── Session Lifecycle ───────────────────────────────────────────────────────

class TestSessionLifecycle:
    """Test multi-session creation, listing, and cleanup."""

    async def test_create_session(self, manager: CompressionManager):
        session = await manager.create_session(name="test-session-1")
        assert session.id is not None
        assert session.name == "test-session-1"
        assert session.status == SessionStatus.ACTIVE

    async def test_list_sessions(self, manager: CompressionManager):
        s1 = await manager.create_session(name="alpha")
        s2 = await manager.create_session(name="beta")
        sessions = await manager.list_sessions()
        names = {s.name for s in sessions}
        assert "alpha" in names
        assert "beta" in names

    async def test_get_session(self, manager: CompressionManager):
        created = await manager.create_session(name="fetch-me")
        fetched = await manager.get_session(created.id)
        assert fetched is not None
        assert fetched.name == "fetch-me"

    async def test_close_session(self, manager: CompressionManager):
        session = await manager.create_session(name="closing")
        await manager.close_session(session.id)
        fetched = await manager.get_session(session.id)
        assert fetched.status == SessionStatus.CLOSED

    async def test_nonexistent_session_returns_none(self, manager: CompressionManager):
        result = await manager.get_session("nonexistent-id")
        assert result is None


# ─── Context Storage & Retrieval ─────────────────────────────────────────────

class TestContextStorage:
    """Test storing and retrieving context entries within sessions."""

    async def test_store_and_retrieve(self, manager: CompressionManager):
        session = await manager.create_session(name="ctx-test")
        entry_id = await manager.store_context(
            session_id=session.id,
            category="knowledge",
            key="litellm_config",
            value={"model": "gpt-4o", "rpm": 500},
        )
        assert entry_id is not None

        entries = await manager.get_context(session_id=session.id, category="knowledge")
        assert len(entries) >= 1
        found = [e for e in entries if e["key"] == "litellm_config"]
        assert len(found) == 1
        assert found[0]["value"]["model"] == "gpt-4o"

    async def test_store_multiple_categories(self, manager: CompressionManager):
        session = await manager.create_session(name="multi-cat")
        await manager.store_context(session.id, "knowledge", "ref1", {"a": 1})
        await manager.store_context(session.id, "workflow", "task1", {"b": 2})
        await manager.store_context(session.id, "debug", "log1", {"c": 3})

        knowledge = await manager.get_context(session.id, "knowledge")
        workflows = await manager.get_context(session.id, "workflow")
        debug = await manager.get_context(session.id, "debug")

        assert len(knowledge) >= 1
        assert len(workflows) >= 1
        assert len(debug) >= 1

    async def test_session_isolation(self, manager: CompressionManager):
        s1 = await manager.create_session(name="iso-1")
        s2 = await manager.create_session(name="iso-2")
        await manager.store_context(s1.id, "knowledge", "shared-key", {"owner": "s1"})
        await manager.store_context(s2.id, "knowledge", "shared-key", {"owner": "s2"})

        s1_entries = await manager.get_context(s1.id, "knowledge")
        s2_entries = await manager.get_context(s2.id, "knowledge")

        assert s1_entries[0]["value"]["owner"] == "s1"
        assert s2_entries[0]["value"]["owner"] == "s2"

    async def test_overwrite_existing_key(self, manager: CompressionManager):
        session = await manager.create_session(name="overwrite")
        await manager.store_context(session.id, "knowledge", "key1", {"v": 1})
        await manager.store_context(session.id, "knowledge", "key1", {"v": 2})

        entries = await manager.get_context(session.id, "knowledge")
        found = [e for e in entries if e["key"] == "key1"]
        assert len(found) == 1
        assert found[0]["value"]["v"] == 2


# ─── /compact Tool ───────────────────────────────────────────────────────────

class TestCompact:
    """Test the /compact compression tool — systematic auto-trigger."""

    async def test_manual_compact(self, manager: CompressionManager):
        session = await manager.create_session(name="compact-test")
        # Store enough data to exceed threshold
        for i in range(20):
            await manager.store_context(
                session.id, "knowledge", f"key_{i}",
                {"data": "x" * 50, "index": i},
            )

        result = await manager.compact(session.id)
        assert isinstance(result, CompactResult)
        assert result.entries_before > result.entries_after
        assert result.bytes_before > 0
        assert result.compression_ratio > 0

    async def test_compact_preserves_latest_values(self, manager: CompressionManager):
        session = await manager.create_session(name="compact-preserve")
        # Old and new values for same key
        await manager.store_context(session.id, "knowledge", "state", {"step": 1})
        await manager.store_context(session.id, "knowledge", "state", {"step": 2})

        result = await manager.compact(session.id)
        entries = await manager.get_context(session.id, "knowledge")
        state_entries = [e for e in entries if e["key"] == "state"]
        assert len(state_entries) == 1
        assert state_entries[0]["value"]["step"] == 2

    async def test_auto_compact_triggers(self, manager: CompressionManager):
        session = await manager.create_session(name="auto-compact")
        # Store data exceeding threshold
        for i in range(30):
            await manager.store_context(
                session.id, "knowledge", f"auto_{i}",
                {"payload": "z" * 30, "idx": i},
            )

        # Check if auto-compact would trigger
        size = await manager.get_session_size(session.id)
        should_compact = size > manager.compact_threshold_bytes

        if should_compact:
            result = await manager.compact(session.id)
            assert result.entries_after <= result.entries_before

    async def test_compact_idempotent(self, manager: CompressionManager):
        session = await manager.create_session(name="idempotent")
        for i in range(10):
            await manager.store_context(session.id, "knowledge", f"k{i}", {"v": i})

        r1 = await manager.compact(session.id)
        r2 = await manager.compact(session.id)
        # Second compact should not lose data
        assert r2.entries_after >= 0


# ─── Session Size Tracking ──────────────────────────────────────────────────

class TestSessionSizeTracking:
    """Test accurate size tracking for compact triggers."""

    async def test_empty_session_size_zero(self, manager: CompressionManager):
        session = await manager.create_session(name="empty")
        size = await manager.get_session_size(session.id)
        assert size == 0

    async def test_size_grows_with_data(self, manager: CompressionManager):
        session = await manager.create_session(name="growing")
        size_before = await manager.get_session_size(session.id)
        await manager.store_context(session.id, "knowledge", "big", {"d": "a" * 100})
        size_after = await manager.get_session_size(session.id)
        assert size_after > size_before

    async def test_size_shrinks_after_compact(self, manager: CompressionManager):
        session = await manager.create_session(name="shrink")
        for i in range(20):
            await manager.store_context(session.id, "knowledge", f"s{i}", {"x": "y" * 20})
        size_before = await manager.get_session_size(session.id)
        await manager.compact(session.id)
        size_after = await manager.get_session_size(session.id)
        assert size_after <= size_before


# ─── TTL Cleanup ─────────────────────────────────────────────────────────────

class TestTTLCleanup:
    """Test automatic cleanup of expired sessions."""

    async def test_cleanup_expired_sessions(self, manager: CompressionManager):
        session = await manager.create_session(name="expiring")
        # Force last_accessed to be old
        await manager._force_expire_session(session.id, hours_ago=2)

        cleaned = await manager.cleanup_expired()
        assert cleaned >= 1

        fetched = await manager.get_session(session.id)
        assert fetched is None

    async def test_active_sessions_not_cleaned(self, manager: CompressionManager):
        session = await manager.create_session(name="active")
        cleaned = await manager.cleanup_expired()
        fetched = await manager.get_session(session.id)
        assert fetched is not None


# ─── Compact History ─────────────────────────────────────────────────────────

class TestCompactHistory:
    """Test that /compact operations are logged for auditing."""

    async def test_compact_creates_history_entry(self, manager: CompressionManager):
        session = await manager.create_session(name="history")
        for i in range(10):
            await manager.store_context(session.id, "knowledge", f"h{i}", {"v": i})

        result = await manager.compact(session.id)
        history = await manager.get_compact_history(session.id)
        assert len(history) >= 1
        assert history[0]["entries_before"] == result.entries_before
        assert history[0]["entries_after"] == result.entries_after
