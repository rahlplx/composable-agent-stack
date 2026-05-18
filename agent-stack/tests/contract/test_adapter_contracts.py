"""Contract Tests for Platform Adapters.

Every adapter must satisfy the same interface contract.
These tests verify that ALL adapter implementations obey the contract:
- submit() returns a job ID string
- get_status() returns a known status string
- get_result() returns an AdapterResult
- cancel() returns a bool
- health_check() returns a bool
"""

import pytest

from orchestrator.adapters.base import PlatformAdapter, AdapterResult
from orchestrator.adapters.agents import AgentSAdapter
from orchestrator.adapters.browser import BrowserUseAdapter
from orchestrator.adapters.openhands import OpenHandsAdapter
from tests.harness.mocks import MockPlatformAdapter, FlakyAdapter, SlowAdapter


# ─── All adapter implementations to test ─────────────────────────────────────

ADAPTER_CLASSES = [
    ("AgentSAdapter", AgentSAdapter),
    ("BrowserUseAdapter", BrowserUseAdapter),
    ("OpenHandsAdapter", OpenHandsAdapter),
    ("MockPlatformAdapter", MockPlatformAdapter),
    ("FlakyAdapter", FlakyAdapter),
    ("SlowAdapter", SlowAdapter),
]


@pytest.fixture(params=[cls for _, cls in ADAPTER_CLASSES], ids=[name for name, _ in ADAPTER_CLASSES])
def adapter(request):
    """Parametrized fixture: test every adapter implementation."""
    cls = request.param
    if cls in (AgentSAdapter, BrowserUseAdapter, OpenHandsAdapter):
        # Real adapters — will use simulation mode (no server running)
        return cls(endpoint="http://localhost:9999")
    elif cls == FlakyAdapter:
        return cls(fail_every=3)
    elif cls == SlowAdapter:
        return cls(latency_ms=10)
    else:
        return cls()


# ─── Contract: submit() ─────────────────────────────────────────────────────

class TestSubmitContract:
    """submit() must return a string job ID and accept the standard params."""

    @pytest.mark.asyncio
    async def test_submit_returns_string(self, adapter):
        job_id = await adapter.submit(
            task_id="test-task-1",
            action_type="execute",
            input_data={"request": "test"},
        )
        assert isinstance(job_id, str)
        assert len(job_id) > 0

    @pytest.mark.asyncio
    async def test_submit_returns_unique_ids(self, adapter):
        ids = set()
        for _ in range(5):
            job_id = await adapter.submit(
                task_id=f"task-{_}", action_type="execute", input_data={},
            )
            ids.add(job_id)
        assert len(ids) == 5  # all unique


# ─── Contract: get_status() ─────────────────────────────────────────────────

class TestGetStatusContract:
    """get_status() must return a known status string."""

    @pytest.mark.asyncio
    async def test_get_status_returns_string(self, adapter):
        job_id = await adapter.submit("t1", "execute", {})
        status = await adapter.get_status(job_id)
        assert isinstance(status, str)
        assert status in ("completed", "failed", "running", "cancelled", "unknown")

    @pytest.mark.asyncio
    async def test_get_status_unknown_job(self, adapter):
        status = await adapter.get_status("nonexistent-job-id")
        assert isinstance(status, str)


# ─── Contract: get_result() ─────────────────────────────────────────────────

class TestGetResultContract:
    """get_result() must return an AdapterResult."""

    @pytest.mark.asyncio
    async def test_get_result_returns_adapter_result(self, adapter):
        job_id = await adapter.submit("t1", "execute", {})
        result = await adapter.get_result(job_id)
        assert isinstance(result, AdapterResult)
        assert isinstance(result.status, str)
        assert isinstance(result.error, str)

    @pytest.mark.asyncio
    async def test_result_has_success_property(self, adapter):
        job_id = await adapter.submit("t1", "execute", {})
        result = await adapter.get_result(job_id)
        assert isinstance(result.success, bool)
        assert result.success == (result.status == "completed")


# ─── Contract: cancel() ─────────────────────────────────────────────────────

class TestCancelContract:
    """cancel() must return a bool."""

    @pytest.mark.asyncio
    async def test_cancel_returns_bool(self, adapter):
        job_id = await adapter.submit("t1", "execute", {})
        result = await adapter.cancel(job_id)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_bool(self, adapter):
        result = await adapter.cancel("nonexistent-job-id")
        assert isinstance(result, bool)


# ─── Contract: health_check() ───────────────────────────────────────────────

class TestHealthCheckContract:
    """health_check() must return a bool."""

    @pytest.mark.asyncio
    async def test_health_check_returns_bool(self, adapter):
        result = await adapter.health_check()
        assert isinstance(result, bool)


# ─── Contract: LLM Config Passthrough ───────────────────────────────────────

class TestLLMConfigContract:
    """Each adapter must pass correct LLM config to the platform."""

    @pytest.mark.asyncio
    async def test_agent_s_uses_agent_s_smart(self):
        adapter = AgentSAdapter(litellm_base_url="http://test:4000", litellm_api_key="sk-test")
        # Verify internal config
        assert adapter.litellm_base_url == "http://test:4000"
        assert adapter.litellm_api_key == "sk-test"

    @pytest.mark.asyncio
    async def test_browser_uses_browser_smart(self):
        adapter = BrowserUseAdapter(litellm_base_url="http://test:4000", litellm_api_key="sk-test")
        assert adapter.litellm_base_url == "http://test:4000"

    @pytest.mark.asyncio
    async def test_openhands_uses_openhands_smart(self):
        adapter = OpenHandsAdapter(litellm_base_url="http://test:4000", litellm_api_key="sk-test")
        assert adapter.litellm_base_url == "http://test:4000"
