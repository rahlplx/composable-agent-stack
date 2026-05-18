"""Mock Platform Adapters — configurable behavior for testing.

Instead of hitting real services, tests use these mocks with:
- Configurable latency (simulate slow responses)
- Configurable failure rate (simulate intermittent failures)
- Full state tracking (how many calls, what args)
- Delay injection (timeout testing)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional
from dataclasses import dataclass, field

from orchestrator.adapters.base import PlatformAdapter, AdapterResult


@dataclass
class MockCall:
    """Record of a single call to the mock adapter."""
    method: str
    task_id: str = ""
    action_type: str = ""
    input_data: dict = field(default_factory=dict)
    timestamp: str = ""


class MockPlatformAdapter(PlatformAdapter):
    """Controllable mock adapter for testing.

    Features:
    - call_log: every method call recorded
    - fail_after: fail after N successful calls
    - latency_ms: simulate processing time
    - next_result: configurable result for next call
    - next_status: configurable status for next call
    """

    def __init__(
        self,
        endpoint: str = "http://mock:9999",
        litellm_base_url: str = "http://mock-litellm:4000",
        litellm_api_key: str = "mock-key",
    ):
        super().__init__(endpoint, litellm_base_url, litellm_api_key)
        self.call_log: list[MockCall] = []
        self._jobs: dict[str, dict] = {}
        self._call_count: int = 0
        self._fail_after: int | None = None          # fail after N calls
        self._latency_ms: int = 0                      # simulated latency
        self._next_result: Any = None
        self._next_error: str = ""
        self._auto_complete: bool = True               # auto-complete on submit

    def configure(
        self,
        fail_after: int | None = None,
        latency_ms: int = 0,
        next_result: Any = None,
        next_error: str = "",
        auto_complete: bool = True,
    ) -> None:
        """Configure mock behavior for upcoming calls."""
        self._fail_after = fail_after
        self._latency_ms = latency_ms
        self._next_result = next_result
        self._next_error = next_error
        self._auto_complete = auto_complete

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def submit_count(self) -> int:
        return sum(1 for c in self.call_log if c.method == "submit")

    def _record(self, method: str, **kwargs) -> None:
        self.call_log.append(MockCall(method=method, **kwargs))

    async def _maybe_delay(self) -> None:
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

    def _should_fail(self) -> bool:
        self._call_count += 1
        if self._fail_after is not None and self._call_count > self._fail_after:
            return True
        return False

    async def submit(self, task_id: str, action_type: str, input_data: dict) -> str:
        self._record("submit", task_id=task_id, action_type=action_type,
                     input_data=input_data)
        await self._maybe_delay()

        job_id = str(uuid.uuid4())

        if self._should_fail():
            self._jobs[job_id] = {
                "status": "failed",
                "task_id": task_id,
                "error": self._next_error or "mock failure",
            }
            return job_id

        if self._auto_complete:
            self._jobs[job_id] = {
                "status": "completed",
                "task_id": task_id,
                "result": self._next_result or {"mock": True, "action_type": action_type},
            }
        else:
            self._jobs[job_id] = {
                "status": "running",
                "task_id": task_id,
            }

        return job_id

    async def get_status(self, platform_job_id: str) -> str:
        self._record("get_status", task_id=platform_job_id)
        job = self._jobs.get(platform_job_id, {})
        return job.get("status", "unknown")

    async def get_result(self, platform_job_id: str) -> AdapterResult:
        self._record("get_result", task_id=platform_job_id)
        job = self._jobs.get(platform_job_id, {})
        return AdapterResult(
            task_id=job.get("task_id", ""),
            status=job.get("status", "failed"),
            output=job.get("result"),
            error=job.get("error", ""),
        )

    async def cancel(self, platform_job_id: str) -> bool:
        self._record("cancel", task_id=platform_job_id)
        if platform_job_id in self._jobs:
            self._jobs[platform_job_id]["status"] = "cancelled"
            return True
        return False

    async def complete_job(self, platform_job_id: str, result: Any = None) -> None:
        """Test helper: mark a running job as completed."""
        if platform_job_id in self._jobs:
            self._jobs[platform_job_id]["status"] = "completed"
            self._jobs[platform_job_id]["result"] = result or {"mock_complete": True}

    async def fail_job(self, platform_job_id: str, error: str = "mock error") -> None:
        """Test helper: mark a running job as failed."""
        if platform_job_id in self._jobs:
            self._jobs[platform_job_id]["status"] = "failed"
            self._jobs[platform_job_id]["error"] = error

    async def health_check(self) -> bool:
        return True  # mock is always healthy


class FlakyAdapter(MockPlatformAdapter):
    """Adapter that fails every Nth call — simulates real network flakiness."""

    def __init__(self, fail_every: int = 3, **kwargs):
        super().__init__(**kwargs)
        self._fail_every = fail_every

    async def submit(self, task_id: str, action_type: str, input_data: dict) -> str:
        self._call_count += 1
        if self._call_count % self._fail_every == 0:
            job_id = str(uuid.uuid4())
            self._jobs[job_id] = {
                "status": "failed",
                "task_id": task_id,
                "error": f"flaky failure (call #{self._call_count})",
            }
            self._record("submit", task_id=task_id, action_type=action_type,
                         input_data=input_data)
            return job_id
        return await super().submit(task_id, action_type, input_data)


class SlowAdapter(MockPlatformAdapter):
    """Adapter with configurable response latency."""

    def __init__(self, latency_ms: int = 500, **kwargs):
        super().__init__(**kwargs)
        self._latency_ms = latency_ms
