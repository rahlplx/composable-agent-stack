"""TDD tests for the Circuit Breaker pattern.

Covers all state transitions and adapter method wrapping.
"""

import time
from unittest.mock import AsyncMock

import pytest

from orchestrator.adapters.base import AdapterResult, PlatformAdapter
from orchestrator.adapters.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

class _FailingAdapter(PlatformAdapter):
    """Adapter whose every method raises RuntimeError."""

    def __init__(self):
        super().__init__(endpoint="http://fail:9999")

    async def submit(self, task_id, action_type, input_data):
        raise RuntimeError("submit failed")

    async def get_status(self, platform_job_id):
        raise RuntimeError("get_status failed")

    async def get_result(self, platform_job_id):
        raise RuntimeError("get_result failed")

    async def cancel(self, platform_job_id):
        raise RuntimeError("cancel failed")


class _SucceedingAdapter(PlatformAdapter):
    """Adapter whose every method succeeds."""

    def __init__(self):
        super().__init__(endpoint="http://ok:9999")

    async def submit(self, task_id, action_type, input_data):
        return "job-123"

    async def get_status(self, platform_job_id):
        return "completed"

    async def get_result(self, platform_job_id):
        return AdapterResult(task_id="t1", status="completed", output={"ok": True})

    async def cancel(self, platform_job_id):
        return True


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestCircuitStartsClosed:
    def test_initial_state(self):
        cb = CircuitBreaker(_SucceedingAdapter())
        assert cb.state == CircuitState.CLOSED

    def test_initial_failure_count(self):
        cb = CircuitBreaker(_SucceedingAdapter())
        assert cb.failure_count == 0


class TestCircuitOpensAfterThreshold:
    @pytest.mark.asyncio
    async def test_opens_after_consecutive_failures(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=3)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.submit("t1", "act", {})
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_does_not_open_before_threshold(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=5)
        for _ in range(4):
            with pytest.raises(RuntimeError):
                await cb.submit("t1", "act", {})
        assert cb.state == CircuitState.CLOSED


class TestCircuitRejectsWhenOpen:
    @pytest.mark.asyncio
    async def test_submit_raises_circuit_open(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=1)
        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        # Now OPEN
        with pytest.raises(CircuitOpenError):
            await cb.submit("t2", "act", {})

    @pytest.mark.asyncio
    async def test_get_status_raises_circuit_open(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=1)
        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        with pytest.raises(CircuitOpenError):
            await cb.get_status("job-1")

    @pytest.mark.asyncio
    async def test_cancel_raises_circuit_open(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=1)
        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        with pytest.raises(CircuitOpenError):
            await cb.cancel("job-1")


class TestCircuitHalfOpenAfterTimeout:
    @pytest.mark.asyncio
    async def test_transitions_to_half_open(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=1, recovery_timeout=0.05)
        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN


class TestCircuitClosesOnSuccessInHalfOpen:
    @pytest.mark.asyncio
    async def test_success_closes_circuit(self):
        adapter = _SucceedingAdapter()
        cb = CircuitBreaker(adapter, failure_threshold=1, recovery_timeout=0.05)

        # Force OPEN by failing — use a mock that fails once then succeeds
        failing_then_ok = AsyncMock(spec=PlatformAdapter)
        failing_then_ok.submit = AsyncMock(side_effect=[RuntimeError("boom"), "job-ok"])
        failing_then_ok.get_status = AsyncMock(return_value="completed")
        failing_then_ok.get_result = AsyncMock(return_value=AdapterResult(status="completed"))
        failing_then_ok.cancel = AsyncMock(return_value=True)

        cb2 = CircuitBreaker(failing_then_ok, failure_threshold=1, recovery_timeout=0.05)

        with pytest.raises(RuntimeError):
            await cb2.submit("t1", "act", {})
        assert cb2.state == CircuitState.OPEN

        time.sleep(0.06)
        assert cb2.state == CircuitState.HALF_OPEN

        result = await cb2.submit("t2", "act", {})
        assert result == "job-ok"
        assert cb2.state == CircuitState.CLOSED
        assert cb2.failure_count == 0


class TestCircuitReopensOnFailureInHalfOpen:
    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens(self):
        failing = AsyncMock(spec=PlatformAdapter)
        failing.submit = AsyncMock(side_effect=RuntimeError("still broken"))
        failing.get_status = AsyncMock(side_effect=RuntimeError("still broken"))
        failing.get_result = AsyncMock(side_effect=RuntimeError("still broken"))
        failing.cancel = AsyncMock(side_effect=RuntimeError("still broken"))

        cb = CircuitBreaker(failing, failure_threshold=1, recovery_timeout=0.05)

        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        assert cb.state == CircuitState.OPEN

        time.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN

        with pytest.raises(RuntimeError):
            await cb.submit("t2", "act", {})
        assert cb.state == CircuitState.OPEN


class TestCircuitTracksFailureCount:
    @pytest.mark.asyncio
    async def test_failure_count_increments(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=5)
        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        assert cb.failure_count == 1

        with pytest.raises(RuntimeError):
            await cb.submit("t2", "act", {})
        assert cb.failure_count == 2

    @pytest.mark.asyncio
    async def test_failure_count_resets_on_success(self):
        adapter = AsyncMock(spec=PlatformAdapter)
        adapter.submit = AsyncMock(side_effect=[
            RuntimeError("err1"), RuntimeError("err2"), "job-ok"
        ])
        adapter.get_status = AsyncMock(return_value="completed")
        adapter.get_result = AsyncMock(return_value=AdapterResult(status="completed"))
        adapter.cancel = AsyncMock(return_value=True)

        cb = CircuitBreaker(adapter, failure_threshold=5)

        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})
        with pytest.raises(RuntimeError):
            await cb.submit("t2", "act", {})
        assert cb.failure_count == 2

        await cb.submit("t3", "act", {})
        assert cb.failure_count == 0


class TestCircuitWrapsAllAdapterMethods:
    @pytest.mark.asyncio
    async def test_submit_passes_through(self):
        adapter = _SucceedingAdapter()
        cb = CircuitBreaker(adapter)
        result = await cb.submit("t1", "act", {"key": "val"})
        assert result == "job-123"

    @pytest.mark.asyncio
    async def test_get_status_passes_through(self):
        adapter = _SucceedingAdapter()
        cb = CircuitBreaker(adapter)
        result = await cb.get_status("job-1")
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_get_result_passes_through(self):
        adapter = _SucceedingAdapter()
        cb = CircuitBreaker(adapter)
        result = await cb.get_result("job-1")
        assert isinstance(result, AdapterResult)
        assert result.success

    @pytest.mark.asyncio
    async def test_cancel_passes_through(self):
        adapter = _SucceedingAdapter()
        cb = CircuitBreaker(adapter)
        result = await cb.cancel("job-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_all_methods_rejected_when_open(self):
        cb = CircuitBreaker(_FailingAdapter(), failure_threshold=1)

        # Open the circuit
        with pytest.raises(RuntimeError):
            await cb.submit("t1", "act", {})

        # submit takes (task_id, action_type, input_data)
        with pytest.raises(CircuitOpenError):
            await cb.submit("t2", "act", {})

        # get_status / get_result / cancel take (platform_job_id)
        for method_name in ("get_status", "get_result", "cancel"):
            fn = getattr(cb, method_name)
            with pytest.raises(CircuitOpenError):
                await fn("some-id")
