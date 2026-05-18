"""Security tests — input validation, injection prevention, and boundary checks.

These tests verify that the API properly validates inputs and rejects
malformed, missing, or malicious data at the HTTP layer.
"""

import uuid

import pytest
from httpx import AsyncClient, ASGITransport

from orchestrator.api.app import app
from orchestrator.compression.manager import CompressionManager
from orchestrator.api.orchestrator import OrchestratorService


@pytest.fixture
async def client(tmp_path):
    """Create an async test client with properly initialized app state."""
    db_path = str(tmp_path / "test_security_memory.db")
    mem = CompressionManager(db_path=db_path)
    await mem.initialize()

    orch = OrchestratorService(
        compression_manager=mem,
        litellm_base_url="http://localhost:4000",
    )

    app.state.mem = mem
    app.state.orch = orch

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await orch.stop()
    await mem.shutdown()


# ─── Workflow Request Validation ─────────────────────────────────────────────

class TestWorkflowRequestValidation:
    """Ensure SubmitWorkflowRequest validates its fields."""

    async def test_workflow_request_empty_string(self, client: AsyncClient):
        """POST /v1/workflows with empty user_request should 422."""
        resp = await client.post(
            "/v1/workflows",
            json={"user_request": ""},
        )
        assert resp.status_code == 422

    async def test_workflow_request_missing_field(self, client: AsyncClient):
        """POST /v1/workflows with no body should 422."""
        resp = await client.post("/v1/workflows")
        assert resp.status_code == 422


# ─── Compact Endpoint Validation ─────────────────────────────────────────────

class TestCompactValidation:
    """Ensure /compact rejects invalid session IDs."""

    async def test_compact_nonexistent_session(self, client: AsyncClient):
        """POST /v1/compact/{bad_id} should 404."""
        bad_id = str(uuid.uuid4())
        resp = await client.post(f"/v1/compact/{bad_id}")
        assert resp.status_code == 404


# ─── Memory Store Validation ─────────────────────────────────────────────────

class TestMemoryStoreValidation:
    """Ensure /v1/memory/store validates priority values."""

    async def test_store_invalid_priority(self, client: AsyncClient):
        """POST /v1/memory/store with bad priority should error."""
        resp = await client.post(
            "/v1/memory/store",
            params={
                "key": "bad_prio",
                "priority": "ultra_high",  # not a valid Priority enum value
            },
            json={"data": 1},
        )
        # The endpoint calls Priority(priority) which raises ValueError
        # FastAPI should surface this as a 422 or 500
        assert resp.status_code in (422, 500)


# ─── ID Injection Prevention ─────────────────────────────────────────────────

class TestIDInjection:
    """Verify that task and session IDs are validated as UUIDs,
    preventing SQL injection or path traversal via crafted IDs."""

    async def test_task_id_injection(self, client: AsyncClient):
        """Task IDs should be UUIDs — SQL injection strings should 404, not error."""
        injection_ids = [
            "1; DROP TABLE tasks; --",
            "' OR '1'='1",
            "../../../etc/passwd",
            "<script>alert('xss')</script>",
        ]
        for bad_id in injection_ids:
            resp = await client.get(f"/v1/tasks/{bad_id}")
            # Should get 404 (not found) or 422 (validation error), never 500
            assert resp.status_code in (404, 422), (
                f"Expected 404/422 for injected task ID '{bad_id}', "
                f"got {resp.status_code}"
            )

    async def test_session_id_injection(self, client: AsyncClient):
        """Session IDs in /compact should be validated — non-UUID strings
        should 404 or 422, not cause internal errors."""
        injection_ids = [
            "1; DROP TABLE sessions; --",
            "' OR '1'='1",
            "../../../etc/passwd",
        ]
        for bad_id in injection_ids:
            resp = await client.post(f"/v1/compact/{bad_id}")
            # Should get 404 (not found) or 422 (validation error), never 500
            assert resp.status_code in (404, 422), (
                f"Expected 404/422 for injected session ID '{bad_id}', "
                f"got {resp.status_code}"
            )
