"""FastAPI Orchestrator — the central coordination service.

Combines:
- SQLite Compression Manager (Z.ai execution memory)
- State Machine (task lifecycle)
- Platform Adapters (Agent-S, Browser Use, OpenHands)
- Redis Streams (task distribution)
- WebSocket (real-time status)

This is the main entry point for the composable agent stack.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from orchestrator.compression.manager import CompressionManager, Priority
from orchestrator.state.models import (
    Task, Workflow, TaskStatus, WorkflowStatus, Platform,
    classify_task, decompose_request, build_dependency_dag,
    transition_task, check_dependencies, should_retry, cascade_failure,
)
from orchestrator.adapters.agents import AgentSAdapter
from orchestrator.adapters.browser import BrowserUseAdapter
from orchestrator.adapters.openhands import OpenHandsAdapter
from orchestrator.adapters.base import PlatformAdapter
from orchestrator.adapters.circuit_breaker import CircuitBreaker, CircuitState
from orchestrator.metrics import (
    WORKFLOWS_TOTAL, TASKS_TOTAL, TASK_DURATION_SECONDS,
    ACTIVE_TASKS, COMPRESSION_BYTES, set_circuit_breaker_state,
)


# ─── Pydantic Models (API) ──────────────────────────────────────────────────

class SubmitWorkflowRequest(BaseModel):
    user_request: str = Field(min_length=1)
    tasks: list[dict] | None = None  # explicit DAG specs, or None for auto-decompose


class TaskResponse(BaseModel):
    id: str
    workflow_id: str
    platform: str
    action_type: str
    status: str
    result: Any = None
    error: str = ""
    retries: int = 0


class WorkflowResponse(BaseModel):
    id: str
    user_request: str
    status: str
    tasks: list[TaskResponse]
    created_at: str


class CompactResponse(BaseModel):
    session_id: str
    entries_before: int
    entries_after: int
    bytes_before: int
    bytes_after: int
    compression_ratio: float
    critical_preserved: int
    savings_pct: float


class SnapshotResponse(BaseModel):
    session: dict
    context: list[dict]
    low_entries_count: int


# ─── Orchestrator Core ──────────────────────────────────────────────────────

class OrchestratorService:
    """Central orchestrator coordinating all platform adapters.

    Uses CompressionManager for execution memory (Z.ai's context tool)
    and manages the full task lifecycle through the state machine.
    """

    def __init__(
        self,
        compression_manager: CompressionManager,
        redis_url: str = "redis://localhost:6379/0",
        litellm_base_url: str = "http://localhost:4000",
        litellm_api_key: str = "",
    ):
        self.mem = compression_manager
        self.litellm_base_url = litellm_base_url
        self.litellm_api_key = litellm_api_key

        # Platform adapters wrapped in circuit breakers
        self._raw_adapters: dict[Platform, PlatformAdapter] = {
            Platform.AGENT_S: AgentSAdapter(
                litellm_base_url=litellm_base_url,
                litellm_api_key=litellm_api_key,
            ),
            Platform.BROWSER_USE: BrowserUseAdapter(
                litellm_base_url=litellm_base_url,
                litellm_api_key=litellm_api_key,
            ),
            Platform.OPENHANDS: OpenHandsAdapter(
                litellm_base_url=litellm_base_url,
                litellm_api_key=litellm_api_key,
            ),
        }
        self.adapters: dict[Platform, CircuitBreaker | PlatformAdapter] = {
            platform: CircuitBreaker(adapter)
            for platform, adapter in self._raw_adapters.items()
        }
        # Initialize circuit breaker gauges
        for platform in self.adapters:
            set_circuit_breaker_state(platform.value, "closed")

        # In-memory workflow/task store (production: PostgreSQL)
        self.workflows: dict[str, Workflow] = {}
        self.tasks: dict[str, Task] = {}

        # WebSocket subscribers
        self._ws_subscribers: list[WebSocket] = []

        # Platform job tracking
        self._task_to_job: dict[str, str] = {}  # task_id -> platform_job_id

        # Background task
        self._dispatch_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start background dispatch loop and auto-compact."""
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        await self.mem.store_decision(
            "orchestrator_started",
            {"timestamp": datetime.now(timezone.utc).isoformat()},
            source="orchestrator",
        )

    async def stop(self) -> None:
        """Stop background tasks."""
        if self._dispatch_task:
            self._dispatch_task.cancel()
            self._dispatch_task = None

    # ── Workflow Submission ──────────────────────────────────────────

    async def submit_workflow(self, request: SubmitWorkflowRequest) -> Workflow:
        """Submit a new workflow for execution."""
        WORKFLOWS_TOTAL.inc()
        workflow = Workflow(user_request=request.user_request)

        if request.tasks:
            # Explicit DAG specification
            tasks = build_dependency_dag(request.tasks)
        else:
            # Auto-decompose
            tasks = decompose_request(request.user_request)

        for task in tasks:
            task.workflow_id = workflow.id
            self.tasks[task.id] = task
            workflow.tasks.append(task)

        self.workflows[workflow.id] = workflow

        # Store in compression manager for context persistence
        await self.mem.store_workflow(
            f"workflow_{workflow.id}",
            workflow.to_dict(),
            source="orchestrator",
        )

        # Broadcast workflow created
        await self._broadcast({
            "event": "workflow_created",
            "workflow_id": workflow.id,
            "task_count": len(tasks),
        })

        return workflow

    # ── Task Dispatch Loop ───────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        """Background loop: check for ready tasks and dispatch them."""
        while True:
            try:
                await self._process_ready_tasks()
                await self._check_running_tasks()
                await asyncio.sleep(1.0)  # poll interval
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.mem.store_debug(
                    "dispatch_error",
                    {"error": str(e)},
                    source="dispatch_loop",
                )
                await asyncio.sleep(5.0)

    async def _process_ready_tasks(self) -> None:
        """Find PENDING tasks with met dependencies → move to QUEUED → dispatch."""
        for task in list(self.tasks.values()):
            if task.status != TaskStatus.PENDING:
                continue

            # Check dependencies
            if not check_dependencies(task, self.tasks):
                continue

            # PENDING → QUEUED
            transition_task(task, TaskStatus.QUEUED)
            await self._broadcast({
                "event": "task_queued",
                "task_id": task.id,
                "platform": task.platform.value,
            })

            # Dispatch to platform adapter
            adapter = self.adapters.get(task.platform)
            if adapter is None:
                transition_task(task, TaskStatus.FAILED,
                                error=f"No adapter for platform {task.platform.value}")
                continue

            try:
                import time as _time
                start = _time.monotonic()
                job_id = await adapter.submit(
                    task_id=task.id,
                    action_type=task.action_type,
                    input_data=task.input_data,
                )
                elapsed = _time.monotonic() - start
                TASK_DURATION_SECONDS.observe(elapsed)
                self._task_to_job[task.id] = job_id
                transition_task(task, TaskStatus.RUNNING)
                ACTIVE_TASKS.inc()
                TASKS_TOTAL.labels(platform=task.platform.value, status="running").inc()
                # Update circuit breaker gauge
                if isinstance(adapter, CircuitBreaker):
                    set_circuit_breaker_state(task.platform.value, adapter.state.value)
                await self._broadcast({
                    "event": "task_running",
                    "task_id": task.id,
                    "platform": task.platform.value,
                    "job_id": job_id,
                })
            except Exception as e:
                TASKS_TOTAL.labels(platform=task.platform.value, status="failed").inc()
                if isinstance(adapter, CircuitBreaker):
                    set_circuit_breaker_state(task.platform.value, adapter.state.value)
                transition_task(task, TaskStatus.FAILED, error=str(e))
                await self._handle_failure(task)

    async def _check_running_tasks(self) -> None:
        """Poll running tasks for completion."""
        for task in list(self.tasks.values()):
            if task.status != TaskStatus.RUNNING:
                continue

            job_id = self._task_to_job.get(task.id)
            if not job_id:
                continue

            adapter = self.adapters.get(task.platform)
            if adapter is None:
                continue

            try:
                status = await adapter.get_status(job_id)
                if status == "completed":
                    result = await adapter.get_result(job_id)
                    task.result = result.output
                    transition_task(task, TaskStatus.COMPLETED)
                    ACTIVE_TASKS.dec()
                    TASKS_TOTAL.labels(platform=task.platform.value, status="completed").inc()
                    await self._broadcast({
                        "event": "task_completed",
                        "task_id": task.id,
                        "platform": task.platform.value,
                    })
                    await self._check_workflow_completion(task.workflow_id)
                elif status == "failed":
                    ACTIVE_TASKS.dec()
                    TASKS_TOTAL.labels(platform=task.platform.value, status="failed").inc()
                    transition_task(task, TaskStatus.FAILED, error="Platform reported failure")
                    await self._handle_failure(task)
            except Exception as e:
                # Don't fail on polling errors, just log
                pass

    async def _handle_failure(self, task: Task) -> None:
        """Handle task failure: retry, reassign, or escalate."""
        await self.mem.store_debug(
            f"task_failed_{task.id}",
            {"error": task.error, "retries": task.retries,
             "platform": task.platform.value},
            source="orchestrator",
        )

        if should_retry(task):
            task.retries += 1
            # Exponential backoff: 1s, 2s, 4s
            backoff = 2 ** (task.retries - 1)
            await asyncio.sleep(backoff)
            transition_task(task, TaskStatus.QUEUED)
            await self._broadcast({
                "event": "task_retrying",
                "task_id": task.id,
                "attempt": task.retries,
            })
        else:
            # Permanent failure — cascade
            all_wf_tasks = {
                t.id: t for t in self.tasks.values()
                if t.workflow_id == task.workflow_id
            }
            skipped = cascade_failure(task, all_wf_tasks)

            # Check if workflow is now failed
            wf = self.workflows.get(task.workflow_id)
            if wf:
                wf.status = WorkflowStatus.FAILED

            await self._broadcast({
                "event": "task_failed_permanent",
                "task_id": task.id,
                "skipped_count": len(skipped),
                "workflow_id": task.workflow_id,
            })

    async def _check_workflow_completion(self, workflow_id: str) -> None:
        """Check if all tasks in a workflow are complete."""
        wf = self.workflows.get(workflow_id)
        if not wf:
            return

        wf_tasks = [t for t in self.tasks.values() if t.workflow_id == workflow_id]
        all_done = all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
            for t in wf_tasks
        )

        if all_done:
            all_success = all(t.status == TaskStatus.COMPLETED for t in wf_tasks)
            wf.status = WorkflowStatus.COMPLETED if all_success else WorkflowStatus.FAILED

            await self.mem.store_output(
                f"workflow_result_{workflow_id}",
                {"status": wf.status.value, "task_count": len(wf_tasks)},
                source="orchestrator",
            )
            await self._broadcast({
                "event": "workflow_completed",
                "workflow_id": workflow_id,
                "status": wf.status.value,
            })

    # ── WebSocket ────────────────────────────────────────────────────

    async def subscribe(self, ws: WebSocket) -> None:
        await ws.accept()
        self._ws_subscribers.append(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            self._ws_subscribers.remove(ws)

    async def _broadcast(self, message: dict) -> None:
        dead = []
        for ws in self._ws_subscribers:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_subscribers.remove(ws)

    # ── Queries ──────────────────────────────────────────────────────

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self.workflows.get(workflow_id)

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def list_workflows(self) -> list[Workflow]:
        return list(self.workflows.values())
