"""TDD Tests for RL Test Optimizer.

Verifies that the RL engine learns correctly:
- Priority scoring
- Flaky test detection
- CI budget selection
- Coverage gap identification
"""

import pytest

from tests.rl.optimizer import RLTestOptimizer


@pytest.fixture
async def optimizer(tmp_path):
    db_path = str(tmp_path / "test_rl.db")
    opt = RLTestOptimizer(db_path=db_path)
    await opt.initialize()
    yield opt
    await opt.shutdown()


class TestRLRecording:
    """Test recording and tracking of test execution history."""

    @pytest.mark.asyncio
    async def test_record_single_result(self, optimizer: RLTestOptimizer):
        await optimizer.record("test_example", passed=True, duration_ms=50)
        scores = await optimizer.get_scores()
        assert len(scores) == 1
        assert scores[0].test_name == "test_example"
        assert scores[0].total_runs == 1
        assert scores[0].total_passes == 1

    @pytest.mark.asyncio
    async def test_record_batch(self, optimizer: RLTestOptimizer):
        await optimizer.record_batch([
            {"test_name": "test_a", "passed": True, "duration_ms": 10},
            {"test_name": "test_b", "passed": False, "duration_ms": 200},
            {"test_name": "test_c", "passed": True, "duration_ms": 50},
        ])
        scores = await optimizer.get_scores()
        assert len(scores) == 3

    @pytest.mark.asyncio
    async def test_duration_averaging(self, optimizer: RLTestOptimizer):
        await optimizer.record("test_avg", passed=True, duration_ms=100)
        await optimizer.record("test_avg", passed=True, duration_ms=200)
        scores = await optimizer.get_scores()
        avg = scores[0].avg_duration_ms
        assert 100 <= avg <= 200  # should be between 100 and 200


class TestRLPriority:
    """Test priority score calculation."""

    @pytest.mark.asyncio
    async def test_fast_stable_test_gets_high_priority(self, optimizer: RLTestOptimizer):
        # Fast + always passing + stable = high base priority
        for _ in range(10):
            await optimizer.record("test_fast_stable", passed=True, duration_ms=20)

        scores = await optimizer.get_scores()
        s = next(s for s in scores if s.test_name == "test_fast_stable")
        assert s.priority_score > 0.5

    @pytest.mark.asyncio
    async def test_slow_test_gets_penalty(self, optimizer: RLTestOptimizer):
        for _ in range(10):
            await optimizer.record("test_slow", passed=True, duration_ms=5000)

        scores = await optimizer.get_scores()
        s = next(s for s in scores if s.test_name == "test_slow")
        # Slow + always passing = lower priority
        assert s.priority_score < 0.7

    @pytest.mark.asyncio
    async def test_regression_catcher_gets_bonus(self, optimizer: RLTestOptimizer):
        # First passes, then fails = regression catch
        for _ in range(5):
            await optimizer.record("test_catcher", passed=True, duration_ms=50)
        await optimizer.record("test_catcher", passed=False, duration_ms=50)

        scores = await optimizer.get_scores()
        s = next(s for s in scores if s.test_name == "test_catcher")
        assert s.regression_catch_count >= 1


class TestRLPrioritization:
    """Test adaptive test prioritization."""

    @pytest.mark.asyncio
    async def test_prioritize_returns_all_tests(self, optimizer: RLTestOptimizer):
        tests = ["test_a", "test_b", "test_c"]
        await optimizer.record("test_a", True, 50)
        await optimizer.record("test_b", True, 200)
        await optimizer.record("test_c", True, 10)

        prioritized = await optimizer.prioritize(tests)
        assert set(prioritized) == set(tests)

    @pytest.mark.asyncio
    async def test_higher_priority_tests_first(self, optimizer: RLTestOptimizer):
        # Make test_fast high priority, test_slow low
        for _ in range(10):
            await optimizer.record("test_fast", True, 10)
        for _ in range(10):
            await optimizer.record("test_slow", True, 3000)

        prioritized = await optimizer.prioritize(["test_slow", "test_fast"])
        assert prioritized[0] == "test_fast"

    @pytest.mark.asyncio
    async def test_ci_budget_selection(self, optimizer: RLTestOptimizer):
        # Fast test fits in budget, slow one doesn't
        for _ in range(5):
            await optimizer.record("test_quick", True, 50)
            await optimizer.record("test_sluggish", True, 30000)

        selected = await optimizer.select_for_ci(
            ["test_quick", "test_sluggish"],
            budget_seconds=1.0,
        )
        assert "test_quick" in selected


class TestFlakyDetection:
    """Test flaky test identification."""

    @pytest.mark.asyncio
    async def test_stable_test_not_flagged(self, optimizer: RLTestOptimizer):
        for _ in range(10):
            await optimizer.record("test_stable", True, 50)

        report = await optimizer.flaky_report()
        names = [r["test_name"] for r in report]
        assert "test_stable" not in names

    @pytest.mark.asyncio
    async def test_flaky_test_flagged(self, optimizer: RLTestOptimizer):
        # Alternate pass/fail
        for i in range(10):
            await optimizer.record("test_flaky", passed=(i % 2 == 0), duration_ms=50)

        report = await optimizer.flaky_report()
        names = [r["test_name"] for r in report]
        assert "test_flaky" in names


class TestCoverageGaps:
    """Test coverage gap detection."""

    @pytest.mark.asyncio
    async def test_unscored_tests_identified(self, optimizer: RLTestOptimizer):
        await optimizer.record("test_recorded", True, 50)

        report = await optimizer.coverage_gap_report(["test_recorded", "test_unknown"])
        assert "test_unknown" in report["unscored"]
        assert "test_recorded" not in report["unscored"]

    @pytest.mark.asyncio
    async def test_coverage_percentage(self, optimizer: RLTestOptimizer):
        await optimizer.record("test_a", True, 50)
        await optimizer.record("test_b", True, 50)

        report = await optimizer.coverage_gap_report(["test_a", "test_b", "test_c"])
        assert report["coverage_pct"] == 66.7


class TestRLSummary:
    """Test suite health summary."""

    @pytest.mark.asyncio
    async def test_summary_returns_dict(self, optimizer: RLTestOptimizer):
        for i in range(5):
            await optimizer.record(f"test_{i}", True, 50)

        summary = await optimizer.summary()
        assert "total_tracked" in summary
        assert "avg_priority" in summary
        assert "flaky_count" in summary
        assert summary["total_tracked"] == 5
