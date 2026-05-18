"""Property-Based Tests for State Machine — Hypothesis-driven.

These tests use Hypothesis to generate arbitrary inputs and verify
invariants that should hold for ALL possible inputs, not just the
ones we thought to test manually.
"""

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis.strategies import (
    text, sampled_from, lists, builds, integers, tuples, just, one_of,
)

from orchestrator.state.models import (
    Task, TaskStatus, Platform, WorkflowStatus,
    classify_task, can_transition, transition_task,
    check_dependencies, should_retry, cascade_failure,
    VALID_TRANSITIONS,
)


# ─── Strategies ──────────────────────────────────────────────────────────────

task_status_strategy = sampled_from(list(TaskStatus))
platform_strategy = sampled_from(list(Platform))

# Valid transition pairs: (from_status, to_status) that are valid
valid_transitions = [
    (from_s, to_s)
    for from_s, allowed in VALID_TRANSITIONS.items()
    for to_s in allowed
]

invalid_transitions = [
    (from_s, to_s)
    for from_s, allowed in VALID_TRANSITIONS.items()
    for to_s in TaskStatus
    if to_s not in allowed
]


# ─── Invariant: Valid transitions never raise ────────────────────────────────

class TestTransitionInvariants:
    """State transitions must obey these invariants for ALL inputs."""

    @given(from_status=sampled_from([s for s in TaskStatus if VALID_TRANSITIONS[s]]),
           to_status=sampled_from(TaskStatus))
    @settings(max_examples=100)
    def test_transition_result_matches_can_transition(self, from_status, to_status):
        """can_transition() must agree with whether transition_task raises."""
        task = Task(status=from_status)
        if can_transition(from_status, to_status):
            transition_task(task, to_status)
            assert task.status == to_status
        else:
            with pytest.raises(ValueError):
                transition_task(task, to_status)

    @given(from_status=task_status_strategy, to_status=task_status_strategy)
    @settings(max_examples=50)
    def test_terminal_states_block_all_transitions(self, from_status, to_status):
        """COMPLETED and SKIPPED must never allow any outgoing transition."""
        if from_status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED):
            assert can_transition(from_status, to_status) is False


# ─── Invariant: Dependency resolution ────────────────────────────────────────

class TestDependencyInvariants:
    """check_dependencies must hold these invariants."""

    @given(dep_ids=lists(text(min_size=1, max_size=20), max_size=5))
    @settings(max_examples=30)
    def test_all_completed_deps_means_ready(self, dep_ids):
        """If ALL dependencies are COMPLETED, task must be ready."""
        deps = {did: Task(id=did, status=TaskStatus.COMPLETED) for did in dep_ids}
        task = Task(depends_on=dep_ids)
        assert check_dependencies(task, deps) is True

    @given(dep_ids=lists(text(min_size=1, max_size=20), min_size=1, max_size=5))
    @settings(max_examples=30)
    def test_any_pending_dep_means_not_ready(self, dep_ids):
        """If ANY dependency is PENDING, task must NOT be ready."""
        deps = {did: Task(id=did, status=TaskStatus.PENDING) for did in dep_ids}
        task = Task(depends_on=dep_ids)
        assert check_dependencies(task, deps) is False

    @given(dep_ids=lists(text(min_size=1, max_size=20), max_size=5))
    @settings(max_examples=30)
    def test_no_deps_always_ready(self, dep_ids):
        """A task with no dependencies is always ready."""
        task = Task(depends_on=[])
        assert check_dependencies(task, {}) is True


# ─── Invariant: Classification ───────────────────────────────────────────────

class TestClassificationInvariants:
    """classify_task must hold these invariants for ALL inputs."""

    @given(request=text(min_size=1, max_size=500))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_always_returns_valid_platform(self, request):
        """classify_task must always return a valid Platform."""
        platform, confidence = classify_task(request)
        assert isinstance(platform, Platform)
        assert confidence in ("high", "medium", "low")

    @given(request=text(min_size=1, max_size=500))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_empty_or_garbage_returns_low_confidence(self, request):
        """Pure noise should get low or medium confidence."""
        # Only check if no keywords match
        keywords = ["web", "browser", "site", "scrape", "url", "desktop", "click",
                     "app", "excel", "code", "program", "script", "function"]
        has_keyword = any(kw in request.lower() for kw in keywords)
        if not has_keyword:
            platform, confidence = classify_task(request)
            assert confidence in ("low", "medium")

    @given(keyword=sampled_from(["scrape", "browser", "website", "url", "navigate"]))
    @settings(max_examples=10)
    def test_browser_keywords_always_route_to_browser(self, keyword):
        """Browser keywords must always route to BROWSER_USE with high confidence."""
        request = f"please {keyword} the data"
        platform, confidence = classify_task(request)
        assert platform == Platform.BROWSER_USE
        assert confidence == "high"


# ─── Invariant: Retry logic ──────────────────────────────────────────────────

class TestRetryInvariants:
    """should_retry must hold these invariants."""

    @given(retries=integers(min_value=0, max_value=100),
           max_retries=integers(min_value=1, max_value=100))
    @settings(max_examples=50)
    def test_retry_only_when_under_limit(self, retries, max_retries):
        task = Task(status=TaskStatus.FAILED, retries=retries, max_retries=max_retries)
        expected = retries < max_retries
        assert should_retry(task) == expected

    @given(status=sampled_from([s for s in TaskStatus if s != TaskStatus.FAILED]))
    @settings(max_examples=20)
    def test_non_failed_never_retries(self, status):
        task = Task(status=status, retries=0, max_retries=3)
        assert should_retry(task) is False


# ─── Invariant: Cascade ──────────────────────────────────────────────────────

class TestCascadeInvariants:
    """cascade_failure must hold these invariants."""

    @given(task_count=integers(min_value=2, max_value=10))
    @settings(max_examples=20)
    def test_cascade_only_affects_downstream(self, task_count):
        """cascade_failure should only skip tasks that depend on the failed one."""
        tasks = {}
        for i in range(task_count):
            t = Task(id=f"task_{i}", status=TaskStatus.PENDING, depends_on=[])
            tasks[t.id] = t

        # Make task_0 fail, task_1 depends on it, task_2 does not
        failed = tasks["task_0"]
        failed.status = TaskStatus.FAILED
        tasks["task_1"].depends_on = ["task_0"]

        skipped = cascade_failure(failed, tasks)

        assert "task_1" in skipped
        assert tasks["task_1"].status == TaskStatus.SKIPPED
        # task_2 should not be affected (no dependency on task_0)
        if task_count > 2:
            assert tasks["task_2"].status == TaskStatus.PENDING
