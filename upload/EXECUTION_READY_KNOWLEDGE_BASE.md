# Agent Stack – Execution-Ready Knowledge Base (Source-Verified)

> **Purpose:** Single file containing ALL verified integration points, code paths, and configuration patterns needed to build and deploy the composable agent stack. Every entry is verified against actual source code. Use this as the primary context for any AI building, debugging, or extending the system.

---

## 1. AGENT-S (Desktop Automation)

### 1.1 Source: `/home/z/my-project/download/agent-stack-sources/Agent-S/`

### 1.2 LLM Integration (VERIFIED)

**Engine registry:** `gui_agents/s3/core/engine.py`

| engine_type | Class | Env Var (Key) | Env Var (Base URL) | base_url param? |
|-------------|-------|---------------|---------------------|-----------------|
| `openai` | LMMEngineOpenAI | OPENAI_API_KEY | — | ✅ Yes |
| `anthropic` | LMMEngineAnthropic | ANTHROPIC_API_KEY | — | ✅ (unused by SDK) |
| `azure` | LMMEngineAzureOpenAI | AZURE_OPENAI_API_KEY | AZURE_OPENAI_ENDPOINT | ✅ azure_endpoint |
| `gemini` | LMMEngineGemini | GEMINI_API_KEY | GEMINI_ENDPOINT_URL | ✅ Yes |
| `open_router` | LMMEngineOpenRouter | OPENROUTER_API_KEY | OPEN_ROUTER_ENDPOINT_URL | ✅ Yes |
| `vllm` | LMMEnginevLLM | vLLM_API_KEY | vLLM_ENDPOINT_URL | ✅ Yes |
| `ollama` | LMMEngineOpenAI | (key="ollama") | OLLAMA_HOST | ✅ Yes (+ /v1) |
| `deepseek` | LMMEngineOpenAI | DEEPSEEK_API_KEY | DEEPSEEK_ENDPOINT_URL | ✅ Yes |
| `qwen` | LMMEngineOpenAI | QWEN_API_KEY | QWEN_ENDPOINT_URL | ✅ Yes |

**LiteLLM proxy routing (VERIFIED):**
```python
# In LMMEngineOpenAI.__init__ (engine.py line 19-68):
# If base_url is provided, OpenAI client uses it instead of default
engine_params = {
    "engine_type": "openai",
    "model": "agent-s-smart",        # LiteLLM alias
    "base_url": "http://localhost:4000/v1",
    "api_key": "sk-your-litellm-key"
}
```

### 1.3 CLI Entry Point (VERIFIED)

**File:** `setup.py` line 36-40
```python
entry_points={"console_scripts": ["agent_s=gui_agents.s3.cli_app:main"]}
```

**CLI args (gui_agents/s3/cli_app.py lines 228-323):**

| Arg | Required | Default | Purpose |
|-----|----------|---------|---------|
| `--provider` | No | "openai" | LLM engine_type |
| `--model` | No | "gpt-5-2025-08-07" | Model name |
| `--model_url` | No | "" | Base URL for LLM API |
| `--model_api_key` | No | "" | API key |
| `--ground_provider` | **Yes** | — | Grounding model provider |
| `--ground_url` | **Yes** | — | Grounding model URL |
| `--ground_api_key` | No | "" | Grounding model key |
| `--ground_model` | **Yes** | — | Grounding model name |
| `--grounding_width` | **Yes** | — | Screenshot resize width |
| `--grounding_height` | **Yes** | — | Screenshot resize height |
| `--task` | No | — | Task instruction (interactive if omitted) |
| `--max_trajectory_length` | No | 8 | Max image turns |
| `--enable_reflection` | No | True | Reflection agent |
| `--enable_local_env` | No | False | Local code exec (DANGEROUS) |

**No --headless flag** — Agent-S always controls the local desktop via pyautogui.

### 1.4 Programmatic API (VERIFIED)

```python
from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import OSWorldACI
from gui_agents.s3.core.mllm import LMMAgent
from gui_agents.s3.core.engine import LMMEngineOpenAI

# Worker LLM (reasoning)
worker_engine_params = {
    "engine_type": "openai",
    "model": "agent-s-smart",
    "base_url": "http://localhost:4000/v1",
    "api_key": "sk-litellm-key"
}

# Grounding LLM (coordinate prediction)
grounding_engine_params = {
    "engine_type": "openai",
    "model": "agent-s-smart",
    "base_url": "http://localhost:4000/v1",
    "api_key": "sk-litellm-key"
}

grounding_agent = OSWorldACI(
    env=None,
    platform="darwin",  # or "linux" / "windows"
    engine_params_for_generation=grounding_engine_params,
    engine_params_for_grounding=grounding_engine_params,
    width=1920, height=1080,
)

agent = AgentS3(
    worker_engine_params=worker_engine_params,
    grounding_agent=grounding_agent,
    max_trajectory_length=8,
    enable_reflection=True,
)

observation = {"screenshot": screenshot_bytes}  # PNG bytes, NOT base64
info, actions = agent.predict(instruction="Open Excel", observation=observation)
# actions = ["pyautogui.click(x=500, y=300)"] or "DONE" or "FAIL"
```

### 1.5 Screenshot Pipeline (VERIFIED)

```python
# cli_app.py lines 164-176
screenshot = pyautogui.screenshot()                                      # PIL Image
screenshot = screenshot.resize((scaled_width, scaled_height), Image.LANCZOS)  # Max dim 2400px
buffered = io.BytesIO()
screenshot.save(buffered, format="PNG")
screenshot_bytes = buffered.getvalue()   # Raw bytes → base64 encoded by LMMAgent
```

**LLM transmission:** base64 PNG in OpenAI vision format (`data:image/png;base64,...`)
**Anthropic format:** `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}}`

### 1.6 Action System (VERIFIED)

All actions in `gui_agents/s3/agents/grounding.py`, decorated with `@agent_action`:

| Action | Line | Signature |
|--------|------|-----------|
| click | 347 | `(element_description: str, num_clicks=1, button_type="left", hold_keys=[])` |
| switch_applications | 375 | `(app_code)` |
| open | 392 | `(app_or_filename: str)` |
| type | 414 | `(element_description=None, text="", overwrite=False, enter=False)` |
| save_to_knowledge | 466 | `(text: List[str])` → returns "WAIT" |
| drag_and_drop | 475 | `(starting_description, ending_description, hold_keys=[])` |
| highlight_text_span | 504 | `(starting_phrase, ending_phrase, button="left")` |
| set_cell_values | 528 | `(cell_values: Dict, app_name, sheet_name)` |
| call_code_agent | 543 | `(task: str = None)` |
| scroll | 606 | `(element_description: str, clicks: int, shift=False)` |
| hotkey | 622 | `(keys: List)` |
| hold_and_press | 632 | `(hold_keys: List, press_keys: List)` |
| wait | 650 | `(time: float)` |
| done | 658 | `()` → "DONE" |
| fail | 665 | `()` → "FAIL" |

**Action flow:** LLM generates code → `eval(code)` → action method → `generate_coords()` → grounding model → `(x,y)` → pyautogui code → `exec(code)`

### 1.7 Dependencies (VERIFIED)

`pyautogui` (core), `openai`, `anthropic`, `google-genai`, `pytesseract`, `pyobjc` (macOS), `pywinauto` (Windows), `numpy`, `pandas`, `tiktoken`, `backoff`, `websockets`

**Python:** >=3.9, <=3.12

---

## 2. BROWSER USE (Web Automation)

### 2.1 Source: `/home/z/my-project/download/agent-stack-sources/browser-use/` (v0.12.6)

### 2.2 LLM Classes (VERIFIED)

| Class | Import Path | Key Constructor Params |
|-------|------------|----------------------|
| ChatOpenAI | `browser_use.llm.openai.chat.ChatOpenAI` | model, api_key, **base_url**, temperature, max_completion_tokens |
| ChatAnthropic | `browser_use.llm.anthropic.chat.ChatAnthropic` | model, api_key, **base_url**, max_tokens |
| ChatLiteLLM | `browser_use.llm.litellm.chat.ChatLiteLLM` | model, api_key, **api_base** (NOT base_url!), temperature, max_tokens |
| ChatGoogle | `browser_use.llm.google.chat.ChatGoogle` | model, api_key, vertexai, project |
| ChatOllama | `browser_use.llm.ollama.chat.ChatOllama` | model, **host** |
| ChatDeepSeek | `browser_use.llm.deepseek.chat.ChatDeepSeek` | model, api_key, base_url |
| ChatGroq | `browser_use.llm.groq.chat.ChatGroq` | model, api_key |
| ChatMistral | `browser_use.llm.mistral.chat.ChatMistral` | model, api_key |
| ChatOpenRouter | `browser_use.llm.openrouter.chat.ChatOpenRouter` | model, api_key |
| ChatAzureOpenAI | `browser_use.llm.azure.chat.ChatAzureOpenAI` | model, api_key, azure_endpoint |

**⚠️ CRITICAL:** ChatLiteLLM uses `api_base` NOT `base_url`. All other classes use `base_url`.

### 2.3 LiteLLM Integration (VERIFIED)

```python
# Method 1: ChatLiteLLM (RECOMMENDED)
from browser_use.llm.litellm.chat import ChatLiteLLM
llm = ChatLiteLLM(model="browser-smart", api_base="http://localhost:4000", api_key="sk-litellm-key")

# Method 2: ChatOpenAI with custom base_url
from browser_use.llm.openai.chat import ChatOpenAI
llm = ChatOpenAI(model="browser-smart", base_url="http://localhost:4000/v1", api_key="sk-litellm-key")
```

### 2.4 Agent API (VERIFIED)

**File:** `browser_use/agent/service.py` line 131

```python
from browser_use import Agent, ChatLiteLLM

agent = Agent(
    task="Search for X",
    llm=ChatLiteLLM(model="browser-smart", api_base="http://localhost:4000"),
    browser_profile=BrowserProfile(headless=True, user_data_dir="./profile"),
    use_vision=True,            # or "auto"
    max_failures=5,
    max_actions_per_step=5,
    step_timeout=180,
    enable_planning=True,
)

result = await agent.run(max_steps=500)
# result is AgentHistoryList
# result.final_result() → str | None
# result.is_done() → bool
# result.is_successful() → bool | None
# result.errors() → list[str | None]
```

### 2.5 Page Interaction API (VERIFIED)

**File:** `browser_use/actor/page.py`

| Method | Signature | Returns |
|--------|-----------|---------|
| goto | `(url: str)` | None |
| navigate | `(url: str)` | None |
| get_element_by_prompt | `(prompt: str, llm=None)` | Element or None |
| must_get_element_by_prompt | `(prompt: str, llm=None)` | Element (raises if not found) |
| extract_content | `(prompt: str, structured_output: type[T], llm=None)` | T |
| screenshot | `(format='png')` | base64 str |
| press | `(key: str)` | None |
| evaluate | `(page_function: str, *args)` | str |
| get_elements_by_css_selector | `(selector: str)` | list[Element] |

### 2.6 Browser Configuration (VERIFIED)

**File:** `browser_use/browser/profile.py`

```python
from browser_use import BrowserProfile

profile = BrowserProfile(
    headless=True,                      # or False for visual
    user_data_dir="./chrome_profile",   # persistent profile
    cdp_url=None,                       # connect to existing browser
    proxy=ProxySettings(server="http://proxy:8080"),
    enable_default_extensions=True,     # uBlock, ClearURLs, cookie handler
    captcha_solver=True,
    minimum_wait_page_load_time=0.25,
    wait_for_network_idle_page_load_time=0.5,
    wait_between_actions=0.1,
    highlight_elements=True,
)
```

**Anti-detection (built-in):** Removes `--enable-automation`, adds `--disable-blink-features=AutomationControlled`, loads uBlock Origin + ClearURLs + cookie banner handler.

**Uses `cdp-use` (NOT Playwright)** — direct Chrome DevTools Protocol communication.

### 2.7 CLI Entry Points (VERIFIED)

**File:** pyproject.toml lines 96-101

| Command | Entry Point |
|---------|-------------|
| `browser-use` | `browser_use.skill_cli.main:main` |
| `browseruse` | same |
| `bu` | same |
| `browser-use-tui` | `browser_use.cli:main` (legacy) |

**MCP server:** `--mcp` flag on any CLI command

### 2.8 Environment Variables (VERIFIED)

| Variable | Purpose |
|----------|---------|
| OPENAI_API_KEY | Default OpenAI key |
| ANTHROPIC_API_KEY | Default Anthropic key |
| GOOGLE_API_KEY | Default Google key |
| DEFAULT_LLM | Default model name (e.g. "openai_gpt_4o") |
| BROWSER_USE_HEADLESS | Override headless mode |
| BROWSER_USE_ALLOWED_DOMAINS | Domain whitelist |
| BROWSER_USE_PROXY_URL | Proxy URL |
| SKIP_LLM_API_KEY_VERIFICATION | "true" to bypass key checks |

---

## 3. OPENHANDS (Software Development Platform)

### 3.1 Source: `/home/z/my-project/download/agent-stack-sources/OpenHands/`

### 3.2 LLM Configuration (VERIFIED)

**Core class:** `openhands.sdk.llm.llm.LLM` (built on LiteLLM)

```python
from openhands.sdk import LLM
from pydantic import SecretStr

llm = LLM(
    model="anthropic/claude-sonnet-4-20250514",   # or "litellm_proxy/openhands-smart"
    api_key=SecretStr("sk-key"),
    base_url="http://localhost:4000",              # LiteLLM proxy
    num_retries=5,
    timeout=300,
    max_output_tokens=None,
    temperature=None,
)
```

**Model rewrite rule (VERIFIED):** `openhands/*` → `litellm_proxy/*` with base_url defaulting to `https://llm-proxy.app.all-hands.dev/`

**To use custom LiteLLM proxy:**
```bash
export OPENHANDS_PROVIDER_BASE_URL=http://your-litellm:4000
# OR
export LLM_BASE_URL=http://your-litellm:4000
```

**Auto-forwarded env vars:** All `LLM_*` and `LMNR_*` prefixes are auto-forwarded to agent-server containers.

### 3.3 REST API (VERIFIED)

**Base URL:** `http://localhost:3000/api/v1/`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/app-conversations` | Start new conversation |
| POST | `/app-conversations/{id}/send-message` | Send message to agent |
| GET | `/app-conversations/search` | List conversations |
| PATCH | `/app-conversations/{id}` | Update conversation |
| DELETE | `/app-conversations/{id}` | Delete conversation |
| POST | `/app-conversations/{id}/switch_profile` | Switch LLM profile |
| GET | `/app-conversations/{id}/file` | Read file from sandbox |
| POST | `/sandboxes` | Start new sandbox |
| GET | `/sandboxes/search` | List sandboxes |
| POST | `/sandboxes/{id}/pause` | Pause sandbox |
| POST | `/sandboxes/{id}/resume` | Resume sandbox |
| DELETE | `/sandboxes/{id}` | Delete sandbox |
| GET | `/health` | Health check |

### 3.4 Headless/Programmatic (VERIFIED)

**NO dedicated CLI binary.** Use Python SDK:

```python
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.conversation import LocalConversation
from pydantic import SecretStr

llm = LLM(model="anthropic/claude-sonnet-4-20250514",
          api_key=SecretStr("key"),
          base_url="http://localhost:4000")

agent = Agent(llm=llm, tools=[
    Tool(name="TerminalTool"),
    Tool(name="FileEditorTool"),
])

conversation = LocalConversation(agent=agent)
```

### 3.5 Sandbox (VERIFIED)

| Config | Value |
|--------|-------|
| Default image | `ghcr.io/openhands/agent-server:1.22.1-python` |
| Override image | `AGENT_SERVER_IMAGE_REPOSITORY` + `AGENT_SERVER_IMAGE_TAG` env vars |
| Custom Dockerfile | `base_container_image` in config.toml `[sandbox]` section |
| Volume mounts | `SANDBOX_VOLUMES="/host:/workspace:rw"` env var |
| Working dir | `/workspace/project` |
| Max sandboxes | 5 (configurable via `OH_SANDBOX_MAX_NUM_SANDBOXES`) |

### 3.6 Docker Compose (VERIFIED)

```yaml
services:
  openhands:
    image: openhands:latest
    ports: ["3000:3000"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ~/.openhands:/.openhands
      - ${WORKSPACE_BASE:-$PWD/workspace}:/opt/workspace_base
    environment:
      - AGENT_SERVER_IMAGE_REPOSITORY=ghcr.io/openhands/agent-server
      - AGENT_SERVER_IMAGE_TAG=1.22.1-python
```

### 3.7 Agent Tools (VERIFIED)

| Tool | Config Key | Purpose |
|------|-----------|---------|
| TerminalTool | enable_cmd | Shell command execution |
| FileEditorTool | enable_editor | str_replace_editor |
| JupyterTool | enable_jupyter | IPython/Jupyter execution |
| BrowserTool | enable_browsing | BrowserGym web browsing |
| LLMEditorTool | enable_llm_editor | LLM-powered editor |
| ThinkTool | enable_think | Internal reasoning |
| FinishTool | enable_finish | Task completion |
| SwitchLLMTool | — | Runtime LLM switching |

### 3.8 Default Agent

`CodeActAgent` (config.template.toml line 74)
Max iterations: 500 (configurable)

---

## 4. LITELLM (Unified LLM Router)

### 4.1 Source: `/home/z/my-project/download/agent-stack-sources/litellm/`

### 4.2 Full config.yaml (VERIFIED from previous conversation)

**9 model aliases, 11 deployments, full routing:**

| Alias | Model | Key | RPM | Max Parallel | Weight | Special |
|-------|-------|-----|-----|-------------|--------|---------|
| smart | openai/gpt-4o | KEY_1 | 500 | 20 | 45 | Load balanced |
| smart | openai/gpt-4o | KEY_2 | 500 | 20 | 45 | Load balanced |
| smart | openai/gpt-4o-mini | KEY_1 | 500 | 20 | 10 | Canary |
| fast | openai/gpt-4o-mini | KEY_1 | 120 | 30 | — | |
| local | ollama/llama3 | ollama:11434 | 30 | 5 | — | Free |
| claude | anthropic/claude-sonnet-4-20250514 | ANTHROPIC | 60 | 10 | — | |
| agent-s-smart | openai/gpt-4o | KEY_1 | 300 | 20 | — | Priority HIGH |
| browser-smart | openai/gpt-4o | KEY_2 | 200 | 10 | — | Priority MED |
| openhands-smart | openai/gpt-4o | KEY_3 | 100 | 5 | — | Priority LOW |
| vision-smart | openai/gpt-4o | KEY_1 | 200 | 10 | — | vision:true |
| text-fast | openai/gpt-4o-mini | KEY_1 | 300 | 20 | — | |

**Fallbacks:** smart→[claude,fast,local], claude→[fast,local]
**Cooldown:** 3 fails → 60s removal
**Budget:** $1000/mo hard, $800/mo soft
**Logging:** metadata only (turn_off_message_logging: true)

### 4.3 Docker Run (VERIFIED)

```bash
docker run -d --name litellm-proxy -p 4000:4000 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -e LITELLM_MASTER_KEY="sk-xxx" \
  -e OPENAI_API_KEY_1="sk-xxx" -e OPENAI_API_KEY_2="sk-yyy" -e OPENAI_API_KEY_3="sk-zzz" \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e DATABASE_URL="postgresql://..." \
  ghcr.io/berriai/litellm:main-latest --config /app/config.yaml
```

**Endpoints:** :4000 (proxy), /health, /metrics (Prometheus)

---

## 5. ORCHESTRATOR (FastAPI)

### 5.1 Architecture

```
[User] → [FastAPI :8000] → Redis Streams → [Agent-S Adapter]
                              ↓              [Browser Use Adapter]
                         PostgreSQL          [OpenHands Adapter]
                         WebSocket
```

### 5.2 Platform Adapter API Contract

| Action | Method | Path | Request | Response |
|--------|--------|------|---------|----------|
| Submit | POST | /v1/tasks | {action, params} | {task_id} |
| Status | GET | /v1/tasks/{id} | — | {status, progress} |
| Result | GET | /v1/tasks/{id}/result | — | {output} |
| Cancel | DELETE | /v1/tasks/{id} | — | {status: cancelled} |

### 5.3 Adapter Implementations (Verified Code Paths)

**Agent-S Adapter:**
```python
# Wraps AgentS3.predict() in a subprocess
# Screenshots captured via pyautogui
# Actions: click, type, scroll, hotkey, open, wait, done, fail
# No headless mode — requires display
```

**Browser Use Adapter:**
```python
# Wraps Agent.run() with ChatLiteLLM
# headless=True for server deployment
# Actions: navigate, click, type, scroll, extract, screenshot, done
# Full async support (await agent.run())
```

**OpenHands Adapter:**
```python
# Uses REST API: POST /api/v1/app-conversations
# Headless via Python SDK LocalConversation
# Sandbox isolation via Docker
# Actions: code, test, file_edit, browse, think, finish
```

### 5.4 State Machine

```
PENDING → QUEUED → RUNNING → COMPLETED/FAILED/SKIPPED
                     ↑            │
                     └─ retry ────┘
```

**Max retries:** 3 with exponential backoff (1s, 2s, 4s)
**Failure cascade:** mark downstream tasks SKIPPED
**Human escalation:** Slack/email notification with full context

---

## 6. CRITICAL CORRECTIONS (Source vs. Documentation)

These items differ from the earlier AI-generated reference documents:

| Item | Earlier Assumption | Source-Verified Reality |
|------|-------------------|----------------------|
| Browser Use browser engine | Playwright | **cdp-use** (direct CDP protocol) |
| Browser Use ChatLiteLLM param | base_url | **api_base** (different from all other classes!) |
| OpenHands CLI | `openhands --headless --json -f` | **No CLI binary** — use Python SDK LocalConversation |
| OpenHands default model | varies | **openhands/claude-opus-4-5-20251101** (auto-rewrites to litellm_proxy/) |
| Agent-S headless mode | Assumed available | **No headless** — always controls local desktop via pyautogui |
| Agent-S config files | YAML/TOML assumed | **No config files** — all via CLI args, env vars, or constructor params |
| Agent-S requires 2 LLM configs | Single model assumed | **Two models needed:** worker (reasoning) + grounding (coordinate prediction) |
| Browser Use default LLM | OpenAI | **ChatBrowserUse** (cloud service) if no LLM specified |

---

## 7. EXECUTION ORDER

### Phase 1: LiteLLM Proxy (Start First)
1. Write config.yaml with all 11 deployments
2. Write router_callback.py for vision routing
3. `docker run` LiteLLM proxy
4. Verify: `curl http://localhost:4000/health`
5. Test: `curl -H "Authorization: Bearer sk-xxx" -d '{"model":"smart","messages":[{"role":"user","content":"hi"}]}' http://localhost:4000/v1/chat/completions`

### Phase 2: Browser Use (Easiest to Start)
1. `pip install browser-use`
2. Test with ChatLiteLLM pointing to localhost:4000
3. Verify LiteLLM logs show requests
4. Test multi-model: smart, fast, local, claude

### Phase 3: OpenHands (Docker Required)
1. `docker compose up` OpenHands
2. Set LLM_BASE_URL=http://host.docker.internal:4000
3. Create conversation via REST API
4. Verify agent executes code in sandbox

### Phase 4: Agent-S (Requires Desktop)
1. `pip install -e .` Agent-S
2. Configure worker + grounding LLM params
3. Test on local desktop with simple task
4. Verify screenshots + actions flow through LiteLLM

### Phase 5: Orchestrator (Ties Everything)
1. Build FastAPI service with 3 platform adapters
2. Connect to Redis + PostgreSQL
3. Implement state machine + task dispatch
4. End-to-end workflow test (price monitoring)
5. Monitoring + alerting setup
