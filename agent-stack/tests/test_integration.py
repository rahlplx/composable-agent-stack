"""Integration test: end-to-end workflow execution through the orchestrator.

Tests the full pipeline: submit workflow → classify → dispatch →
state machine transitions → completion, with the CompressionManager
tracking execution context.
"""

import asyncio
import pytest

from orchestrator.compression.manager import CompressionManager, Priority
from orchestrator.api.orchestrator import OrchestratorService, SubmitWorkflowRequest
from orchestrator.state.models import TaskStatus, WorkflowStatus, Platform


@pytest.fixture
async def integration_env(tmp_path):
    """Set up a full orchestrator with in-memory compression manager."""
    db_path = str(tmp_path / "integration_memory.db")
    mem = CompressionManager(
        db_path=db_path,
        compact_threshold_bytes=500,
        auto_compact_interval_seconds=5,
    )
    await mem.initialize()

    orch = OrchestratorService(
        compression_manager=mem,
        litellm_base_url="http://localhost:4000",
    )
    # Don't start dispatch loop — we'll manually drive it
    yield orch, mem
    await orch.stop()
    await mem.shutdown()


class TestE2EWorkflow:
    """End-to-end workflow through the orchestrator."""

    async def test_single_browser_task(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="scrape the product price from the website"
        ))
        assert len(wf.tasks) == 1
        assert wf.tasks[0].platform == Platform.BROWSER_USE

    async def test_explicit_dag_workflow(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="price monitoring pipeline",
            tasks=[
                {"platform": "browser_use", "action_type": "extract_price"},
                {"platform": "agent_s", "action_type": "update_spreadsheet", "depends_on": [0]},
                {"platform": "agent_s", "action_type": "send_email", "depends_on": [1]},
            ],
        ))
        assert len(wf.tasks) == 3
        assert wf.tasks[0].platform == Platform.BROWSER_USE
        assert wf.tasks[1].platform == Platform.AGENT_S
        assert wf.tasks[2].platform == Platform.AGENT_S

        # Verify dependency chain
        assert wf.tasks[0].depends_on == []
        assert wf.tasks[1].depends_on == [wf.tasks[0].id]
        assert wf.tasks[2].depends_on == [wf.tasks[1].id]

    async def test_parallel_tasks_in_dag(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="parallel extraction",
            tasks=[
                {"platform": "browser_use", "action_type": "extract_1"},
                {"platform": "browser_use", "action_type": "extract_2"},
                {"platform": "agent_s", "action_type": "merge", "depends_on": [0, 1]},
            ],
        ))
        assert wf.tasks[0].depends_on == []
        assert wf.tasks[1].depends_on == []
        assert set(wf.tasks[2].depends_on) == {wf.tasks[0].id, wf.tasks[1].id}

    async def test_workflow_stored_in_memory(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="memory test workflow"
        ))
        # The workflow should be stored in the compression manager
        sessions = await mem.list_sessions()
        assert len(sessions) >= 1

    async def test_dispatch_pending_tasks(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="scrape product prices",
        ))
        task = wf.tasks[0]

        # Manually run one dispatch cycle
        await orch._process_ready_tasks()

        # Task should have moved from PENDING → QUEUED → RUNNING
        # (since adapters simulate success when not connected)
        assert task.status in (TaskStatus.QUEUED, TaskStatus.RUNNING,
                               TaskStatus.COMPLETED)

    async def test_cascade_on_failure(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="cascade test",
            tasks=[
                {"platform": "browser_use", "action_type": "extract"},
                {"platform": "agent_s", "action_type": "update", "depends_on": [0]},
            ],
        ))

        t1, t2 = wf.tasks[0], wf.tasks[1]

        # Force t1 to fail permanently
        from orchestrator.state.models import transition_task
        transition_task(t1, TaskStatus.QUEUED)
        transition_task(t1, TaskStatus.RUNNING)
        transition_task(t1, TaskStatus.FAILED, error="simulated failure")
        t1.retries = t1.max_retries  # exhaust retries

        # Handle failure should cascade
        await orch._handle_failure(t1)

        # t2 should be skipped
        assert t2.status == TaskStatus.SKIPPED

    async def test_compact_integration(self, integration_env):
        orch, mem = integration_env
        wf = await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="compact integration test"
        ))

        # Store enough debug entries (>5 per category) to trigger merge
        session = (await mem.list_sessions())[0]
        for i in range(15):
            await mem.store_debug(f"step_{i}", {"action": f"step_{i}", "result": "ok"})

        result = await mem.compact(session.id)
        # Should have merged at least some medium entries
        assert result.entries_before > 0

    async def test_snapshot_after_workflow(self, integration_env):
        orch, mem = integration_env
        await orch.submit_workflow(SubmitWorkflowRequest(
            user_request="snapshot test"
        ))

        snap = await mem.snapshot()
        assert "session" in snap
        assert "context" in snap

    async def test_list_workflows(self, integration_env):
        orch, mem = integration_env
        await orch.submit_workflow(SubmitWorkflowRequest(user_request="wf1"))
        await orch.submit_workflow(SubmitWorkflowRequest(user_request="wf2"))
        workflows = orch.list_workflows()
        assert len(workflows) == 2
