"""FastAPI application — composable agent stack orchestrator.

Routes:
- POST /v1/workflows         — submit workflow
- GET  /v1/workflows         — list workflows
- GET  /v1/workflows/{id}    — get workflow status
- GET  /v1/tasks/{id}        — get task status
- DELETE /v1/tasks/{id}      — cancel task
- POST /v1/compact/{session} — trigger /compact on execution memory
- GET  /v1/snapshot          — get execution memory snapshot
- WS   /ws                   — real-time status
- GET  /health               — health check
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.compression.manager import CompressionManager, Priority
from orchestrator.api.orchestrator import (
    OrchestratorService, SubmitWorkflowRequest,
    TaskResponse, WorkflowResponse, CompactResponse, SnapshotResponse,
)
from orchestrator.metrics import setup_metrics


# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize compression manager + orchestrator on startup."""
    db_path = str(Path(__file__).resolve().parent.parent.parent / "data" / "memory.db")
    mem = CompressionManager(db_path=db_path)
    await mem.initialize()

    orch = OrchestratorService(
        compression_manager=mem,
        litellm_base_url="http://localhost:4000",
    )
    await orch.start()

    app.state.mem = mem
    app.state.orch = orch

    yield

    await orch.stop()
    await mem.shutdown()


app = FastAPI(
    title="Composable Agent Stack",
    description="Orchestrator: Agent-S + Browser Use + OpenHands via LiteLLM",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Prometheus /metrics endpoint
setup_metrics(app)


# ─── Workflow Routes ─────────────────────────────────────────────────────────

@app.post("/v1/workflows", response_model=WorkflowResponse)
async def submit_workflow(request: SubmitWorkflowRequest):
    """Submit a new workflow for orchestrated execution."""
    orch: OrchestratorService = app.state.orch
    wf = await orch.submit_workflow(request)
    return WorkflowResponse(
        id=wf.id,
        user_request=wf.user_request,
        status=wf.status.value,
        tasks=[
            TaskResponse(
                id=t.id, workflow_id=t.workflow_id,
                platform=t.platform.value, action_type=t.action_type,
                status=t.status.value, result=t.result,
                error=t.error, retries=t.retries,
            )
            for t in wf.tasks
        ],
        created_at=wf.created_at,
    )


@app.get("/v1/workflows", response_model=list[WorkflowResponse])
async def list_workflows():
    orch: OrchestratorService = app.state.orch
    workflows = orch.list_workflows()
    return [
        WorkflowResponse(
            id=wf.id, user_request=wf.user_request,
            status=wf.status.value,
            tasks=[
                TaskResponse(
                    id=t.id, workflow_id=t.workflow_id,
                    platform=t.platform.value, action_type=t.action_type,
                    status=t.status.value, result=t.result,
                    error=t.error, retries=t.retries,
                )
                for t in wf.tasks
            ],
            created_at=wf.created_at,
        )
        for wf in workflows
    ]


@app.get("/v1/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    orch: OrchestratorService = app.state.orch
    wf = orch.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return WorkflowResponse(
        id=wf.id, user_request=wf.user_request,
        status=wf.status.value,
        tasks=[
            TaskResponse(
                id=t.id, workflow_id=t.workflow_id,
                platform=t.platform.value, action_type=t.action_type,
                status=t.status.value, result=t.result,
                error=t.error, retries=t.retries,
            )
            for t in wf.tasks
        ],
        created_at=wf.created_at,
    )


# ─── Task Routes ─────────────────────────────────────────────────────────────

@app.get("/v1/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    orch: OrchestratorService = app.state.orch
    task = orch.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskResponse(
        id=task.id, workflow_id=task.workflow_id,
        platform=task.platform.value, action_type=task.action_type,
        status=task.status.value, result=task.result,
        error=task.error, retries=task.retries,
    )


@app.delete("/v1/tasks/{task_id}")
async def cancel_task(task_id: str):
    orch: OrchestratorService = app.state.orch
    task = orch.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    job_id = orch._task_to_job.get(task_id)
    if job_id and task.platform in orch.adapters:
        adapter = orch.adapters[task.platform]
        await adapter.cancel(job_id)
    return {"status": "cancelled", "task_id": task_id}


# ─── /compact & Execution Memory Routes ─────────────────────────────────────

@app.post("/v1/compact/{session_id}", response_model=CompactResponse)
async def compact_session(session_id: str):
    """Trigger /compact on a session's execution memory.

    This is Z.ai's context management tool — compresses session
    context to prevent overflow during long execution runs.
    Critical entries are never removed.
    """
    mem: CompressionManager = app.state.mem
    session = await mem.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    result = await mem.compact(session_id, triggered_by="manual")
    return CompactResponse(
        session_id=session_id,
        entries_before=result.entries_before,
        entries_after=result.entries_after,
        bytes_before=result.bytes_before,
        bytes_after=result.bytes_after,
        compression_ratio=result.compression_ratio,
        critical_preserved=result.critical_preserved,
        savings_pct=result.savings_pct,
    )


@app.get("/v1/snapshot", response_model=SnapshotResponse)
async def get_snapshot(session_id: str | None = None):
    """Get execution memory snapshot for context injection.

    This is what Z.ai calls before generating a response to
    inject the relevant working context into the prompt.
    """
    mem: CompressionManager = app.state.mem
    snap = await mem.snapshot(session_id)
    return SnapshotResponse(
        session=snap.get("session", {}),
        context=snap.get("context", []),
        low_entries_count=snap.get("low_entries_count", 0),
    )


@app.get("/v1/memory/context")
async def get_context(session_id: str, category: str | None = None):
    """Retrieve context entries from execution memory."""
    mem: CompressionManager = app.state.mem
    return await mem.get_context(session_id, category=category)


@app.post("/v1/memory/store")
async def store_context(
    key: str,
    value: dict,
    session_id: str | None = None,
    category: str = "general",
    priority: Priority = Priority.MEDIUM,
):
    """Store a context entry in execution memory."""
    mem: CompressionManager = app.state.mem
    entry_id = await mem.store(
        key=key, value=value, session_id=session_id,
        category=category, priority=priority,
    )
    return {"id": entry_id, "key": key}


# ─── WebSocket ───────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time status updates for workflows and tasks."""
    orch: OrchestratorService = app.state.orch
    await orch.subscribe(ws)


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    mem: CompressionManager = app.state.mem
    sessions = await mem.list_sessions()
    return {
        "status": "healthy",
        "sessions": len(sessions),
        "workflows": len(app.state.orch.workflows),
    }
