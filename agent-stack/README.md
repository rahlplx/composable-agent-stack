# Composable Agent Stack

> Orchestrator for composable AI agents: Agent-S + Browser Use + OpenHands via LiteLLM

## Architecture

- **SQLite Compression Manager** — Z.ai's execution memory with priority-based auto-compact
- **6-State Machine** — PENDING→QUEUED→RUNNING→COMPLETED/FAILED/SKIPPED
- **Task Dispatcher** — Keyword heuristics + LLM fallback with confidence scoring
- **Platform Adapters** — Unified async interface for Agent-S, Browser Use, OpenHands
- **Circuit Breaker** — Protects adapters from cascading failures (closed→open→half-open)
- **Prometheus Metrics** — 7 gauges/counters on /metrics
- **FastAPI Orchestrator** — 11 REST endpoints + WebSocket
- **Redis Streams** — Durable task distribution with consumer groups and DLQ
- **LiteLLM Proxy** — 11 model deployments with fallback routing
- **RL Test Harness** — Reinforcement learning-based test prioritization

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run tests (216 tests)
python -m pytest tests/ -v

# Start the stack
docker compose up -d

# Submit a workflow
curl -X POST http://localhost:8080/v1/workflows \
  -H "Content-Type: application/json" \
  -d '{"user_request": "scrape the price from example.com"}'
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /v1/workflows | Submit workflow |
| GET | /v1/workflows | List workflows |
| GET | /v1/workflows/{id} | Get workflow status |
| GET | /v1/tasks/{id} | Get task status |
| DELETE | /v1/tasks/{id} | Cancel task |
| POST | /v1/compact/{session} | Trigger /compact on execution memory |
| GET | /v1/snapshot | Get execution memory snapshot |
| GET | /v1/memory/context | Retrieve context entries |
| POST | /v1/memory/store | Store context entry |
| WS | /ws | Real-time status updates |
| GET | /health | Health check |
| GET | /metrics | Prometheus metrics |

## Test Suite

216 tests across 8 categories:

| Category | Count | Tool |
|----------|-------|------|
| Unit (Compression) | 31 | pytest |
| Unit (State Machine) | 37 | pytest |
| Unit (Integration) | 9 | pytest |
| Unit (Circuit Breaker) | 17 | pytest |
| Unit (Metrics) | 7 | pytest |
| Unit (API) | 7 | pytest |
| Contract | 30 | Parameterized |
| Property | 11 | Hypothesis |
| Performance | 5 | Benchmarks |
| Chaos | 15 | Fault injection |
| Security | 6 | Validation |
| RL Optimizer | 13 | Learning |

## Docker Stack

| Service | Port | Purpose |
|---------|------|---------|
| orchestrator | 8080 | FastAPI app |
| litellm | 4000 | LLM proxy |
| redis | 6379 | Task queue |
| postgres | 5432 | Persistence |
| ollama | 11434 | Local LLM |

## License

MIT
