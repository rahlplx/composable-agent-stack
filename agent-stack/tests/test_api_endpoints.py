"""TDD Tests for FastAPI endpoints using httpx.AsyncClient with ASGITransport.

Tests the HTTP layer of the composable agent stack orchestrator,
covering all REST endpoints defined in orchestrator.api.app.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from orchestrator.api.app import app
from orchestrator.compression.manager import CompressionManager
from orchestrator.api.orchestrator import OrchestratorService


@pytest.fixture
async def client(tmp_path):
    """Create an async test client with properly initialized app state.

    We manually set up the CompressionManager and OrchestratorService
    on app.state since ASGITransport doesn't trigger the ASGI lifespan.
    A temp DB is used so tests are isolated.
    """
    db_path = str(tmp_path / "test_api_memory.db")
    mem = CompressionManager(db_path=db_path)
    await mem.initialize()

    orch = OrchestratorService(
        compression_manager=mem,
        litellm_base_url="http://localhost:4000",
    )
    # Don't start the dispatch loop — we test the HTTP layer, not the background worker
    # await orch.start()

    app.state.mem = mem
    app.state.orch = orch

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup
    await orch.stop()
    await mem.shutdown()


# ─── Health ──────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    """GET /health returns service status."""

    async def test_health_endpoint(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "sessions" in data
        assert "workflows" in data


# ─── Workflow CRUD ───────────────────────────────────────────────────────────

class TestWorkflowEndpoints:
    """Workflow submission, listing, and retrieval."""

    async def test_submit_workflow(self, client: AsyncClient):
        resp = await client.post(
            "/v1/workflows",
            json={"user_request": "scrape product prices from example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["user_request"] == "scrape product prices from example.com"
        assert data["status"] in ("pending", "running", "completed")
        assert isinstance(data["tasks"], list)

    async def test_list_workflows(self, client: AsyncClient):
        # Submit a workflow first so the list isn't empty
        await client.post(
            "/v1/workflows",
            json={"user_request": "list-test workflow"},
        )
        resp = await client.get("/v1/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    async def test_get_workflow_not_found(self, client: AsyncClient):
        resp = await client.get("/v1/workflows/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ─── Task Endpoints ─────────────────────────────────────────────────────────

class TestTaskEndpoints:
    """Task retrieval and cancellation."""

    async def test_get_task_not_found(self, client: AsyncClient):
        resp = await client.get("/v1/tasks/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ─── Snapshot ────────────────────────────────────────────────────────────────

class TestSnapshotEndpoint:
    """GET /v1/snapshot returns execution memory snapshot."""

    async def test_snapshot_endpoint(self, client: AsyncClient):
        resp = await client.get("/v1/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert "session" in data
        assert "context" in data
        assert "low_entries_count" in data


# ─── Memory Store & Retrieve ────────────────────────────────────────────────

class TestMemoryEndpoints:
    """Store and retrieve context entries via the memory API."""

    async def test_store_and_retrieve_context(self, client: AsyncClient):
        # Store a context entry
        store_resp = await client.post(
            "/v1/memory/store",
            params={
                "key": "test_config",
                "category": "reference",
                "priority": "critical",
            },
            json={"model": "gpt-4o", "rpm": 500},
        )
        assert store_resp.status_code == 200
        store_data = store_resp.json()
        assert "id" in store_data
        assert store_data["key"] == "test_config"

        # Retrieve the context — need a session_id from the store response
        # The store endpoint auto-creates a session if none is given.
        # We'll list sessions via snapshot to get the session ID.
        snap_resp = await client.get("/v1/snapshot")
        snap_data = snap_resp.json()
        session_id = snap_data["session"].get("id")
        assert session_id is not None, "Expected an auto-created session"

        # Now retrieve context for that session
        ctx_resp = await client.get(
            "/v1/memory/context",
            params={"session_id": session_id},
        )
        assert ctx_resp.status_code == 200
        ctx_data = ctx_resp.json()
        assert isinstance(ctx_data, list)
        # Find our stored entry by key
        keys = [e["key"] for e in ctx_data]
        assert "test_config" in keys
