"""OpenHands Platform Adapter.

Wraps OpenHands code agent (headless CLI mode)
connected via LiteLLM alias `openhands-smart`.
"""

import uuid

import httpx

from .base import PlatformAdapter, AdapterResult


class OpenHandsAdapter(PlatformAdapter):
    """Adapter for OpenHands code generation/execution.

    OpenHands handles: writing code, running tests, debugging,
    CI/CD headless workflows, file manipulation in sandbox.

    LiteLLM routing: openhands-smart (GPT-4o, 100 RPM, 5 parallel)
    """

    def __init__(self, endpoint: str = "http://localhost:8003",
                 litellm_base_url: str = "http://localhost:4000",
                 litellm_api_key: str = ""):
        super().__init__(endpoint, litellm_base_url, litellm_api_key)
        self._jobs: dict[str, dict] = {}

    async def submit(self, task_id: str, action_type: str, input_data: dict) -> str:
        """Submit a code task to OpenHands."""
        platform_job_id = str(uuid.uuid4())
        payload = {
            "task_id": task_id,
            "job_id": platform_job_id,
            "action_type": action_type,
            "input": input_data,
            "llm_config": {
                "base_url": f"{self.litellm_base_url}/v1",
                "api_key": self.litellm_api_key,
                "model": "openhands-smart",
            },
            "max_iterations": input_data.get("max_iterations", 50),
            "sandbox_timeout": input_data.get("sandbox_timeout", 120),
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
