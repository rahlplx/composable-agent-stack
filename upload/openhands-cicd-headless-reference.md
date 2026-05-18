# OpenHands CI/CD & Headless Operation – AI-Compressed Reference

> **Purpose:** Dense, AI-optimized reference for OpenHands headless/CLI mode, GitHub Actions integration, and production CI/CD patterns. Use for reasoning, debugging, and extending the composable agent stack.

---

## Architecture Summary

```
PR Event (label: fix-me) ──► GitHub Actions ──► Checkout + Diff ──► OpenHands Headless
                                                                    │
                                                                    ├── Reads: agent_instruction.txt
                                                                    ├── Writes: /workspace (mounted)
                                                                    ├── LLM: LiteLLM proxy (openhands-smart)
                                                                    └── Output: openhands_report.jsonl
                                                                           │
                                                                    Post-agent steps:
                                                                    ├── pytest verification
                                                                    ├── git commit + push
                                                                    └── PR comment with results
```

- **Trigger:** PR labeled `fix-me` only
- **Agent input:** PR diff + natural-language instruction file
- **Agent output:** Code modifications written directly to mounted workspace
- **Verification:** pytest runs after agent completes
- **Feedback:** PR comment with agent status + test results
- **LLM routing:** All calls through LiteLLM `openhands-smart` alias

---

## Complete GitHub Actions Workflow

### Trigger & Permissions

| Key | Value | Notes |
|-----|-------|-------|
| on | `pull_request` → types: `[labeled]` | Only fires on label events |
| Label filter | `github.event.label.name == 'fix-me'` | Job-level `if` condition |
| contents | `write` | Required for agent to commit code |
| pull-requests | `write` | Required to post review comment |
| issues | `read` | For context retrieval |

### Step 1: Checkout

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0   # Full history for AST parsing
```

### Step 2: Environment Setup & PR Context Extraction

```bash
# Fetch raw diff
curl -sH "Authorization: token ${{ secrets.GITHUB_TOKEN }}" \
     -H "Accept: application/vnd.github.v3.diff" \
     ${{ github.event.pull_request.url }} > pr_diff.patch

# Build instruction file
echo "Please analyze the following PR diff. Identify bugs, generate fixes, and write new tests using pytest." > agent_instruction.txt
echo "--- PR DIFF ---" >> agent_instruction.txt
cat pr_diff.patch >> agent_instruction.txt
```

**Pattern:** Instruction file = system prompt + context delimiter + raw diff

### Step 3: Execute OpenHands Headless

```yaml
env:
  LLM_MODEL: litellm_proxy/openhands-smart    # LiteLLM alias (batch priority)
  LLM_BASE_URL: ${{ secrets.LITELLM_PUBLIC_URL }}
  LLM_API_KEY: ${{ secrets.LITELLM_PROXY_KEY }}
  MAX_ITERATIONS: "50"        # Agent step limit
  SANDBOX_TIMEOUT: "120"      # Seconds per sandbox action
  SANDBOX_VOLUMES: ${{ github.workspace }}:/workspace:rw   # Mount workspace
```

```bash
openhands --headless --json -f agent_instruction.txt > openhands_report.jsonl
echo "AGENT_STATUS=$?" >> $GITHUB_ENV
```

**CLI flags:**
- `--headless` — no web UI, CLI-only execution
- `--json` — structured JSONL output (one JSON object per agent step)
- `-f <file>` — read task instruction from file

**Exit codes:**
- `0` = success (agent completed task)
- Non-zero = failed or max iterations reached

### Step 4: Test Suite Verification

```bash
pytest tests/ --junitxml=test_results.xml > test_output.log || true

# Package multiline output into GitHub env var
TEST_LOG=$(cat test_output.log)
echo "TEST_REPORT<<EOF" >> $GITHUB_ENV
echo "$TEST_LOG" >> $GITHUB_ENV
echo "EOF" >> $GITHUB_ENV
```

**`|| true`** ensures workflow continues even if tests fail (results captured in comment)

### Step 5: Commit Agent Modifications

```bash
git config --global user.name "OpenHands Bot"
git config --global user.email "openhands@automation.local"
git add .
git commit -m "chore: Apply OpenHands autonomous fixes"
git push origin HEAD
```

**Condition:** `if: env.AGENT_STATUS == '0'` — only commits on successful agent execution

### Step 6: Post Results to PR

Uses `peter-evans/create-or-update-comment@v4` to post:
- Agent execution status (✅/❌)
- Full pytest output in code block
- Note about committed modifications

---

## Required GitHub Secrets

| Secret | Purpose | Example Value |
|--------|---------|---------------|
| `GITHUB_TOKEN` | Auto-provided by Actions | (automatic) |
| `LITELLM_PUBLIC_URL` | LiteLLM proxy endpoint | `https://litellm.yourdomain.com` |
| `LITELLM_PROXY_KEY` | Client auth key for LiteLLM | `sk-litellm-xxx` |

---

## OpenHands Headless CLI Reference

### Basic Usage

```bash
# Single task from string
openhands --headless --task "Write a Python function to sort a list"

# Task from file
openhands --headless -f instructions.txt

# With JSON output for programmatic parsing
openhands --headless --json -f instructions.txt > report.jsonl

# With iteration limit
openhands --headless --max-iterations 30 -f instructions.txt
```

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `LLM_MODEL` | Model identifier (LiteLLM format: `litellm_proxy/<alias>`) | `litelllm_proxy/openhands-smart` |
| `LLM_BASE_URL` | LLM endpoint | `https://litellm.yourdomain.com` |
| `LLM_API_KEY` | Authentication key | `sk-litellm-xxx` |
| `MAX_ITERATIONS` | Max agent steps before stopping | `50` |
| `SANDBOX_TIMEOUT` | Per-action timeout (seconds) | `120` |
| `SANDBOX_VOLUMES` | Host:container mount for file I/O | `/path:/workspace:rw` |
| `WORKSPACE_BASE` | Base directory for agent workspace | `/workspace` |

### Output Format (JSONL)

Each line is a JSON object representing one agent step:

```json
{"action": "run", "command": "cat main.py", "observation": "...", "timestamp": "..."}
{"action": "write", "path": "fix.py", "content": "...", "observation": "File written", "timestamp": "..."}
{"action": "run", "command": "pytest tests/", "observation": "3 passed", "timestamp": "..."}
```

**Key action types:** `run` (execute command), `write` (create/modify file), `think` (internal reasoning), `finish` (task complete)

---

## Integration with LiteLLM

### Model Alias Strategy

| Alias | Use Case | RPM | Max Parallel | Why |
|-------|----------|-----|-------------|-----|
| `openhands-smart` | CI/CD code generation | 100 | 5 | Batch priority, no latency sensitivity |
| `openhands-fast` | Quick fixes, linting | 200 | 10 | Cheaper, faster for simple tasks |

### Routing Pattern

```
OpenHands → LLM_MODEL=litellm_proxy/openhands-smart
  → LiteLLM :4000
  → Maps "openhands-smart" → openai/gpt-4o (dedicated key OPENAI_API_KEY_3)
  → Fallback: openhands-smart → fast → local
```

### Streaming Validation

- **Must verify:** Token streaming works through LiteLLM proxy
- **Web UI mode:** Tokens should appear incrementally in browser
- **Headless mode:** Streaming less critical (batch output), but verify `--json` mode captures all steps
- **Test:** Run headless with verbose logging, check no truncated responses in JSONL

---

## Advanced CI/CD Patterns

### Pattern 1: Multi-Stage Agent Pipeline

```yaml
jobs:
  analyze:
    # Agent reads PR, produces analysis report
    # Output: analysis.md

  fix:
    needs: analyze
    # Agent reads analysis.md, produces code fixes
    # Output: modified source files

  test:
    needs: fix
    # Run full test suite on agent-modified code
    # Output: test_results.xml

  report:
    needs: test
    # Post combined analysis + test report to PR
```

### Pattern 2: Branch Protection Bypass

For automated commits, the bot needs to bypass branch protection:

1. Create a GitHub App (not a PAT) with repo permissions
2. Install the App on the repository
3. Configure branch protection to allow the App as a bypass actor
4. Use App installation token instead of GITHUB_TOKEN

### Pattern 3: Conditional Model Selection

```yaml
env:
  LLM_MODEL: ${{ contains(github.event.pull_request.labels.*.name, 'complex-fix') && 'litellm_proxy/openhands-smart' || 'litellm_proxy/fast' }}
```

Routes complex tasks to GPT-4o, simple tasks to GPT-4o-mini.

### Pattern 4: Timeout Safety

```yaml
- name: Execute OpenHands with Timeout
  timeout-minutes: 15  # Hard GitHub Actions timeout
  run: |
    timeout 800 openhands --headless -f agent_instruction.txt || true
    # 800s = 13min, leaving 2min buffer before GitHub kills the job
```

### Pattern 5: Pass PR Context via CLI

```bash
# Build rich instruction with multiple context sources
{
  echo "## Task: Fix the issues in this PR"
  echo ""
  echo "### PR Title: ${{ github.event.pull_request.title }}"
  echo "### PR Description:"
  echo "${{ github.event.pull_request.body }}"
  echo ""
  echo "### PR Diff:"
  cat pr_diff.patch
  echo ""
  echo "### Related Issue:"
  curl -sH "Authorization: token $GITHUB_TOKEN" \
    "${{ github.event.pull_request.issue_url }}" | jq -r '.body'
} > agent_instruction.txt
```

---

## Custom Sandbox Configuration

### Dockerfile for Custom Sandbox

```dockerfile
FROM ghcr.io/all-hands-ai/openhands:latest

# Add Python 3.12
RUN add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && apt-get install -y python3.12 python3.12-venv python3.12-dev

# Add Node.js 20
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Add Go 1.22
RUN curl -fsSL https://go.dev/dl/go1.22.0.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:$PATH"

# Pre-install common dev tools
RUN pip install pytest black ruff mypy httpx fastapi
RUN npm install -g typescript eslint prettier
```

### Docker Compose Integration

```yaml
services:
  openhands:
    image: ghcr.io/all-hands-ai/openhands:latest
    environment:
      - LLM_MODEL=litellm_proxy/openhands-smart
      - LLM_BASE_URL=http://litellm:4000
      - LLM_API_KEY=${LITELLM_MASTER_KEY}
    volumes:
      - ./workspace:/workspace:rw
    depends_on:
      - litellm

  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    ports:
      - "4000:4000"
    volumes:
      - ./config.yaml:/app/config.yaml
    command: --config /app/config.yaml
```

---

## Monitoring & Observability

### Agent Execution Metrics

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Agent success rate | `AGENT_STATUS` in workflow runs | <80% over 10 runs |
| Time to completion | GitHub Actions job duration | >15 minutes |
| Test pass rate after fix | pytest output parsing | <70% |
| LLM token consumption | LiteLLM `litellm_tokens_total{model="openhands-smart"}` | Trending up unexpectedly |

### Workflow Run Tracking

```yaml
- name: Report Metrics
  if: always()
  run: |
    curl -X POST https://your-metrics-endpoint/ingest \
      -H "Content-Type: application/json" \
      -d '{
        "workflow": "openhands-remediation",
        "status": "'"${AGENT_STATUS:-unknown}"'",
        "run_id": "${{ github.run_id }}",
        "pr_number": "${{ github.event.pull_request.number }}"
      }'
```

---

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| Agent writes malicious code | Sandbox isolation (Docker); `SANDBOX_VOLUMES` limits mount scope |
| Agent reads secrets in repo | Use `GITHUB_TOKEN` (scoped); never mount `.git/secrets` |
| Push malicious commits | Branch protection + required reviews on agent commits |
| LLM prompt injection via PR diff | Sanitize diff before including in instruction file |
| Rate limit abuse | LiteLLM RPM limits on `openhands-smart` alias |
| Unbounded agent execution | `MAX_ITERATIONS: 50` + `timeout-minutes: 15` hard cap |

---

## Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Agent exits immediately | LLM_MODEL not recognized | Check format: `litellm_proxy/<alias>` matches LiteLLM config |
| No files modified | `SANDBOX_VOLUMES` not set or wrong path | Verify mount: `${{ github.workspace }}:/workspace:rw` |
| Agent times out | `MAX_ITERATIONS` too low or complex task | Increase to 100; add `timeout-minutes` to step |
| Tests not found | Wrong working directory in sandbox | Use `WORKSPACE_BASE=/workspace` env var |
| Push fails | `contents: write` permission missing | Add to workflow `permissions` block |
| PR comment empty | `TEST_REPORT` env var not set | Check multiline EOF heredoc syntax |
| LiteLLM 429 | `openhands-smart` RPM limit hit | Add another API key deployment or increase RPM |
| Agent loops infinitely | No `MAX_ITERATIONS` set | Always set iteration limit in production |
| JSONL output empty | Agent crashed before first action | Check Docker logs; simplify instruction file |

---

## Composable Stack Integration Map

| Integration Point | Method |
|-------------------|--------|
| LLM routing | `LLM_MODEL=litellm_proxy/openhands-smart` → LiteLLM :4000 |
| Fallback | LiteLLM `router_settings.fallbacks`: openhands-smart → fast → local |
| Priority | Dedicated alias with RPM 100, max_parallel 5 (batch, lower priority) |
| Cost tracking | LiteLLM `model_info.input_cost_per_token` per deployment |
| Output to Agent-S | Commit files → Agent-S workspace reads them |
| Output to Browser Use | Commit scraper code → Browser Use deploys and runs it |
| Monitoring | LiteLLM Prometheus + GitHub Actions run metrics |
| State persistence | Git commits as implicit checkpoints (each run = commit) |
