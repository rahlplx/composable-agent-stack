"""TDD Tests for State Machine, Task Dispatcher, and Dependency DAG.

Critical path: these govern the correctness of task execution order,
retry logic, and failure cascade.
"""

import pytest

from orchestrator.state.models import (
    Task, Workflow, TaskStatus, WorkflowStatus, Platform,
    classify_task, decompose_request, build_dependency_dag,
    can_transition, transition_task, check_dependencies,
    should_retry, cascade_failure,
)


# ─── Task Classification ─────────────────────────────────────────────────────

class TestClassifyTask:
    """Keyword heuristics for routing tasks to platforms."""

    def test_browser_use_keywords(self):
        platform, conf = classify_task("scrape the product price from the website")
        assert platform == Platform.BROWSER_USE
        assert conf == "high"

    def test_agent_s_keywords(self):
        platform, conf = classify_task("open Excel and update the spreadsheet cell")
        assert platform == Platform.AGENT_S
        assert conf == "high"

    def test_openhands_keywords(self):
        platform, conf = classify_task("write a Python function to sort a list and add tests")
        assert platform == Platform.OPENHANDS
        assert conf == "high"

    def test_ambiguous_returns_llm(self):
        platform, conf = classify_task("check the data and update the system")
        # "data" doesn't strongly match any platform
        assert conf in ("low", "medium")

    def test_empty_request(self):
        platform, conf = classify_task("")
        assert conf == "low"

    def test_multi_platform_keywords_ambiguous(self):
        """If browser AND code keywords appear with similar strength, should be ambiguous."""
        platform, conf = classify_task("navigate the web page and compile the code program")
        # "web" + "page" = 2 for browser, "code" + "compile" + "program" = 3 for openhands
        # close scores → ambiguous
        assert conf in ("medium", "low")


# ─── State Machine Transitions ───────────────────────────────────────────────

class TestStateTransitions:
    """Valid and invalid state transitions for tasks."""

    def test_pending_to_queued(self):
        assert can_transition(TaskStatus.PENDING, TaskStatus.QUEUED) is True

    def test_pending_to_running_invalid(self):
        assert can_transition(TaskStatus.PENDING, TaskStatus.RUNNING) is False

    def test_queued_to_running(self):
        assert can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING) is True

    def test_running_to_completed(self):
        assert can_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED) is True

    def test_running_to_failed(self):
        assert can_transition(TaskStatus.RUNNING, TaskStatus.FAILED) is True

    def test_failed_to_queued_retry(self):
        assert can_transition(TaskStatus.FAILED, TaskStatus.QUEUED) is True

    def test_completed_is_terminal(self):
        assert can_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING) is False
        assert can_transition(TaskStatus.COMPLETED, TaskStatus.QUEUED) is False

    def test_skipped_is_terminal(self):
        assert can_transition(TaskStatus.SKIPPED, TaskStatus.RUNNING) is False


class TestTransitionTask:
    """transition_task function with validation."""

    def test_valid_transition(self):
        task = Task(status=TaskStatus.PENDING)
        transition_task(task, TaskStatus.QUEUED)
        assert task.status == TaskStatus.QUEUED

    def test_invalid_transition_raises(self):
        task = Task(status=TaskStatus.PENDING)
        with pytest.raises(ValueError, match="Invalid transition"):
            transition_task(task, TaskStatus.COMPLETED)

    def test_transition_updates_timestamp(self):
        task = Task(status=TaskStatus.QUEUED)
        old_ts = task.updated_at
        transition_task(task, TaskStatus.RUNNING)
        assert task.updated_at != old_ts

    def test_failed_transition_stores_error(self):
        task = Task(status=TaskStatus.RUNNING)
        transition_task(task, TaskStatus.FAILED, error="timeout")
        assert task.error == "timeout"


# ─── Dependency Resolution ───────────────────────────────────────────────────

class TestDependencyResolution:
    """DAG dependency checking for task execution order."""

    def test_no_dependencies_always_ready(self):
        task = Task(depends_on=[])
        assert check_dependencies(task, {}) is True

    def test_dependency_completed(self):
        dep = Task(status=TaskStatus.COMPLETED)
        task = Task(depends_on=[dep.id])
        assert check_dependencies(task, {dep.id: dep}) is True

    def test_dependency_pending(self):
        dep = Task(status=TaskStatus.PENDING)
        task = Task(depends_on=[dep.id])
        assert check_dependencies(task, {dep.id: dep}) is False

    def test_dependency_running(self):
        dep = Task(status=TaskStatus.RUNNING)
        task = Task(depends_on=[dep.id])
        assert check_dependencies(task, {dep.id: dep}) is False

    def test_multiple_deps_all_completed(self):
        d1 = Task(status=TaskStatus.COMPLETED)
        d2 = Task(status=TaskStatus.COMPLETED)
        task = Task(depends_on=[d1.id, d2.id])
        assert check_dependencies(task, {d1.id: d1, d2.id: d2}) is True

    def test_multiple_deps_one_incomplete(self):
        d1 = Task(status=TaskStatus.COMPLETED)
        d2 = Task(status=TaskStatus.RUNNING)
        task = Task(depends_on=[d1.id, d2.id])
        assert check_dependencies(task, {d1.id: d1, d2.id: d2}) is False

    def test_missing_dependency(self):
        task = Task(depends_on=["nonexistent"])
        assert check_dependencies(task, {}) is False


# ─── Retry Logic ─────────────────────────────────────────────────────────────

class TestRetryLogic:
    """Retry decisions for failed tasks."""

    def test_should_retry_failed_task(self):
        task = Task(status=TaskStatus.FAILED, retries=0, max_retries=3)
        assert should_retry(task) is True

    def test_should_not_retry_exhausted(self):
        task = Task(status=TaskStatus.FAILED, retries=3, max_retries=3)
        assert should_retry(task) is False

    def test_should_not_retry_completed(self):
        task = Task(status=TaskStatus.COMPLETED, retries=0, max_retries=3)
        assert should_retry(task) is False

    def test_should_not_retry_pending(self):
        task = Task(status=TaskStatus.PENDING, retries=0, max_retries=3)
        assert should_retry(task) is False


# ─── Failure Cascade ─────────────────────────────────────────────────────────

class TestFailureCascade:
    """When a task fails permanently, downstream tasks are skipped."""

    def test_cascade_skips_downstream(self):
        t1 = Task(status=TaskStatus.FAILED, max_retries=3, retries=3)
        t2 = Task(status=TaskStatus.PENDING, depends_on=[t1.id])
        t3 = Task(status=TaskStatus.PENDING, depends_on=[t2.id])

        all_tasks = {t1.id: t1, t2.id: t2, t3.id: t3}
        skipped = cascade_failure(t1, all_tasks)

        assert t2.id in skipped
        assert t2.status == TaskStatus.SKIPPED

    def test_cascade_does_not_affect_completed(self):
        t1 = Task(status=TaskStatus.FAILED)
        t2 = Task(status=TaskStatus.COMPLETED, depends_on=[t1.id])

        all_tasks = {t1.id: t1, t2.id: t2}
        skipped = cascade_failure(t1, all_tasks)

        assert t2.status == TaskStatus.COMPLETED
        assert t2.id not in skipped

    def test_cascade_does_not_affect_independent(self):
        t1 = Task(status=TaskStatus.FAILED)
        t2 = Task(status=TaskStatus.PENDING, depends_on=[])

        all_tasks = {t1.id: t1, t2.id: t2}
        skipped = cascade_failure(t1, all_tasks)

        assert t2.status == TaskStatus.PENDING


# ─── DAG Building ────────────────────────────────────────────────────────────

class TestDAGBuilder:
    """build_dependency_dag creates proper task dependencies."""

    def test_linear_dag(self):
        specs = [
            {"platform": "browser_use", "action_type": "extract"},
            {"platform": "agent_s", "action_type": "update", "depends_on": [0]},
            {"platform": "agent_s", "action_type": "email", "depends_on": [1]},
        ]
        tasks = build_dependency_dag(specs)
        assert len(tasks) == 3
        assert tasks[0].depends_on == []
        assert tasks[1].depends_on == [tasks[0].id]
        assert tasks[2].depends_on == [tasks[1].id]

    def test_parallel_dag(self):
        specs = [
            {"platform": "browser_use", "action_type": "extract_1"},
            {"platform": "browser_use", "action_type": "extract_2"},
            {"platform": "agent_s", "action_type": "merge", "depends_on": [0, 1]},
        ]
        tasks = build_dependency_dag(specs)
        assert tasks[0].depends_on == []
        assert tasks[1].depends_on == []
        assert set(tasks[2].depends_on) == {tasks[0].id, tasks[1].id}

    def test_empty_dag(self):
        tasks = build_dependency_dag([])
        assert tasks == []


# ─── Decompose Request ───────────────────────────────────────────────────────

class TestDecomposeRequest:
    """Simple request decomposition."""

    def test_single_task_decomposition(self):
        tasks = decompose_request("scrape the product price")
        assert len(tasks) >= 1
        assert tasks[0].platform == Platform.BROWSER_USE

    def test_decomposition_has_no_dependencies(self):
        tasks = decompose_request("open Excel and update cell A1")
        for t in tasks:
            assert t.depends_on == []
