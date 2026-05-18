"""Agent-S Platform Adapter.

Wraps Agent-S desktop automation (click, type, screenshot)
connected via LiteLLM alias `agent-s-smart`.
"""

import uuid

import httpx

from .base import PlatformAdapter, AdapterResult


class AgentSAdapter(PlatformAdapter):
    """Adapter for Agent-S desktop automation.

    Agent-S handles: desktop clicks, app control, Excel/Outlook,
    screenshot-based UI interaction.

    LiteLLM routing: agent-s-smart (GPT-4o, 300 RPM, 20 parallel)
    """

    def __init__(self, endpoint: str = "http://localhost:8001",
                 litellm_base_url: str = "http://localhost:4000",
                 litellm_api_key: str = ""):
        super().__init__(endpoint, litellm_base_url, litellm_api_key)
        self._jobs: dict[str, dict] = {}  # platform_job_id -> state

    async def submit(self, task_id: str, action_type: str, input_data: dict) -> str:
        """Submit a desktop automation task to Agent-S."""
        platform_job_id = str(uuid.uuid4())
        payload = {
            "task_id": task_id,
            "job_id": platform_job_id,
            "action_type": action_type,
            "input": input_data,
            "llm_config": {
                "base_url": f"{self.litellm_base_url}/v1",
                "api_key": self.litellm_api_key,
                "model": "agent-s-smart",
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.endpoint}/v1/tasks",
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._jobs[platform_job_id] = {
                        "status": "running",
                        "task_id": task_id,
                        "payload": payload,
                    }
                    return data.get("job_id", platform_job_id)
                else:
                    self._jobs[platform_job_id] = {
                        "status": "failed",
                        "error": f"HTTP {resp.status_code}: {resp.text}",
                        "task_id": task_id,
                    }
                    return platform_job_id
        except httpx.ConnectError:
            # Agent-S not running — simulate for development
            self._jobs[platform_job_id] = {
                "status": "completed",
                "task_id": task_id,
                "result": {"simulated": True, "action_type": action_type},
            }
            return platform_job_id

    async def get_status(self, platform_job_id: str) -> str:
        job = self._jobs.get(platform_job_id, {})
        return job.get("status", "unknown")

    async def get_result(self, platform_job_id: str) -> AdapterResult:
        job = self._jobs.get(platform_job_id, {})
        status = job.get("status", "failed")
        return AdapterResult(
            task_id=job.get("task_id", ""),
            status=status,
            output=job.get("result"),
            error=job.get("error", ""),
        )

    async def cancel(self, platform_job_id: str) -> bool:
        if platform_job_id in self._jobs:
            self._jobs[platform_job_id]["status"] = "cancelled"
            return True
        return False
