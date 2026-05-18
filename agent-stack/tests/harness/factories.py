"""Object Factories — generate test data with controlled defaults.

Enterprise pattern: every test uses factories instead of manual construction.
This ensures tests are maintainable and defaults can evolve in one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from orchestrator.state.models import (
    Task, Workflow, TaskStatus, WorkflowStatus, Platform,
)
from orchestrator.compression.manager import (
    Session, SessionStatus, ContextEntry, Priority,
)


# ─── Task Factory ────────────────────────────────────────────────────────────

class TaskFactory:
    """Build Task objects with sensible defaults for testing."""

    @staticmethod
    def build(
        id: str | None = None,
        workflow_id: str | None = None,
        platform: Platform = Platform.BROWSER_USE,
        action_type: str = "execute",
        input_data: dict | None = None,
        status: TaskStatus = TaskStatus.PENDING,
        retries: int = 0,
        max_retries: int = 3,
        result: Any = None,
        error: str = "",
        depends_on: list[str] | None = None,
        priority: int = 0,
    ) -> Task:
        return Task(
            id=id or str(uuid.uuid4()),
            workflow_id=workflow_id or str(uuid.uuid4()),
            platform=platform,
            action_type=action_type,
            input_data=input_data or {},
            status=status,
            retries=retries,
            max_retries=max_retries,
            result=result,
            error=error,
            depends_on=depends_on or [],
            priority=priority,
        )

    @staticmethod
    def build_batch(count: int, **overrides) -> list[Task]:
        return [TaskFactory.build(**overrides) for _ in range(count)]

    @staticmethod
    def build_dag_chain(length: int, platform: Platform = Platform.BROWSER_USE) -> list[Task]:
        """Build a linear chain of dependent tasks: t0 → t1 → t2 → ..."""
        tasks = []
        for i in range(length):
            depends_on = [tasks[i - 1].id] if i > 0 else []
            tasks.append(TaskFactory.build(
                platform=platform,
                action_type=f"step_{i}",
                depends_on=depends_on,
            ))
        return tasks

    @staticmethod
    def build_dag_parallel(fan_out: int, fan_in: bool = True) -> list[Task]:
        """Build a fan-out → optional fan-in DAG."""
        root = TaskFactory.build(action_type="root")
        tasks = [root]
        leaves = []
        for i in range(fan_out):
            leaf = TaskFactory.build(
                platform=Platform.BROWSER_USE,
                action_type=f"parallel_{i}",
                depends_on=[root.id],
            )
            tasks.append(leaf)
            leaves.append(leaf)

        if fan_in:
            merge = TaskFactory.build(
                platform=Platform.AGENT_S,
                action_type="merge",
                depends_on=[l.id for l in leaves],
            )
            tasks.append(merge)

        return tasks


# ─── Workflow Factory ────────────────────────────────────────────────────────

class WorkflowFactory:
    """Build Workflow objects for testing."""

    @staticmethod
    def build(
        id: str | None = None,
        user_request: str = "test workflow",
        status: WorkflowStatus = WorkflowStatus.RUNNING,
        task_count: int = 0,
    ) -> Workflow:
        wf = Workflow(
            id=id or str(uuid.uuid4()),
            user_request=user_request,
            status=status,
        )
        if task_count > 0:
            for t in TaskFactory.build_batch(task_count):
                t.workflow_id = wf.id
                wf.tasks.append(t)
        return wf


# ─── Session Factory (Compression Manager) ───────────────────────────────────

class SessionFactory:
    """Build Session objects for compression manager testing."""

    @staticmethod
    def build(
        id: str | None = None,
        name: str = "test-session",
        status: SessionStatus = SessionStatus.ACTIVE,
    ) -> Session:
        return Session(
            id=id or str(uuid.uuid4()),
            name=name,
            status=status,
        )


# ─── Context Entry Factory ──────────────────────────────────────────────────

class ContextEntryFactory:
    """Build ContextEntry objects for testing."""

    @staticmethod
    def build(
        id: str | None = None,
        session_id: str | None = None,
        category: str = "general",
        key: str = "test_key",
        value: Any = None,
        priority: Priority = Priority.MEDIUM,
        source: str = "test",
    ) -> ContextEntry:
        return ContextEntry(
            id=id or str(uuid.uuid4()),
            session_id=session_id or str(uuid.uuid4()),
            category=category,
            key=key,
            value=value or {"test": True},
            priority=priority,
            source=source,
        )

    @staticmethod
    def build_critical(key: str = "ref", value: Any = None) -> ContextEntry:
        return ContextEntryFactory.build(
            key=key, value=value, category="reference",
            priority=Priority.CRITICAL,
        )

    @staticmethod
    def build_batch(count: int, **overrides) -> list[ContextEntry]:
        return [ContextEntryFactory.build(**overrides) for _ in range(count)]
