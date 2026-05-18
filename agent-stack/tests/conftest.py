"""Shared test fixtures for the Composable Agent Stack test suite.

Centralizes common fixture setup so individual test modules stay focused
on behavior rather than boilerplate initialization/teardown.
"""

import pytest

from orchestrator.compression.manager import CompressionManager
from orchestrator.api.orchestrator import OrchestratorService
from orchestrator.state.models import Platform
from tests.harness.mocks import MockPlatformAdapter
from tests.harness.factories import TaskFactory, WorkflowFactory
from tests.rl.optimizer import RLTestOptimizer


# ─── Compression Manager ─────────────────────────────────────────────────────

@pytest.fixture
async def compression_manager(tmp_path):
    """Create, initialize, and tear down a CompressionManager with a temp DB."""
    db_path = str(tmp_path / "test_memory.db")
    mgr = CompressionManager(
        db_path=db_path,
        compact_threshold_bytes=200,
        compact_max_snapshot_bytes=1000,
        session_ttl_hours=1,
        auto_compact_interval_seconds=5,
    )
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


# ─── Orchestrator Service ────────────────────────────────────────────────────

@pytest.fixture
async def orchestrator_service(compression_manager, mock_adapter):
    """Create an OrchestratorService with mock adapters and the compression_manager."""
    orch = OrchestratorService(
        compression_manager=compression_manager,
        litellm_base_url="http://localhost:4000",
        litellm_api_key="test-key",
    )
    # Replace real adapters with mock
    orch.adapters = {
        Platform.AGENT_S: mock_adapter,
        Platform.BROWSER_USE: mock_adapter,
        Platform.OPENHANDS: mock_adapter,
    }
    yield orch
    await orch.stop()


# ─── Mock Adapter ────────────────────────────────────────────────────────────

@pytest.fixture
async def mock_adapter():
    """Create a MockPlatformAdapter."""
    return MockPlatformAdapter()


# ─── RL Optimizer ────────────────────────────────────────────────────────────

@pytest.fixture
async def rl_optimizer(tmp_path):
    """Create and initialize an RLTestOptimizer with a temp DB."""
    db_path = str(tmp_path / "test_rl.db")
    opt = RLTestOptimizer(db_path=db_path)
    await opt.initialize()
    yield opt
    await opt.shutdown()


# ─── Factory Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def task_factory():
    """Return the TaskFactory class for building test Task objects."""
    return TaskFactory


@pytest.fixture
def workflow_factory():
    """Return the WorkflowFactory class for building test Workflow objects."""
    return WorkflowFactory
