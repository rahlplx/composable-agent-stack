"""TDD tests for Prometheus metrics endpoint.

Covers metric registration, counter increments, and the /metrics HTTP endpoint.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.metrics import (
    WORKFLOWS_TOTAL,
    TASKS_TOTAL,
    setup_metrics,
    set_circuit_breaker_state,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def metrics_app():
    """Create a minimal FastAPI app with metrics enabled."""
    app = FastAPI()
    setup_metrics(app)
    return app


@pytest.fixture()
def client(metrics_app):
    return TestClient(metrics_app)


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestMetricsEndpoint:
    def test_metrics_endpoint_exists(self, client):
        """GET /metrics should return 200."""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_endpoint_content_type(self, client):
        """Response should be Prometheus text format."""
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers.get("content-type", "") or \
               "text/version" in resp.headers.get("content-type", "")

    def test_metrics_contain_agent_stack_prefix(self, client):
        """All our custom metrics should appear in the output."""
        resp = client.get("/metrics")
        body = resp.text
        assert "agent_stack_workflows_total" in body
        assert "agent_stack_tasks_total" in body
        assert "agent_stack_task_duration_seconds" in body
        assert "agent_stack_active_tasks" in body
        assert "agent_stack_circuit_breaker_state" in body
        assert "agent_stack_compression_bytes" in body
        assert "agent_stack_llm_tokens_total" in body


class TestWorkflowCounter:
    def test_workflow_counter_increments(self, client):
        """Incrementing WORKFLOWS_TOTAL should be reflected in /metrics."""
        before = client.get("/metrics").text
        before_val = _extract_counter_value(before, "agent_stack_workflows_total")

        WORKFLOWS_TOTAL.inc()

        after = client.get("/metrics").text
        after_val = _extract_counter_value(after, "agent_stack_workflows_total")
        assert after_val == before_val + 1


class TestTaskCounter:
    def test_task_counter_by_platform(self, client):
        """TASKS_TOTAL should be observable by platform and status labels."""
        TASKS_TOTAL.labels(platform="browser_use", status="completed").inc()

        resp = client.get("/metrics")
        body = resp.text
        assert 'agent_stack_tasks_total{platform="browser_use",status="completed"}' in body


class TestCircuitBreakerGauge:
    def test_circuit_breaker_gauge(self, client):
        """set_circuit_breaker_state should update the gauge."""
        set_circuit_breaker_state("test_adapter", "closed")

        resp = client.get("/metrics")
        body = resp.text
        # closed = 1.0
        assert 'agent_stack_circuit_breaker_state{adapter="test_adapter"} 1.0' in body

        set_circuit_breaker_state("test_adapter", "open")
        resp = client.get("/metrics")
        body = resp.text
        # open = 0.0
        assert 'agent_stack_circuit_breaker_state{adapter="test_adapter"} 0.0' in body

    def test_circuit_breaker_half_open(self, client):
        """half_open state should map to 0.5."""
        set_circuit_breaker_state("ho_adapter", "half_open")

        resp = client.get("/metrics")
        body = resp.text
        assert 'agent_stack_circuit_breaker_state{adapter="ho_adapter"} 0.5' in body


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _extract_counter_value(text: str, metric_name: str) -> float:
    """Extract the value of an unlabelled counter from Prometheus text output."""
    for line in text.splitlines():
        if line.startswith(metric_name) and "{" not in line:
            _, _, val = line.partition(" ")
            return float(val.strip())
    return 0.0
