"""Prometheus metrics for the Composable Agent Stack.

Exposes a ``/metrics`` endpoint on the FastAPI application using
``prometheus_client``.  All metrics are prefixed with ``agent_stack_``.

Metrics:
- agent_stack_workflows_total           — Counter
- agent_stack_tasks_total               — Counter (labels: platform, status)
- agent_stack_task_duration_seconds     — Histogram
- agent_stack_active_tasks              — Gauge
- agent_stack_circuit_breaker_state     — Gauge (1=closed, 0.5=half_open, 0=open)
- agent_stack_compression_bytes         — Gauge
- agent_stack_llm_tokens_total          — Counter
"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

# ─── Metrics Registry ────────────────────────────────────────────────────────

# Use the default global registry so collectors from across the app are
# included, but we also keep a reference so tests can reset if needed.
_REGISTRY = REGISTRY


# ─── Metric Definitions ──────────────────────────────────────────────────────

WORKFLOWS_TOTAL = Counter(
    "agent_stack_workflows_total",
    "Total number of workflows submitted",
    registry=_REGISTRY,
)

TASKS_TOTAL = Counter(
    "agent_stack_tasks_total",
    "Total number of tasks processed",
    labelnames=["platform", "status"],
    registry=_REGISTRY,
)

TASK_DURATION_SECONDS = Histogram(
    "agent_stack_task_duration_seconds",
    "Task execution duration in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=_REGISTRY,
)

ACTIVE_TASKS = Gauge(
    "agent_stack_active_tasks",
    "Number of currently active tasks",
    registry=_REGISTRY,
)

CIRCUIT_BREAKER_STATE = Gauge(
    "agent_stack_circuit_breaker_state",
    "Circuit breaker state: 1=closed, 0.5=half_open, 0=open",
    labelnames=["adapter"],
    registry=_REGISTRY,
)

COMPRESSION_BYTES = Gauge(
    "agent_stack_compression_bytes",
    "Bytes of execution memory after compression",
    registry=_REGISTRY,
)

LLM_TOKENS_TOTAL = Counter(
    "agent_stack_llm_tokens_total",
    "Total LLM tokens consumed",
    labelnames=["model"],
    registry=_REGISTRY,
)


# ─── Helper: circuit breaker state → float ───────────────────────────────────

_CIRCUIT_STATE_MAP = {
    "closed": 1.0,
    "open": 0.0,
    "half_open": 0.5,
}


def set_circuit_breaker_state(adapter_name: str, state: str) -> None:
    """Update the circuit breaker gauge for *adapter_name*.

    *state* should be one of ``"closed"``, ``"open"``, ``"half_open"``.
    """
    value = _CIRCUIT_STATE_MAP.get(state)
    if value is None:
        return
    CIRCUIT_BREAKER_STATE.labels(adapter=adapter_name).set(value)


# ─── FastAPI Integration ─────────────────────────────────────────────────────

def setup_metrics(app: FastAPI) -> None:
    """Add a ``/metrics`` endpoint to *app* that serves Prometheus metrics."""

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():  # noqa: ANN202
        content = generate_latest(_REGISTRY)
        return PlainTextResponse(content=content, media_type=CONTENT_TYPE_LATEST)
