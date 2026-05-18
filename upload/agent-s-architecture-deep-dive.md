# Browser Use – AI-Compressed Architecture Reference

> **Purpose:** Dense, AI-optimized reference for Browser Use internals, LLM integration, scaling, and production operations within the composable agent stack. Use for reasoning, debugging, and integration decisions.

---

## Architecture Summary

```
Agent (Python) ──► Browser Use API ──► Playwright ──► Chromium (headless/full)
       │                                      │
       │  page.get_element_by_prompt()        │  Real DOM + JS execution
       │  page.extract_content()              │  SPA/lazy-load support
       │                                      │
       └──► LLM (ChatLiteLLM / ChatOpenAI) ──► LiteLLM :4000 ──► Provider
```

- **Core loop:** Agent sends natural-language action → Browser Use extracts DOM state → LLM identifies target element → Playwright executes action → DOM re-extracted
- **DOM is NOT raw HTML sent to LLM** — structured prompts with element labels, indices, text snippets
- **Each element gets an index** (e.g., `[0] input "Name"`, `[1] button "Submit"`) for deterministic targeting
- **JS runs natively** — SPAs render automatically; lazy-load/infinite scroll requires explicit scroll/evaluate actions
- **Single-threaded per Agent** — one active page at a time; true parallelism needs separate Agent instances

---

## DOM Extraction Pipeline

| Step | Mechanism | API |
|------|-----------|-----|
| 1. Navigate | Playwright page.goto() | `browser.new_page(url)` |
| 2. Wait for load | Playwright auto-waits + explicit `wait` | `browser.wait()` / `page.wait_for_load_state()` |
| 3. Enumerate elements | CSS selector sweep (links, buttons, inputs, etc.) | `page.get_elements_by_css_selector()` |
| 4. Build structured state | Index + tag + text + attributes | CLI `state` command / internal representation |
| 5. Present to LLM | Structured prompt (not raw HTML) | `page.get_element_by_prompt(prompt, llm)` |

**Dynamic content flow:** scroll/evaluate → wait → re-extract state → LLM re-evaluates

---

## Element Detection & Interaction

### Natural Language → Element

```python
button = await page.get_element_by_prompt("login button", llm=llm)
await button.click()
```

- LLM receives prompt like "Which element is the login button?" + structured element list
- Returns Element handle or `None` (raises in `must_get_element_by_prompt`)
- **Multiple matches:** LLM picks first/most prominent, or returns error for refinement
- **Workaround:** Use indexed targeting (`click 3`) after `state` inspection, or more specific prompt ("the bottom submit button")

### Coordinate-Based Clicking

- Supported for **Anthropic Claude** models specifically
- LLM outputs exact `(x, y)` on viewport instead of element index
- Enables pixel-precise targeting for non-standard UI elements

### Data Extraction

```python
result = await page.extract_content(prompt="product price and title", output_model=ProductInfo, llm=llm)
```

- LLM reads page text, returns typed result matching `output_model` (Pydantic)
- Only sends relevant fields — reduces token consumption vs. dumping full page

---

## Multi-Tab Management

| Operation | API | CLI |
|-----------|-----|-----|
| Open new tab | `browser.new_page(url)` | `browser-use open <url>` |
| List tabs | `browser.get_pages()` | (implied by `state`) |
| Switch tab | `browser.get_current_page()` / page focus | `switch <index>` |
| Close tab | page.close() | `close` |

- **Each tab = separate DOM context** (isolated cookies/storage per BrowserContext)
- **Single-threaded execution** — agent works one page at a time
- **True parallelism:** Run separate `Agent` instances or separate browser processes with different `user_data_dir`
- **Memory sharing option:** `browser.new_context()` shares engine memory but isolates storage (lower overhead)

---

## LLM Integration

### Method 1: ChatLiteLLM (Recommended for LiteLLM routing)

```python
from browser_use.llm.litellm import ChatLiteLLM
llm = ChatLiteLLM(model="openai/gpt-4o")  # maps to LiteLLM alias
agent = Agent(task="...", llm=llm, browser=browser)
```

### Method 2: ChatOpenAI with custom base_url

```python
from browser_use.llm.openai.chat import ChatOpenAI
llm = ChatOpenAI(model="gpt-4o", base_url="http://localhost:4000/v1", api_key="sk-XXX")
```

### Method 3: ChatAnthropic with custom base_url

```python
from browser_use.llm.anthropic.chat import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-20250514", base_url="http://localhost:4000/v1", api_key="sk-XXX")
```

### Dynamic Model Selection (Orchestrator Pattern)

```python
# Choose alias based on task complexity
model_alias = "smart" if task.complexity > 0.7 else "fast"
llm = ChatLiteLLM(model=model_alias)
agent = Agent(task=task.description, llm=llm, browser=browser)
result = await agent.run()
```

### Fallback in Code

```python
try:
    agent.run_sync()
except Exception:
    llm.model = "fast"  # fallback alias
    agent.run_sync()
```

Or configure LiteLLM-level fallbacks in `config.yaml` (`router_settings.fallbacks`).

---

## Multi-Model Test Script

```python
import time
from browser_use import Browser, Agent
from browser_use.llm.litellm import ChatLiteLLM

def run_search(model_alias):
    llm = ChatLiteLLM(model=model_alias)
    browser = Browser()
    agent = Agent(
        task="Search Google for 'openhands ai' and get first result text",
        llm=llm, browser=browser
    )
    start = time.time()
    result = agent.run_sync()
    latency = time.time() - start
    success = "openhands" in result.lower()
    return success, latency

for alias in ["smart", "fast", "local", "claude"]:
    success, latency = run_search(alias)
    print(f"{alias} -> {'Success' if success else 'Fail'} in {latency:.1f}s")
```

---

## Token Optimization

| Problem | Solution |
|---------|----------|
| Large page exceeds context window | Use `page.extract_content()` with narrow prompt instead of full page dump |
| Local model (4K context) too small | Restrict CSS selectors to relevant sections only |
| High per-step token cost | Pre-filter with `page.evaluate()` to summarize/extract on page side |
| Monitoring token usage | LiteLLM `/v1/messages/count_tokens` or Prometheus `litellm_tokens_total` |

**Rule of thumb:** A single Browser Use step can consume thousands of tokens (page text + prompt). For 4K-context local models, always use `extract_content` with targeted fields.

---

## Anti-Detection Capabilities

| Feature | Support Level | How to Use |
|---------|--------------|------------|
| Chrome extensions | ✅ Built-in | Default loads uBlock Origin, ClearURLs |
| Real Chrome profile | ✅ Built-in | `browser-use connect` or `--profile` flag |
| Custom user agent | ✅ Built-in | `user_agent` parameter in Browser config |
| Human-like delays | ✅ Manual | `await asyncio.sleep()` or `browser.wait()` between actions |
| headless=False mode | ✅ Built-in | Set `headless=False` for full browser UI |
| WebDriver fingerprint removal | ❌ Not built-in | Use `undetected-chromedriver` or real profile mode |
| Advanced evasion | ❌ Not built-in | Use specialized stealth library separately |

**Best stealth recipe:** Use `--profile` mode (attaches to your normal Chrome with real cookies/history) + `headless=False` + popular UA string.

---

## Scaling & Resource Requirements

### Per-Session Resource Usage

| Metric | Value | Source |
|--------|-------|--------|
| RAM per session | ~2-5 GB | Empirical (2-CPU/5GB VM overloaded at ~30 steps) |
| CPU per session | High (full core during LLM+render) | Observation |
| Idle browser RAM | ~400 MB - 1 GB | Chromium base overhead |

### 10 Concurrent Sessions: Minimum Hardware

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU cores | 8 | 16+ |
| RAM | 50 GB | 100-128 GB |
| Storage | SSD (browser cache I/O) | NVMe SSD |

### Browser Instance Isolation

```python
# Isolated: each agent gets its own browser process + profile
browsers = []
for i in range(10):
    profile = BrowserProfile(user_data_dir=f"profile_dir_{i}")
    b = Browser(browser_profile=profile)
    b.start()
    browsers.append(b)
```

CLI equivalent: `browser-use --session NAME` for named sessions.

### Rate Limiting Calculation

- 10 agents × 1 request/5-10s = **1-2 RPS** sustained
- GPT-4o limit: 500 RPM = ~8.3 RPS (comfortable headroom)
- Configure in LiteLLM: `model_info.rpm: 500` per deployment key
- LiteLLM enforces via Redis-based rate limiter at proxy layer

---

## Session Persistence & Checkpointing

| Feature | Status | Workaround |
|---------|--------|------------|
| Native checkpoint/resume | ❌ Not available (cloud-only paid feature) | Custom: log last completed step + URL |
| Deterministic reruns | ❌ Cloud only | Split tasks into subtasks; repeat only last piece |
| State persistence | ❌ Not built-in | Save agent state externally (URL + step index) |

**Recommended pattern:** Split large tasks into small idempotent subtasks. On failure, only re-run the failed subtask.

---

## Monitoring

### System-Level (Browser Use doesn't emit metrics natively)

| Metric | Method | Alert Threshold |
|--------|--------|-----------------|
| CPU per browser process | node_exporter / `ps` | >80% sustained |
| RAM per browser process | node_exporter / `ps` | >4 GB per session |
| Agent exception count | Application logs | >5/minute |
| Step latency | Log start/end timestamps | >30s per step |

### LiteLLM-Level

| Metric | Prometheus Name | Use |
|--------|----------------|-----|
| In-flight requests | `litellm_in_flight_requests` | Queue depth |
| Failed requests | `litellm_proxy_failed_requests` | Error rate |
| Token usage | `litellm_tokens_total` | Cost tracking |

### Custom Instrumentation

```python
# Wrap agent.run() with timing/logging
import time, logging
logger = logging.getLogger("browser-use-agent")

async def monitored_run(agent, task_name):
    start = time.time()
    try:
        result = await agent.run()
        logger.info(f"{task_name}: success in {time.time()-start:.1f}s")
        return result
    except Exception as e:
        logger.error(f"{task_name}: failed after {time.time()-start:.1f}s - {e}")
        raise
```

---

## Quick Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| "element not found" on React app | Dynamic content not loaded | Add `await page.wait_for_load_state("networkidle")` or `browser.wait()` |
| Element found but click misses | SPA re-rendered between detect and click | Add small delay; use coordinate clicking for Claude |
| Session OOM crash | Page too heavy / memory leak | Restart browser every N steps; limit tabs per session |
| LLM returns invalid action | Model too weak for task | Switch to "smart" alias; add more specific prompt |
| 429 from LiteLLM | RPM limit exceeded | Check `litellm_remaining_api_key_requests_for_model`; add key or reduce concurrency |
| Cookies not persisting | Each run creates new profile | Use `--profile` or `BrowserProfile(user_data_dir=...)` |
| Bot detection triggered | WebDriver fingerprint visible | Use real Chrome profile mode + `headless=False` + stealth extensions |
| Token limit exceeded on local model | Page DOM too large | Use `extract_content` with narrow prompt; pre-filter with `evaluate()` |

---

## Integration with Composable Stack

| Integration Point | Method |
|-------------------|--------|
| LLM routing | `ChatLiteLLM(model="browser-smart")` → LiteLLM :4000 |
| Fallback | LiteLLM `router_settings.fallbacks` + Python exception catch |
| Priority routing | Use dedicated alias `browser-smart` (RPM 200, max_parallel 10) |
| Vision routing | Not needed (Browser Use sends structured prompts, not screenshots) |
| Cost tracking | LiteLLM `model_info.input_cost_per_token` / `output_cost_per_token` |
| Monitoring | LiteLLM Prometheus + custom agent logging |
| State sharing with Agent-S | Save extracted data to file/API → Agent-S reads |
| State sharing with OpenHands | Save scraped content → OpenHands processes in sandbox |
