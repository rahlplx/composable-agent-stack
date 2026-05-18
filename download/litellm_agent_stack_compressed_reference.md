# LiteLLM Agent Stack – AI-Compressed Reference

> **Purpose:** Dense, AI-optimized reference for LiteLLM proxy configuration serving Agent-S, Browser Use, OpenHands. Use this for reasoning, debugging, and extending the composable stack.

---

## Architecture Summary

```
Agent-S ──┐                          ┌── OpenAI (GPT-4o ×3 keys)
Browser Use ──┤── LiteLLM Proxy :4000 ──┤── Anthropic (Claude Sonnet 4)
OpenHands ──┘   (config.yaml)       ├── OpenAI (GPT-4o-mini)
                                    └── Ollama (Llama3, local)
```

- **4 model aliases:** `smart` (GPT-4o), `fast` (GPT-4o-mini), `local` (Ollama Llama3), `claude` (Claude Sonnet 4)
- **3 per-platform aliases:** `agent-s-smart`, `browser-smart`, `openhands-smart` (capacity isolation)
- **2 special-purpose aliases:** `vision-smart` (image-heavy), `text-fast` (cheap text-only)
- **Fallback cascade:** smart → claude → fast → local; claude → fast → local
- **Load balancing:** 3 OpenAI keys for `smart` alias (45/45/10 weight = 90% GPT-4o + 10% canary GPT-4o-mini)
- **Cooldown:** 3 consecutive fails → 60s removal from rotation
- **Cost control:** hard limit $1000/mo, soft warning at $800/mo
- **Logging:** metadata only (model, tokens, latency); prompt/response content disabled

---

## Config Structure (Key-Value Quick Reference)

### general_settings
| Key | Value | Notes |
|-----|-------|-------|
| master_key | `os.environ/LITELLM_MASTER_KEY` | Client auth required |
| database_url | PostgreSQL connection string | Persistent spend tracking |
| disable_master_key_return | true | Don't leak key in responses |
| turn_off_message_logging | true | Block prompt/response content logging |

### litellm_settings
| Key | Value | Notes |
|-----|-------|-------|
| max_budget | 1000.0 | Hard monthly limit (USD) |
| budget_duration | "1mo" | Reset monthly |
| soft_budget | 800.0 | Alert at 80% |
| alerting | ["slack"] | Webhook for budget alerts |
| success_callback | ["prometheus"] | Expose /metrics |
| failure_callback | ["prometheus"] | |
| set_verbose | false | Clean logs |
| custom_router | "router_callback.VisionRouter" | Vision/text auto-routing |

### router_settings
| Key | Value | Notes |
|-----|-------|-------|
| routing_strategy | "usage-based" | Distributes across deployments |
| cooldown_time | 60 | Seconds to remove failing deployment |
| allowed_fails | 3 | Consecutive failures before cooldown |
| allowed_fails_policy | "consecutive" | |
| num_retries | 0 | Rely on fallback, not retry |
| fallbacks | smart→[claude,fast,local], claude→[fast,local] | |

---

## Model Deployments (All 10 entries)

| Alias | Model | Key Env Var | RPM | Max Parallel | Weight | Input $/token | Output $/token | Special |
|-------|-------|-------------|-----|-------------|--------|--------------|----------------|---------|
| smart | openai/gpt-4o | OPENAI_API_KEY_1 | 500 | 20 | 45 | 0.00003 | 0.00006 | Load balanced |
| smart | openai/gpt-4o | OPENAI_API_KEY_2 | 500 | 20 | 45 | 0.00003 | 0.00006 | Load balanced |
| smart | openai/gpt-4o-mini | OPENAI_API_KEY_1 | 500 | 20 | 10 | 0.0000015 | 0.000006 | Canary (10%) |
| fast | openai/gpt-4o-mini | OPENAI_API_KEY_1 | 120 | 30 | - | 0.0000015 | 0.000006 | |
| local | ollama/llama3 | (api_base: ollama:11434) | 30 | 5 | - | 0.0 | 0.0 | Self-hosted |
| claude | anthropic/claude-sonnet-4-20250514 | ANTHROPIC_API_KEY | 60 | 10 | - | 0.000008 | 0.000024 | |
| agent-s-smart | openai/gpt-4o | OPENAI_API_KEY_1 | 300 | 20 | - | 0.00003 | 0.00006 | Priority: HIGH |
| browser-smart | openai/gpt-4o | OPENAI_API_KEY_2 | 200 | 10 | - | 0.00003 | 0.00006 | Priority: MED |
| openhands-smart | openai/gpt-4o | OPENAI_API_KEY_3 | 100 | 5 | - | 0.00003 | 0.00006 | Priority: LOW |
| vision-smart | openai/gpt-4o | OPENAI_API_KEY_1 | 200 | 10 | - | 0.00003 | 0.00006 | vision: true |
| text-fast | openai/gpt-4o-mini | OPENAI_API_KEY_1 | 300 | 20 | - | 0.0000015 | 0.000006 | |

**Total smart capacity:** 1500 RPM (3×500 across keys)

---

## Docker Run

```bash
docker run -d \
  --name litellm-proxy \
  -p 4000:4000 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -e LITELLM_MASTER_KEY="sk-your-master-key" \
  -e OPENAI_API_KEY_1="sk-xxx" \
  -e OPENAI_API_KEY_2="sk-yyy" \
  -e OPENAI_API_KEY_3="sk-zzz" \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e DATABASE_URL="postgresql://..." \
  ghcr.io/berriai/litellm:main-latest \
  --config /app/config.yaml
```

Proxy endpoint: `http://localhost:4000`
Health check: `GET /health`
Metrics: `GET /metrics`

---

## Vision/Text Auto-Routing (Custom Callback)

File: `router_callback.py` — mount into container, set `PYTHONPATH`.

```python
import litellm
from litellm.integrations.custom_logger import CustomLogger

class VisionRouter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data):
        messages = data.get("messages", [])
        has_image = any(
            isinstance(content, list) and any(item.get("type") == "image_url" for item in content)
            for msg in messages
            for content in ([msg["content"]] if isinstance(msg.get("content"), str) else msg.get("content", []))
        )
        if has_image:
            data["model"] = "vision-smart"
        else:
            data["model"] = "text-fast"
        return data

litellm.callbacks = [VisionRouter()]
```

Config integration: `litellm_settings.custom_router: "router_callback.VisionRouter"`

---

## Routing Strategies Summary

| Strategy | Mechanism | Config Location |
|----------|-----------|-----------------|
| Load balancing | Multiple deployments same alias, usage-based routing | `router_settings.routing_strategy: "usage-based"` + duplicate `model_name` entries |
| Fallback | Sequential provider cascade on failure | `router_settings.fallbacks` |
| Canary | Weighted traffic split across model versions | `model_info.weight` on duplicate `model_name` entries |
| Priority routing | Separate aliases per platform with different RPM/parallel limits | `agent-s-smart`, `browser-smart`, `openhands-smart` |
| Vision routing | Custom callback inspects messages for image_url | `litellm_settings.custom_router` + `router_callback.py` |
| Cooldown | Auto-remove failing deployments | `router_settings.cooldown_time` + `allowed_fails` |

---

## Prometheus Metrics

| Metric | Type | Labels | Use Case |
|--------|------|--------|----------|
| `litellm_requests_total` | Counter | model, provider, status | Request rate, error rate |
| `litellm_request_duration_seconds` | Histogram | model, provider | Latency p50/p99 |
| `litellm_tokens_total` | Counter | model, type (input/output) | Token consumption |
| `litellm_spend_metric` | Gauge/Counter | model, provider | Cost tracking |
| `litellm_remaining_budget` | Gauge | - | Budget gauge |
| `litellm_deployment_state` | Gauge | model, deployment_id | 1=healthy, 0=cooldown |

Prometheus scrape config:
```yaml
scrape_configs:
  - job_name: 'litellm'
    static_configs:
      - targets: ['localhost:4000']
```

---

## Alert Rules (PromQL)

| Alert | Expression | Threshold | For | Severity |
|-------|-----------|-----------|-----|----------|
| Daily spend > $50 | `increase(litellm_spend_metric[1d]) > 50` | $50 | 5m | warning |
| Error rate > 10% | `fail_rate / total_rate > 0.1` per model | 10% | 5m | warning |
| API key >50% RPM | `rate(reqs[1m])*60 / rate_limit > 0.5` | 50% | 0m | warning |
| p99 latency > 30s | `histogram_quantile(0.99, rate(duration_bucket[5m])) > 30` | 30s | 5m | critical |

---

## Grafana Dashboard Panels

| Panel | Type | Query | Purpose |
|-------|------|-------|---------|
| Request Rate | Timeseries | `sum(rate(litellm_requests_total[1m])) by (model)` | RPS per alias |
| Daily Spend | Stat/Bar | `increase(litellm_spend_metric[1d])` | Daily cost |
| Error Rate | Timeseries | `fail_rate/total_rate by (provider)` | % errors per provider |
| Latency | Heatmap | `rate(duration_bucket[5m])` | Latency distribution |
| Budget | Gauge | `litellm_remaining_budget` | Hard budget remaining |
| Deployments | Table | `litellm_deployment_state` | Cooldown status |

---

## Cost Control Mechanics

1. **Hard limit:** `max_budget: 1000.0` + `budget_duration: "1mo"` → HTTP 429 when exceeded
2. **Soft warning:** `soft_budget: 800.0` → alert via `alerting` callback at 80%
3. **Per-team allocation:** Create teams per agent platform with `team_spend_limit` and `team_budget_duration`
4. **Model routing:** vision→expensive model, text→cheap model (custom callback)
5. **RPM/TPM caps:** per-deployment limits prevent runaway loops

---

## Client Connection Pattern

Agents connect with:
```
Base URL: http://localhost:4000
Header: Authorization: Bearer sk-your-master-key
Model parameter: "agent-s-smart" | "browser-smart" | "openhands-smart" | "smart" | "fast" | "local" | "claude" | "vision-smart" | "text-fast"
```

---

## Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 429 on all requests | max_budget exceeded | Check `litellm_remaining_budget`, wait for cycle reset or increase limit |
| 429 on specific model | RPM limit hit | Check deployment RPM, add another key/deployment |
| Fallback not triggering | `num_retries > 0` | Set `num_retries: 0` so failures go directly to fallback |
| Canary not working | Missing `weight` on deployments | Ensure all same-alias deployments have `weight` summing to 100 |
| Vision routing inactive | Custom callback not loaded | Check `custom_router` path, `PYTHONPATH`, mount in container |
| High latency | Provider-side, not proxy | Check `litellm_request_duration_seconds` histogram; proxy overhead should be <50ms p50 |
| Spend tracking gaps | No database_url | Set PostgreSQL `database_url` for persistent tracking |
| Deployment stuck in cooldown | `allowed_fails` too low | Increase `allowed_fails` or decrease `cooldown_time` |
