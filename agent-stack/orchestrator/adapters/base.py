"""Base platform adapter interface.

All platform adapters (Agent-S, Browser Use, OpenHands) implement
this interface so the orchestrator can dispatch tasks uniformly.
"""

import abc
from dataclasses import dataclass
from typing import Any


@dataclass
class AdapterResult:
    """Result from a platform adapter execution."""
    task_id: str = ""
    status: str = "completed"    # completed | failed
    output: Any = None
    error: str = ""
    latency_ms: float = 0.0
    tokens_used: int = 0

    @property
    def success(self) -> bool:
        return self.status == "completed"


class PlatformAdapter(abc.ABC):
    """Abstract base class for platform adapters.

    Each adapter wraps one agent platform and translates the
    orchestrator's task format into platform-specific API calls.
    """

    def __init__(self, endpoint: str, litellm_base_url: str = "http://localhost:4000",
                 litellm_api_key: str = ""):
        self.endpoint = endpoint
        self.litellm_base_url = litellm_base_url
        self.litellm_api_key = litellm_api_key

    @abc.abstractmethod
    async def submit(self, task_id: str, action_type: str, input_data: dict) -> str:
        """Submit a task to the platform. Returns a platform-side job ID."""
        ...

    @abc.abstractmethod
    async def get_status(self, platform_job_id: str) -> str:
        """Get the current status of a platform job."""
        ...

    @abc.abstractmethod
    async def get_result(self, platform_job_id: str) -> AdapterResult:
        """Get the result of a completed platform job."""
        ...

    @abc.abstractmethod
    async def cancel(self, platform_job_id: str) -> bool:
        """Cancel a running platform job."""
        ...

    async def health_check(self) -> bool:
        """Check if the platform is reachable."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.endpoint}/health")
                return resp.status_code == 200
        except Exception:
            return False
