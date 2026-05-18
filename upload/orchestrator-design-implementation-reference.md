# Orchestrator Design & Implementation – AI-Compressed Reference

> **Purpose:** Dense, AI-optimized reference for the composable agent orchestrator (FastAPI + Redis + PostgreSQL) that coordinates Agent-S, Browser Use, and OpenHands via LiteLLM. Use for reasoning, debugging, and building the orchestration layer.

---

## Architecture Summary

```
[User]
   │
   ▼
[FastAPI Orchestrator] ─── Redis Streams ─── [Platform Adapters]
   │                      (task distribution)  ├─ Agent-S Adapter
   │  ┌─ Task Manager ─┐                      ├─ Browser Use Adapter
   │  │ PostgreSQL      │                      └─ OpenHands Adapter
   │  │ (state DB)      │
   │  └────────────────┘
   └── WebSocket Server (real-time status)
```

- **Core:** Python FastAPI (async, WebSocket, BackgroundTasks)
- **Task queue:** Redis Streams (durable, at-least-once delivery)
- **State DB:** PostgreSQL + SQLAlchemy (workflows + tasks tables)
- **LLM routing:** All calls through LiteLLM proxy at :4000
- **Communication:** REST API (sync poll) + webhooks (async callback) + Redis Streams

---

## Task Dispatch Algorithm

### Input → Output

**Input:** User request string → **Output:** DAG of subtasks with platform assignments + dependencies

### Heuristic Classification Rules

| Keyword Pattern | Platform | Confidence |
|----------------|----------|------------|
| "web", "browser", "site", "scrape", "url" | Browser Use | High |
| "desktop", "click", "app", "excel", "outlook", "spreadsheet" | Agent-S | High |
| "code", "program", "compile", "script", "function", "test" | OpenHands | High |
| Ambiguous / mixed | LLM router call (`fast` alias) | Medium |

### Decomposition Steps

1. **Keyword scan** → initial platform assignment
2. **Identify sequential vs parallel tasks** → build dependency graph
3. **Map data flow** → task A output = task B input → directed edge
4. **Attach metadata** → `task_id`, `platform`, `action_type`, `input_data`, `expected_output`, `depends_on`
5. **Fallback** → if uncertain, call LLM (`fast` alias) to classify

---

## State Machine

```
PENDING → QUEUED → RUNNING → COMPLETED
                     │            │
                     └─ retry ←───┘
                     │
                     ├── FAILED (retries exhausted)
                     └── SKIPPED (upstream failed)
```

| State | Meaning | Transition Trigger |
|-------|---------|-------------------|
| PENDING | Created, waiting for dependencies | All dependencies → COMPLETED |
| QUEUED | Dependencies met, sent to platform | Adapter acknowledges receipt |
| RUNNING | Platform executing | Adapter reports status |
| COMPLETED | Success, result available | Adapter returns result |
| FAILED | Execution failed | Retry count < 3 → back to QUEUED |
| SKIPPED | Upstream dependency failed | Any dependency → FAILED |

### Database Schema

```sql
CREATE TABLE workflows (
  id UUID PRIMARY KEY,
  user_request TEXT,
  status TEXT,           -- running/completed/failed
  created_at TIMESTAMP
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  workflow_id UUID REFERENCES workflows(id),
  platform TEXT,         -- agent_s / browser_use / openhands
  action_type TEXT,
  input JSONB,
  status TEXT,           -- pending/queued/running/completed/failed/skipped
  retries INT DEFAULT 0,
  result JSONB,
  error TEXT,
  depends_on UUID[]      -- array of task IDs
);
```

### Orchestrator Loop

1. Query tasks with status=PENDING where all `depends_on` tasks are COMPLETED → move to QUEUED
2. Dispatch QUEUED tasks to respective platform adapters
3. Poll for status (or receive webhook callback) → update RUNNING → COMPLETED/FAILED
4. On FAILED: apply error handling decision tree
5. Repeat

---

## Error Handling Decision Tree

```
Task fails:
├── retries < 3?
│   ├── YES → exponential backoff (1s, 2s, 4s) → retry same platform
│   └── NO  → alternate platform available?
│           ├── YES → reassign to alternate platform
│           └── NO  → FAILED_ESCALATE → notify human (Slack/email)
```

**Dependency cascade:** When a task fails with no retries left → mark all downstream tasks as SKIPPED → set workflow status = FAILED

**Human escalation:** Send message to configured channel with full task context (request, error, attempted fixes)

---

## API Contract (Platform Adapters)

| Action | Method | Path | Request | Response |
|--------|--------|------|---------|----------|
| Submit task | POST | `/v1/tasks` | `{action, params}` | `{task_id}` |
| Get status | GET | `/v1/tasks/{id}` | — | `{status, progress}` |
| Get result | GET | `/v1/tasks/{id}/result` | — | `{output}` |
| Cancel task | DELETE | `/v1/tasks/{id}` | — | `{status: cancelled}` |

**Async callback:** `POST /internal/callback/{task_id}` with `{status, result}`

**Communication patterns:**
- **Synchronous (poll):** Simpler, good for short tasks (<30s)
- **Asynchronous (webhook):** Better for long tasks (5-60min); platform calls orchestrator on completion
- **Message queue (Redis Streams):** Adapter publishes results; orchestrator consumes with consumer groups

---

## End-to-End Workflow: Price Monitoring

### Task Decomposition

| Step | Platform | Action | Input | Output |
|------|----------|--------|-------|--------|
| 1 | Orchestrator | Start periodic monitor | interval=30min | — |
| 2 | Browser Use | Navigate + extract price | URL, CSS selector | `price_text` |
| 3 | LLM (`fast`) | Parse price, compare with stored | `price_text`, `stored_price` | `price_changed`, `new_price` |
| 4 | Orchestrator | If changed, trigger update | — | — |
| 5 | Agent-S | Open spreadsheet, update cell | file_path, cell, new_value | confirmation |
| 6 | Agent-S | Open email client, compose, send | recipients, subject, body | sent confirmation |
| 7 | Orchestrator | Update stored price | `new_price` | — |

**Dependencies:** 5 depends on 4 (conditional). 6 depends on 5.

### Python Pseudocode

```python
import asyncio, uuid
from typing import Dict, Any, Optional

class Orchestrator:
    def __init__(self, platforms):
        self.platforms = platforms  # {name: PlatformAdapter}
        self.workflows: Dict[str, Workflow] = {}

    async def execute_workflow(self, user_request: str):
        wf_id = str(uuid.uuid4())
        wf = Workflow(wf_id, user_request)
        self.workflows[wf_id] = wf
        tasks = self.decompose(user_request)
        wf.tasks = tasks
        if wf.is_periodic:
            asyncio.create_task(self._monitoring_loop(wf))
        return wf_id

    async def _monitoring_loop(self, wf: Workflow):
        while wf.active:
            price_data = await self.run_task("browser_use", "extract_price", url="...")
            if price_data:
                change = await self.analyze_price(price_data, wf.stored_price)
                if change:
                    await self.run_task_chain(wf, [
                        {"platform": "agent_s", "action": "update_spreadsheet", ...},
                        {"platform": "agent_s", "action": "send_email", ...}
                    ])
                    wf.stored_price = change.new_price
            await asyncio.sleep(wf.interval)

    async def run_task(self, platform_name, action, **params):
        adapter = self.platforms[platform_name]
        task_id = await adapter.submit(action, params)
        result = await adapter.wait_for_completion(task_id)
        return result

    async def run_task_chain(self, wf, task_specs):
        for spec in task_specs:
            try:
                result = await self.run_task(spec["platform"], spec["action"], **spec.get("params", {}))
            except TaskFailed as e:
                self.handle_failure(wf, spec, e)
```

### LiteLLM Model Selection per Step

| Step | Alias | Reason |
|------|-------|--------|
| Price parsing | `fast` | Simple text extraction, cheap |
| Ambiguous price | `smart` | Strong reasoning, fallback from `fast` |
| Agent-S UI actions | `agent-s-smart` | Vision-capable, high priority |
| OpenHands code gen | `openhands-smart` | Complex code, batch priority |
| Workflow planning | `smart` | Accurate decomposition |

### Edge Case Handling

| Edge Case | Resolution |
|-----------|------------|
| Website down | Retry 3 times per cycle; 3 consecutive cycle failures → alert human |
| Ambiguous price | Retry with `smart` model; if still ambiguous → human review |
| Spreadsheet locked | Agent-S retries every 10s for 2min; then report failure |
| Email 2FA | Agent-S pauses + notifies human; or use TOTP secret if configured |

---

## Troubleshooting: 6 Common Integration Failures

### 1. Agent-S clicks off by 20-50px after switching LLM

| Field | Detail |
|-------|--------|
| **Root cause** | Different LLMs return coordinates in different spaces (screen-relative 0-1 vs pixel) |
| **Fix** | Standardize: always request fractional coords (0-1) → map to screen dims in Agent-S |
| **Alt fix** | Switch from coordinate clicking to UI element selectors (accessibility IDs, text labels) |
| **Calibration** | Send dummy prompt to new model, measure offset, apply correction factor |

### 2. Browser Use "element not found" on React app (30% failure)

| Field | Detail |
|-------|--------|
| **Root cause** | React re-render race condition; element momentarily absent during hydration |
| **Fix** | `waitForSelector(state='attached')` + generous timeout |
| **Alt fix** | Target stable selectors: `data-testid`, `aria-label` (not generated class names) |
| **Wait strategy** | `page.waitForLoadState('networkidle')` before interaction |
| **Retry** | 3 attempts with short delay between each |

### 3. OpenHands generates code with missing packages

| Field | Detail |
|-------|--------|
| **Root cause** | LLM doesn't know sandbox environment |
| **Fix** | Pre-install curated packages in custom Docker image |
| **Runtime fix** | Scan `import` statements after generation → `pip install` missing ones (try/except) |
| **Prompt fix** | Include "sandbox manifest" in instruction file listing available packages |

### 4. LiteLLM returns 429 despite being within rate limit

| Field | Detail |
|-------|--------|
| **Root cause** | Deployment in cooldown from transient errors, or `max_budget` exceeded |
| **Fix** | Check `litellm_deployment_state` metric (0=cooldown); adjust `allowed_fails`/`cooldown_time` |
| **Budget check** | Verify `litellm_remaining_budget`; reset or increase if hit |
| **Config check** | Ensure RPM/TPM limits are per-deployment, not per-alias |

### 5. Entire stack unresponsive after 2 hours (normal CPU/RAM)

| Field | Detail |
|-------|--------|
| **Root cause** | Resource leak (connections, file descriptors) or async deadlock (DB pool exhausted) |
| **Fix** | Check `ulimit -n`, netstat for open sockets; increase limits |
| **Debug** | `py-spy dump` on orchestrator + LiteLLM processes to find stuck points |
| **Prevention** | Set timeouts on all external calls; health checks + auto-restart stuck containers |
| **LiteLLM-specific** | Increase `database_max_connections` for PostgreSQL pool |

### 6. Silent failure: Browser Use result never reaches Agent-S

| Field | Detail |
|-------|--------|
| **Root cause** | Callback lost (network glitch, queue unacknowledged, webhook unreachable) |
| **Fix** | Use Redis Streams with consumer groups + explicit acknowledgements |
| **Timeout fallback** | Orchestrator enforces timeout; if RUNNING too long → actively query adapter status |
| **Logging** | Log all state transitions; alert if task stuck >X minutes |

---

## Performance Optimization

### Bottleneck Identification

| Layer | Measurement | Typical Bottleneck |
|-------|------------|-------------------|
| LLM inference | `response_ms` from LiteLLM metadata | Token generation (especially `smart`) |
| Browser | Platform adapter timing | Page load + DOM extraction |
| Desktop | Agent-S adapter timing | Screenshot capture + transfer |
| Sandbox | OpenHands adapter timing | Docker cold start |
| Orchestrator | Task dispatch latency | Usually negligible (<5ms) |

**Tool:** OpenTelemetry for distributed traces across FastAPI + async calls

### LLM Optimizations

| Technique | Implementation | Savings |
|-----------|---------------|---------|
| Prompt compression | LiteLLM `cache` param for identical system messages | 30-50% tokens on repeated calls |
| Model selection | Use `fast` for simple steps, `smart` only for complex | 10x cost reduction on simple tasks |
| Semantic caching | LiteLLM response caching for deterministic requests | 100% on cache hits |
| Streaming | `stream=True` for long generations | Lower time-to-first-token |

### Browser Use Optimizations

| Technique | Implementation | Savings |
|-----------|---------------|---------|
| Focused DOM extraction | `page.locator('text="Price"').bounding_box()` vs full page | 80% fewer tokens |
| Browser pool | Pre-warm Playwright contexts, reuse across sessions | 2-5s startup saved |
| Resource blocking | `page.route()` to block images/fonts/3rd-party scripts | 50% faster page load |
| Parallel extraction | `Promise.all`-style parallel locator evaluations | Linear speedup per element |

### Agent-S Optimizations

| Technique | Implementation | Savings |
|-----------|---------------|---------|
| Region-of-interest capture | `pyautogui.screenshot(region=(x,y,w,h))` vs full desktop | 70% fewer pixels/tokens |
| Batch actions | One LLM call for "click(x1,y1), type 'hello', click(x2,y2)" | 3x fewer LLM calls |
| Action caching | Cache coordinates by window position hash | Skip LLM for unchanged layouts |
| Fast OCR | Tesseract for text extraction, LLM only for semantics | 10x faster than LLM-based OCR |

### OpenHands Optimizations

| Technique | Implementation | Savings |
|-----------|---------------|---------|
| Sandbox pre-baking | Warm pool of containers with common libraries | 5-10s startup saved |
| Dependency caching | Persistent volume for pip cache | 30-60s install saved |
| Test streaming | StreamingResponse for incremental test output | Early failure detection |
| Cell-by-cell execution | Jupyter-like incremental execution | Intermediate feedback, less re-work |

---

## Monitoring & Observability

| Component | Metrics | Method |
|-----------|---------|--------|
| Orchestrator | Task dispatch latency, workflow success rate, queue depth | FastAPI middleware + PostgreSQL queries |
| Platform adapters | Call latency, error rate, retry count | httpx/aiohttp client middleware |
| LiteLLM | `response_ms`, `litellm_tokens_total`, `litellm_deployment_state` | Prometheus /metrics |
| Redis Streams | Consumer lag, stream length | `XINFO` command |
| PostgreSQL | Connection pool usage, query latency | pg_stat_statements |
| System | CPU, RAM, file descriptors per process | node_exporter / psutil |

---

## Quick Reference: Orchestrator Building Blocks

| Need | Solution |
|------|----------|
| Task queue | Redis Streams (XADD/XREADGROUP) |
| State persistence | PostgreSQL + SQLAlchemy |
| Async HTTP client | httpx (async) or aiohttp |
| WebSocket server | FastAPI `@app.websocket` |
| Background tasks | `asyncio.create_task()` or FastAPI `BackgroundTasks` |
| LLM calls from orchestrator | `litellm.completion(model="fast", messages=[...])` |
| Callback endpoint | `POST /internal/callback/{task_id}` |
| Health checks | `/health` endpoint + auto-restart on failure |
| Distributed tracing | OpenTelemetry SDK (FastAPI instrumentation) |
