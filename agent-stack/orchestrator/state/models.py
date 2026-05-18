"""State Machine & Task Dispatcher for the Composable Agent Stack.

Implements the 6-state machine (PENDING→QUEUED→RUNNING→COMPLETED/FAILED/SKIPPED)
with retry logic, dependency resolution, and platform dispatch.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Platform(str, enum.Enum):
    AGENT_S = "agent_s"
    BROWSER_USE = "browser_use"
    OPENHANDS = "openhands"
    LLM = "llm"          # Direct LLM call (classification, parsing)


class WorkflowStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    platform: Platform = Platform.BROWSER_USE
    action_type: str = ""           # e.g. "extract_price", "update_spreadsheet"
    input_data: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    retries: int = 0
    max_retries: int = 3
    result: Any = None
    error: str = ""
    depends_on: list[str] = field(default_factory=list)  # task IDs
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    priority: int = 0               # higher = more important

    def to_dict(self) -> dict:
        return {
            "id": self.id, "workflow_id": self.workflow_id,
            "platform": self.platform.value, "action_type": self.action_type,
            "input_data": self.input_data, "status": self.status.value,
            "retries": self.retries, "max_retries": self.max_retries,
            "result": self.result, "error": self.error,
            "depends_on": self.depends_on, "priority": self.priority,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


@dataclass
class Workflow:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_request: str = ""
    status: WorkflowStatus = WorkflowStatus.RUNNING
    tasks: list[Task] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id, "user_request": self.user_request,
            "status": self.status.value,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
        }


# ── Task Dispatch: Keyword Heuristics + LLM Fallback ────────────────────────

KEYWORD_RULES: list[dict[str, Any]] = [
    {
        "keywords": ["web", "browser", "site", "scrape", "url", "page", "website", "navigate", "form"],
        "platform": Platform.BROWSER_USE,
        "confidence": "high",
    },
    {
        "keywords": ["desktop", "click", "app", "excel", "outlook", "spreadsheet",
                      "email", "file", "window", "menu", "button"],
        "platform": Platform.AGENT_S,
        "confidence": "high",
    },
    {
        "keywords": ["code", "program", "compile", "script", "function", "test",
                      "debug", "refactor", "implement", "build", "deploy"],
        "platform": Platform.OPENHANDS,
        "confidence": "high",
    },
]


def classify_task(request: str) -> tuple[Platform, str]:
    """Classify a task request to a platform using keyword heuristics.

    Returns (platform, confidence).
    For ambiguous requests, returns LLM platform for reclassification.
    """
    request_lower = request.lower()

    scores: dict[Platform, int] = {p: 0 for p in Platform}
    for rule in KEYWORD_RULES:
        for kw in rule["keywords"]:
            if kw in request_lower:
                scores[rule["platform"]] += 1

    best_platform = max(scores, key=lambda p: scores[p])
    best_score = scores[best_platform]

    if best_score == 0:
        return Platform.LLM, "low"

    # Check if there's a clear winner
    # Single match (score=1, others=0) is high confidence
    # Multiple platforms matching similarly = ambiguous
    other_scores = [scores[p] for p in Platform if p != best_platform]
    max_other = max(other_scores) if other_scores else 0

    if max_other > 0 and (best_score - max_other) < 2:
        return Platform.LLM, "medium"  # ambiguous, needs LLM reclassification

    return best_platform, "high"


def decompose_request(user_request: str) -> list[Task]:
    """Decompose a user request into subtasks with platform assignments.

    This is a simplified version that creates single tasks.
    For complex requests, the orchestrator would use LLM to build a DAG.
    """
    platform, confidence = classify_task(user_request)

    task = Task(
        platform=platform,
        action_type="execute",
        input_data={"request": user_request},
        depends_on=[],
    )

    return [task]


def build_dependency_dag(tasks: list[dict]) -> list[Task]:
    """Build a DAG of tasks from specification dicts.

    Each dict: {platform, action_type, input_data, depends_on: [indices]}
    Indices refer to position in the input list.
    """
    result = []
    for i, spec in enumerate(tasks):
        depends_on_indices = spec.get("depends_on", [])
        depends_on_ids = []
        for idx in depends_on_indices:
            if 0 <= idx < len(result):
                depends_on_ids.append(result[idx].id)

        task = Task(
            platform=Platform(spec.get("platform", "browser_use")),
            action_type=spec.get("action_type", "execute"),
            input_data=spec.get("input_data", {}),
            depends_on=depends_on_ids,
            priority=spec.get("priority", 0),
        )
        result.append(task)

    return result


# ── State Machine Transitions ────────────────────────────────────────────────

VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.SKIPPED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.SKIPPED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.SKIPPED},  # retry or skip
    TaskStatus.COMPLETED: set(),  # terminal
    TaskStatus.SKIPPED: set(),    # terminal
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Check if a state transition is valid."""
    return target in VALID_TRANSITIONS.get(current, set())


def transition_task(task: Task, new_status: TaskStatus, error: str = "") -> Task:
    """Transition a task to a new status. Raises ValueError if invalid."""
    if not can_transition(task.status, new_status):
        raise ValueError(
            f"Invalid transition: {task.status.value} → {new_status.value} "
            f"for task {task.id}"
        )
    task.status = new_status
    task.updated_at = datetime.now(timezone.utc).isoformat()
    if error:
        task.error = error
    return task


def check_dependencies(task: Task, all_tasks: dict[str, Task]) -> bool:
    """Check if all dependencies of a task are completed."""
    for dep_id in task.depends_on:
        dep = all_tasks.get(dep_id)
        if dep is None or dep.status != TaskStatus.COMPLETED:
            return False
    return True


def should_retry(task: Task) -> bool:
    """Check if a failed task should be retried."""
    return task.status == TaskStatus.FAILED and task.retries < task.max_retries


def cascade_failure(task: Task, all_tasks: dict[str, Task]) -> list[str]:
    """Mark all downstream tasks as SKIPPED when a task fails permanently.

    Returns list of skipped task IDs.
    """
    skipped = []
    for tid, t in all_tasks.items():
        if task.id in t.depends_on and t.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
            t.status = TaskStatus.SKIPPED
            t.updated_at = datetime.now(timezone.utc).isoformat()
            skipped.append(tid)
    return skipped
