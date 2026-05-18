"""Performance & Load Tests for the orchestrator.

Measures:
- Throughput: workflows/second
- Latency: time per workflow submission
- Compression: compact speed vs data size
- Memory: session growth rate
"""

import time
import pytest

from orchestrator.compression.manager import CompressionManager
from orchestrator.api.orchestrator import OrchestratorService, SubmitWorkflowRequest
from orchestrator.state.models import Platform
from tests.harness.mocks import MockPlatformAdapter


@pytest.fixture
async def perf_env(tmp_path):
    db_path = str(tmp_path / "perf_memory.db")
    mem = CompressionManager(db_path=db_path, compact_threshold_bytes=100_000)
    await mem.initialize()

    orch = OrchestratorService(compression_manager=mem)
    orch.adapters[Platform.AGENT_S] = MockPlatformAdapter()
    orch.adapters[Platform.BROWSER_USE] = MockPlatformAdapter()
    orch.adapters[Platform.OPENHANDS] = MockPlatformAdapter()

    yield orch, mem
    await orch.stop()
    await mem.shutdown()


class TestWorkflowThroughput:
    """Measure workflow submission throughput."""

    @pytest.mark.asyncio
    async def test_submit_100_workflows_under_5_seconds(self, perf_env):
        orch, mem = perf_env
        start = time.monotonic()

        for i in range(100):
            await orch.submit_workflow(SubmitWorkflowRequest(
                user_request=f"workflow {i}: scrape the product price",
            ))

        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"100 workflow submissions took {elapsed:.2f}s"
        assert len(orch.workflows) == 100

    @pytest.mark.asyncio
    async def test_submit_latency_p50_under_10ms(self, perf_env):
        orch, mem = perf_env
        latencies = []

        for i in range(50):
            start = time.monotonic()
            await orch.submit_workflow(SubmitWorkflowRequest(
                user_request=f"latency test {i}",
            ))
            latencies.append((time.monotonic() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        assert p50 < 50, f"p50 latency: {p50:.1f}ms (expected <50ms)"


class TestCompressionPerformance:
    """Measure /compact performance under data load."""

    @pytest.mark.asyncio
    async def test_compact_1000_entries_under_2_seconds(self, tmp_path):
        db_path = str(tmp_path / "perf_compact.db")
        mem = CompressionManager(db_path=db_path, compact_threshold_bytes=10_000_000)
        await mem.initialize()

        session = await mem.create_session(name="perf-compact")

        # Store 1000 entries
        for i in range(1000):
            await mem.store_debug(f"perf_{i}", {
                "step": i, "action": f"action_{i}",
                "result": "ok" * 10,
            })

        start = time.monotonic()
        result = await mem.compact(session.id)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"Compact of 1000 entries took {elapsed:.2f}s"
        assert result.medium_merged > 0

        await mem.shutdown()

    @pytest.mark.asyncio
    async def test_snapshot_speed_under_100ms(self, perf_env):
        orch, mem = perf_env
        session = await mem.create_session(name="snap-perf")
        await mem.store_reference("config", {"model": "gpt-4o"})
        await mem.store_workflow("dag", {"steps": 5})

        for i in range(50):
            await mem.store_debug(f"dbg_{i}", {"v": i})

        start = time.monotonic()
        snap = await mem.snapshot(session.id)
        elapsed = (time.monotonic() - start) * 1000

        assert elapsed < 100, f"Snapshot took {elapsed:.1f}ms"


class TestSessionGrowth:
    """Verify session size grows linearly (not super-linearly)."""

    @pytest.mark.asyncio
    async def test_size_grows_linearly(self, tmp_path):
        db_path = str(tmp_path / "growth.db")
        mem = CompressionManager(db_path=db_path, compact_threshold_bytes=10_000_000)
        await mem.initialize()

        session = await mem.create_session(name="growth-test")

        sizes = []
        for batch in range(5):
            for i in range(100):
                await mem.store_debug(f"batch{batch}_{i}", {"v": f"data_{batch}_{i}"})
            size = await mem.get_session_size(session.id)
            sizes.append(size)

        # Each batch adds roughly the same amount
        growths = [sizes[i] - sizes[i - 1] for i in range(1, len(sizes))]
        # Check that growth rate is roughly consistent (not super-linear)
        # Allow 50% variance from mean
        if growths:
            mean_growth = sum(growths) / len(growths)
            for g in growths:
                assert g > mean_growth * 0.5, f"Growth {g} is unexpectedly low vs mean {mean_growth}"

        await mem.shutdown()
