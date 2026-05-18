"""Circuit Breaker pattern for PlatformAdapters.

Prevents cascading failures by wrapping an adapter with state-based
call gating:

- CLOSED: Normal operation — all calls pass through to the adapter.
- OPEN: Failing — all calls are rejected immediately with CircuitOpenError.
- HALF_OPEN: Testing recovery — one test call is allowed. If it succeeds,
  the circuit transitions to CLOSED. If it fails, it reverts to OPEN.

Transitions:
- CLOSED → OPEN:   When consecutive failures reach `failure_threshold`.
- OPEN   → HALF_OPEN: After `recovery_timeout` seconds have elapsed.
- HALF_OPEN → CLOSED:  On a successful call.
- HALF_OPEN → OPEN:    On a failed call.
"""

import time
from enum import Enum
from typing import Optional

from orchestrator.adapters.base import PlatformAdapter, AdapterResult


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is OPEN."""
    pass


class CircuitBreaker:
    """Wraps a PlatformAdapter with circuit-breaker semantics.

    Args:
        adapter: The underlying platform adapter to protect.
        failure_threshold: Number of consecutive failures before opening.
        recovery_timeout: Seconds to wait in OPEN before transitioning
            to HALF_OPEN.
    """

    def __init__(
        self,
        adapter: PlatformAdapter,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self._adapter = adapter
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: Optional[float] = None

    # ── Public Properties ──────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current circuit state, lazily transitioning OPEN → HALF_OPEN."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def last_failure_time(self) -> Optional[float]:
        return self._last_failure_time

    # ── Adapter Method Proxies ─────────────────────────────────────────

    async def submit(self, task_id: str, action_type: str, input_data: dict) -> str:
        return await self._call("submit", task_id, action_type, input_data)

    async def get_status(self, platform_job_id: str) -> str:
        return await self._call("get_status", platform_job_id)

    async def get_result(self, platform_job_id: str) -> AdapterResult:
        return await self._call("get_result", platform_job_id)

    async def cancel(self, platform_job_id: str) -> bool:
        return await self._call("cancel", platform_job_id)

    # ── Internal Dispatch ──────────────────────────────────────────────

    async def _call(self, method: str, *args):
        """Execute a method call through the circuit breaker logic."""
        current_state = self.state  # triggers OPEN→HALF_OPEN transition

        if current_state == CircuitState.OPEN:
            raise CircuitOpenError(
                f"Circuit is OPEN — rejecting call to {method}"
            )

        try:
            fn = getattr(self._adapter, method)
            result = await fn(*args)
        except CircuitOpenError:
            raise  # never catch our own error
        except Exception as exc:
            self._on_failure(exc)
        else:
            self._on_success()
            return result

    # ── State Transitions ──────────────────────────────────────────────

    def _on_success(self) -> None:
        """Handle a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            # Recovery confirmed — close the circuit
            self._state = CircuitState.CLOSED
        self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        """Handle a failed call — may transition to OPEN."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Recovery test failed — back to OPEN
            self._state = CircuitState.OPEN
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN

        raise exc
