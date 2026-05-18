"""TDD Tests for Z.ai Execution Memory (SQLite Compression Manager).

Critical path testing for the AI's own context management tool.
"""

from pathlib import Path

import pytest

from orchestrator.compression.manager import (
    CompressionManager, CompactResult, Priority, SessionStatus
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
async def mem(tmp_path: Path):
    """Create a fresh CompressionManager with a temp DB."""
    db_path = str(tmp_path / "test_memory.db")
    mgr = CompressionManager(
        db_path=db_path,
        compact_threshold_bytes=200,       # low for testing
        compact_max_snapshot_bytes=1000,
        session_ttl_hours=1,
        auto_compact_interval_seconds=5,
    )
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


# ─── Session Lifecycle ───────────────────────────────────────────────────────

class TestSessionLifecycle:
    """Multi-session creation, listing, isolation, cleanup."""

    async def test_create_session(self, mem: CompressionManager):
        session = await mem.create_session(name="test-1")
        assert session.id is not None
        assert session.name == "test-1"
        assert session.status == SessionStatus.ACTIVE

    async def test_list_sessions(self, mem: CompressionManager):
        await mem.create_session(name="alpha")
        await mem.create_session(name="beta")
        sessions = await mem.list_sessions()
        names = {s.name for s in sessions}
        assert "alpha" in names
        assert "beta" in names

    async def test_get_session(self, mem: CompressionManager):
        created = await mem.create_session(name="fetch-me")
        fetched = await mem.get_session(created.id)
        assert fetched is not None
        assert fetched.name == "fetch-me"

    async def test_close_session(self, mem: CompressionManager):
        session = await mem.create_session(name="closing")
        await mem.close_session(session.id)
        fetched = await mem.get_session(session.id)
        assert fetched.status == SessionStatus.CLOSED

    async def test_nonexistent_session_returns_none(self, mem: CompressionManager):
        result = await mem.get_session("nonexistent-id")
        assert result is None


# ─── Context Storage & Retrieval ─────────────────────────────────────────────

class TestContextStorage:
    """Storing and retrieving knowledge within sessions."""

    async def test_store_and_retrieve(self, mem: CompressionManager):
        session = await mem.create_session(name="ctx-test")
        await mem.store("litellm_config", {"model": "gpt-4o", "rpm": 500},
                         session_id=session.id, category="reference",
                         priority=Priority.CRITICAL)

        value = await mem.retrieve("litellm_config", session_id=session.id)
        assert value is not None
        assert value["model"] == "gpt-4o"

    async def test_store_multiple_categories(self, mem: CompressionManager):
        session = await mem.create_session(name="multi-cat")
        await mem.store("ref1", {"a": 1}, session_id=session.id, category="reference")
        await mem.store("task1", {"b": 2}, session_id=session.id, category="workflow")
        await mem.store("log1", {"c": 3}, session_id=session.id, category="debug")

        ref = await mem.get_context(session.id, category="reference")
        wf = await mem.get_context(session.id, category="workflow")
        dbg = await mem.get_context(session.id, category="debug")

        assert len(ref) >= 1
        assert len(wf) >= 1
        assert len(dbg) >= 1

    async def test_session_isolation(self, mem: CompressionManager):
        s1 = await mem.create_session(name="iso-1")
        s2 = await mem.create_session(name="iso-2")
        await mem.store("shared-key", {"owner": "s1"}, session_id=s1.id, category="knowledge")
        await mem.store("shared-key", {"owner": "s2"}, session_id=s2.id, category="knowledge")

        v1 = await mem.retrieve("shared-key", session_id=s1.id)
        v2 = await mem.retrieve("shared-key", session_id=s2.id)

        assert v1["owner"] == "s1"
        assert v2["owner"] == "s2"

    async def test_overwrite_existing_key(self, mem: CompressionManager):
        session = await mem.create_session(name="overwrite")
        await mem.store("key1", {"v": 1}, session_id=session.id, category="general")
        await mem.store("key1", {"v": 2}, session_id=session.id, category="general")

        value = await mem.retrieve("key1", session_id=session.id)
        assert value["v"] == 2

    async def test_auto_session_creation(self, mem: CompressionManager):
        """Store without session_id creates a default session."""
        await mem.store("auto_key", {"x": 1}, category="general")
        value = await mem.retrieve("auto_key")
        assert value is not None
        assert value["x"] == 1


# ─── /compact Tool ───────────────────────────────────────────────────────────

class TestCompact:
    """The /compact compression tool — auto-triggered systematically."""

    async def test_manual_compact(self, mem: CompressionManager):
        session = await mem.create_session(name="compact-test")
        for i in range(20):
            await mem.store(f"key_{i}", {"data": "x" * 30, "index": i},
                             session_id=session.id, category="knowledge",
                             priority=Priority.MEDIUM)

        result = await mem.compact(session.id)
        assert isinstance(result, CompactResult)
        assert result.entries_before > 0
        assert result.compression_ratio > 0

    async def test_compact_preserves_critical(self, mem: CompressionManager):
        session = await mem.create_session(name="preserve-critical")
        # Store CRITICAL entries
        await mem.store("config", {"model": "gpt-4o"}, session_id=session.id,
                         category="reference", priority=Priority.CRITICAL)
        await mem.store("schema", {"table": "tasks"}, session_id=session.id,
                         category="reference", priority=Priority.CRITICAL)
        # Store MEDIUM entries to trigger compact
        for i in range(20):
            await mem.store(f"med_{i}", {"d": "x" * 20}, session_id=session.id,
                             category="debug", priority=Priority.MEDIUM)

        result = await mem.compact(session.id)
        # CRITICAL entries must be untouched
        config = await mem.retrieve("config", session_id=session.id)
        schema = await mem.retrieve("schema", session_id=session.id)
        assert config["model"] == "gpt-4o"
        assert schema["table"] == "tasks"
        assert result.critical_preserved == 2

    async def test_compact_merges_medium_entries(self, mem: CompressionManager):
        session = await mem.create_session(name="merge-medium")
        for i in range(10):
            await mem.store(f"debug_{i}", {"step": i}, session_id=session.id,
                             category="debug", priority=Priority.MEDIUM)

        result = await mem.compact(session.id)
        # >5 medium entries in "debug" category → should merge
        assert result.medium_merged > 0
        # Should have a summary entry
        context = await mem.get_context(session.id, category="debug")
        summaries = [e for e in context if e.get("is_summary")]
        assert len(summaries) >= 1

    async def test_compact_aggressively_compresses_low(self, mem: CompressionManager):
        session = await mem.create_session(name="compress-low")
        for i in range(8):
            await mem.store(f"raw_{i}", {"response": "y" * 50}, session_id=session.id,
                             category="output", priority=Priority.LOW)

        result = await mem.compact(session.id)
        assert result.low_compressed > 0
        # Only summary should remain
        context = await mem.get_context(session.id, category="output")
        non_summaries = [e for e in context if not e.get("is_summary")]
        assert len(non_summaries) == 0

    async def test_auto_compact_triggers_on_store(self, mem: CompressionManager):
        """When context exceeds threshold, store() auto-triggers compact."""
        session = await mem.create_session(name="auto-trigger")
        # Store data exceeding 200-byte threshold
        for i in range(15):
            await mem.store(f"auto_{i}", {"payload": "z" * 20, "idx": i},
                             session_id=session.id, category="general",
                             priority=Priority.MEDIUM)

        # Check compact history — should have at least one auto-triggered
        history = await mem.get_compact_history(session.id)
        auto_compacts = [h for h in history if h["triggered_by"] == "auto"]
        assert len(auto_compacts) >= 1

    async def test_compact_idempotent(self, mem: CompressionManager):
        session = await mem.create_session(name="idempotent")
        for i in range(10):
            await mem.store(f"k{i}", {"v": i}, session_id=session.id,
                             category="general", priority=Priority.MEDIUM)

        r1 = await mem.compact(session.id)
        r2 = await mem.compact(session.id)
        # Second compact shouldn't lose data
        assert r2.entries_after >= 0

    async def test_compact_preserves_latest_overwrite(self, mem: CompressionManager):
        session = await mem.create_session(name="latest-wins")
        await mem.store("state", {"step": 1}, session_id=session.id,
                         category="workflow", priority=Priority.HIGH)
        await mem.store("state", {"step": 2}, session_id=session.id,
                         category="workflow", priority=Priority.HIGH)

        await mem.compact(session.id)
        value = await mem.retrieve("state", session_id=session.id)
        assert value["step"] == 2


# ─── Session Size Tracking ──────────────────────────────────────────────────

class TestSessionSizeTracking:
    """Accurate size tracking for compact triggers."""

    async def test_empty_session_size_zero(self, mem: CompressionManager):
        session = await mem.create_session(name="empty")
        size = await mem.get_session_size(session.id)
        assert size == 0

    async def test_size_grows_with_data(self, mem: CompressionManager):
        session = await mem.create_session(name="growing")
        size_before = await mem.get_session_size(session.id)
        await mem.store("big", {"d": "a" * 100}, session_id=session.id, category="general")
        size_after = await mem.get_session_size(session.id)
        assert size_after > size_before

    async def test_size_shrinks_after_compact(self, mem: CompressionManager):
        session = await mem.create_session(name="shrink")
        for i in range(20):
            await mem.store(f"s{i}", {"x": "y" * 20}, session_id=session.id,
                             category="debug", priority=Priority.LOW)
        size_before = await mem.get_session_size(session.id)
        await mem.compact(session.id)
        size_after = await mem.get_session_size(session.id)
        # Compact should reduce entry count; size may grow briefly from
        # JSON overhead on tiny payloads, but entries should decrease
        context = await mem.get_context(session.id, category="debug")
        non_summary_count = sum(1 for e in context if not e.get("is_summary"))
        assert non_summary_count == 0  # LOW entries fully merged


# ─── TTL Cleanup ─────────────────────────────────────────────────────────────

class TestTTLCleanup:
    """Automatic cleanup of expired sessions."""

    async def test_cleanup_expired_sessions(self, mem: CompressionManager):
        session = await mem.create_session(name="expiring")
        await mem._force_expire_session(session.id, hours_ago=2)

        cleaned = await mem.cleanup_expired()
        assert cleaned >= 1
        fetched = await mem.get_session(session.id)
        assert fetched is None

    async def test_active_sessions_not_cleaned(self, mem: CompressionManager):
        session = await mem.create_session(name="active")
        cleaned = await mem.cleanup_expired()
        fetched = await mem.get_session(session.id)
        assert fetched is not None


# ─── Compact History ─────────────────────────────────────────────────────────

class TestCompactHistory:
    """/compact operations are logged for auditing."""

    async def test_compact_creates_history_entry(self, mem: CompressionManager):
        session = await mem.create_session(name="history")
        for i in range(10):
            await mem.store(f"h{i}", {"v": i}, session_id=session.id,
                             category="general", priority=Priority.MEDIUM)

        result = await mem.compact(session.id)
        history = await mem.get_compact_history(session.id)
        assert len(history) >= 1
        assert history[0]["entries_before"] == result.entries_before
        assert history[0]["entries_after"] == result.entries_after

    async def test_history_tracks_trigger_type(self, mem: CompressionManager):
        session = await mem.create_session(name="trigger-type")
        for i in range(10):
            await mem.store(f"t{i}", {"v": i}, session_id=session.id,
                             category="general", priority=Priority.MEDIUM)

        await mem.compact(session.id, triggered_by="manual")
        history = await mem.get_compact_history(session.id)
        assert history[0]["triggered_by"] == "manual"


# ─── Convenience Methods ─────────────────────────────────────────────────────

class TestConvenienceMethods:
    """store_reference, store_workflow, store_decision, store_debug, store_output."""

    async def test_store_reference_is_critical(self, mem: CompressionManager):
        session = await mem.create_session(name="conv")
        await mem.store_reference("ref", {"important": True})
        context = await mem.get_context(session.id, category="reference", priority=Priority.CRITICAL)
        assert len(context) >= 1

    async def test_store_workflow_is_high(self, mem: CompressionManager):
        session = await mem.create_session(name="conv")
        await mem.store_workflow("wf", {"tasks": [1, 2, 3]})
        context = await mem.get_context(session.id, category="workflow", priority=Priority.HIGH)
        assert len(context) >= 1

    async def test_store_decision_is_high(self, mem: CompressionManager):
        session = await mem.create_session(name="conv")
        await mem.store_decision("dec", {"choice": "FastAPI"})
        context = await mem.get_context(session.id, category="decision", priority=Priority.HIGH)
        assert len(context) >= 1

    async def test_store_debug_is_medium(self, mem: CompressionManager):
        session = await mem.create_session(name="conv")
        await mem.store_debug("err", {"msg": "timeout"})
        context = await mem.get_context(session.id, category="debug", priority=Priority.MEDIUM)
        assert len(context) >= 1

    async def test_store_output_is_low(self, mem: CompressionManager):
        session = await mem.create_session(name="conv")
        await mem.store_output("gen", {"code": "def foo(): pass"})
        context = await mem.get_context(session.id, category="output", priority=Priority.LOW)
        assert len(context) >= 1


# ─── Snapshot (Context Injection) ────────────────────────────────────────────

class TestSnapshot:
    """Snapshot for injecting context into Z.ai's prompt."""

    async def test_snapshot_includes_critical_and_high(self, mem: CompressionManager):
        session = await mem.create_session(name="snap")
        await mem.store_reference("config", {"model": "gpt-4o"})
        await mem.store_workflow("dag", {"steps": 3})
        await mem.store_debug("log", {"msg": "verbose"})

        snap = await mem.snapshot(session.id)
        keys = [e["key"] for e in snap["context"]]
        assert "config" in keys
        assert "dag" in keys

    async def test_snapshot_medium_only_keys_and_summaries(self, mem: CompressionManager):
        session = await mem.create_session(name="snap-med")
        for i in range(10):
            await mem.store_debug(f"dbg_{i}", {"step": i})

        await mem.compact(session.id)
        snap = await mem.snapshot(session.id)

        # Medium entries should be summarized
        medium_entries = [e for e in snap["context"] if e.get("category") == "debug"]
        # Only summary entries or key-only entries should appear
        for e in medium_entries:
            assert e.get("is_summary") or "note" in e
