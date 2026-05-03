# genesis-router

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-passing-green.svg)]()

**Self-healing LLM routing. No manual intervention required.**

When a provider goes down, genesis-router detects it, routes around it, and recovers automatically when it comes back. You find out what happened from the event log — not from a crash at 3am.

Extracted from [GENesis-AGI](https://github.com/WingedGuardian/GENesis-AGI) — an autonomous cognitive architecture that routes ~40 heterogeneous LLM call sites across 15+ providers without human intervention. This is the standalone routing engine.

---

## Why This Exists

Most LLM routers are glorified try/except blocks. Provider goes down? You get a 500. Rate limit hit? You get a 429. Quota exhausted? Retry loop until timeout. The developer gets paged, manually switches providers, and deploys a fix.

Genesis Router was built for systems that **can't stop to wait for a human**. It detects failures, classifies them, routes around them, and self-heals — all without manual intervention.

### The Self-Healing Loop

```
Provider goes down
  │
  ▼
1. DETECT — Error classification identifies the failure type
   │         (transient? permanent? quota exhausted? degraded?)
   │
   ▼
2. ISOLATE — Circuit breaker trips OPEN, stops sending traffic
   │          to the failing provider immediately
   │
   ▼
3. ROUTE AROUND — Fallback chain activates, traffic flows
   │               through healthy providers seamlessly
   │
   ▼
4. RECOVER — After cooldown, circuit enters HALF_OPEN state
   │          and sends a probe request to test recovery
   │
   ▼
5. VERIFY — Probe succeeds → still HALF_OPEN. Second probe
   │         succeeds → CLOSED. Provider is back in rotation.
   │         Probe fails → back to OPEN with escalating backoff
   │         (2x longer each consecutive trip, up to 30min)
   │
   ▼
6. REPORT — Every state change emits an event. You know exactly
            what failed, when, which fallback was used, and when
            recovery completed. No guessing.
```

**When manual intervention IS needed, you know exactly why:**
- Circuit breaker hit the 30-minute cap → provider has a sustained outage, not a blip
- Quota exhaustion with 4-hour backoff → billing issue, not a transient error
- All providers exhausted → your entire chain is down, time to add capacity
- Budget exceeded → spending limit hit, business decision needed

The system tells you *which* failure mode triggered and *what* to fix. It never just says "error."

### What This Looks Like in Practice

```
Request arrives
  → Check call site config → provider priority: [anthropic, openai, groq]
  → Try anthropic:
    ├── Circuit breaker: CLOSED ✓
    ├── Rate gate: within RPM ✓
    ├── Call provider... → 500 error
    ├── Classify error → TRANSIENT
    ├── Retry with exponential backoff... → 500 again
    ├── Circuit breaker: record_failure → threshold hit → OPEN
    └── Fallback to next provider
  → Try openai:
    ├── Circuit breaker: CLOSED ✓
    ├── Call succeeds → record_success
    └── Return result + emit fallback event
  → Later: cooldown expires on anthropic
    → Probe succeeds → HALF_OPEN → 2 successes → CLOSED
    → Provider back in rotation. Zero downtime. Zero manual intervention.
```

Every resilience layer is real, tested, and extracted from a production system that runs 24/7.

## Features

### Resilience
- **Unlimited provider chaining** — Add as many providers and API keys as you want. Chain 2 providers or 15 — the router walks the entire chain until one succeeds. Mix paid and free models, cloud and local, fast and powerful. Every provider you add is another layer of redundancy.
- **Free-model safety net** — Mark providers as `free: true` and use `never_pays: true` on budget-conscious call sites. Even if every paid provider is down or your budget is exhausted, free models (Groq, Ollama, Cerebras, etc.) keep your system running. The router automatically skips paid providers when budget limits are hit — it doesn't error, it degrades gracefully to free alternatives.
- **3-state circuit breaker** — CLOSED → OPEN → HALF_OPEN with escalating backoff. Each consecutive trip doubles the cooldown (120s → 240s → 480s), capped at 30 minutes. Quota exhaustion gets a separate 4-hour cap because billing issues don't resolve in minutes.
- **Intelligent error classification** — Not all errors are the same. The router classifies each failure as transient (retry with backoff), permanent (fail fast, don't waste time), quota exhausted (long backoff, don't waste money), or degraded (retry once). This classification drives every downstream decision.
- **Per-call-site fallback chains** — Different call sites have different priorities. Your chat endpoint might prefer Claude → GPT-4o → Llama. Your summarizer might prefer Groq (fast) → GPT-4o-mini (cheap). Each has its own chain, retry policy, and budget rules.
- **RPM-aware rate limiting** — When a provider goes down and traffic cascades to the fallback, the rate gate prevents the thundering herd from tripping the fallback's circuit breaker too. Requests queue orderly instead of all firing at once.
- **Exponential backoff with jitter** — Configurable per call site. Jitter prevents synchronized retry storms across concurrent requests.

### Observability
- **Event emission on every state change** — Fallback used, circuit breaker tripped, budget warning, all providers exhausted. Every event includes the call site, provider, and failure context. Plug in your own `EventHook` to route to your alerting stack.
- **Cost tracking + budget enforcement** — Every call is logged to SQLite with provider, model, token counts, and cost. Daily/weekly/monthly budget limits with configurable warning thresholds. When a budget is exceeded, paid providers are automatically skipped — not errored.
- **RoutingResult tells the full story** — Every call returns which provider was used, whether fallback was needed, how many attempts, which providers failed and why. When something goes wrong, you don't debug — you read.

### Operations
- **Hot-reloadable config** — Swap routing config without restart. Circuit breaker state is preserved across reloads. Add providers, change chains, adjust retry policies — all live.
- **YAML config with env var expansion** — `${API_KEY}` and `${VAR:-default}` syntax. Local overlay files (`.local.yaml`) for per-environment overrides that survive upstream updates.
- **Disk-persistent circuit breaker state** — Opt-in. Circuit breaker memory survives process restarts so recovered providers don't get re-probed unnecessarily.
- **System-wide degradation level** — `compute_degradation_level()` gives you a single L0-L5 reading across all providers. L0 = normal, L5 = local compute down. Use it to adjust system behavior at a higher level.

### Developer Experience
- **MultiDelegate** — Maps provider types to SDK delegates. `from_config()` auto-detects API keys from environment. OpenAI-compatible providers (Ollama, LM Studio, DeepInfra, Together) automatically route through the OpenAI delegate with per-provider `base_url`.
- **Model alias resolution** — Type "sonnet" instead of "anthropic/claude-sonnet-4-6". Fuzzy matching catches typos.
- **Protocol-based extensibility** — Implement `CallDelegate` for any LLM backend. Optional `EventHook` for observability, `ActivityTracker` for metrics. No mandatory dependencies beyond PyYAML and aiosqlite.

## Installation

```bash
pip install genesis-router

# With specific provider SDKs:
pip install genesis-router[anthropic]   # Claude
pip install genesis-router[openai]      # GPT / OpenAI-compatible
pip install genesis-router[groq]        # Groq
pip install genesis-router[all]         # All providers
```

## Quick Start

```python
import asyncio
from genesis_router import Router, CircuitBreakerRegistry, MultiDelegate, load_config_from_string

config_yaml = """
providers:
  anthropic-sonnet:
    type: anthropic
    model: claude-sonnet-4-6-20250514
    free: false
    rpm_limit: 60
    open_duration_s: 120
  groq-llama:
    type: groq
    model: llama-3.1-8b-instant
    free: true
    rpm_limit: 30
    open_duration_s: 60

call_sites:
  chat:
    chain: [anthropic-sonnet, groq-llama]

retry:
  default:
    max_retries: 3
    base_delay_ms: 500
    max_delay_ms: 30000
"""

async def main():
    config = load_config_from_string(config_yaml, check_api_keys=False)
    breakers = CircuitBreakerRegistry(config.providers)

    # Auto-detects API keys from environment (ANTHROPIC_API_KEY, etc.)
    delegate = MultiDelegate.from_config(config)
    router = Router(config, breakers, delegate)

    result = await router.route_call(
        "chat",
        [{"role": "user", "content": "Hello, world!"}],
    )

    if result.success:
        print(f"Response from {result.provider_used}: {result.content}")
        print(f"Fallback used: {result.fallback_used}")
        print(f"Tokens: {result.input_tokens} in, {result.output_tokens} out")
    else:
        print(f"Failed: {result.error}")
        print(f"Tried: {', '.join(result.failed_providers)}")

asyncio.run(main())
```

### With Explicit Provider Mapping

```python
from genesis_router import MultiDelegate
from genesis_router.providers.anthropic import AnthropicDelegate
from genesis_router.providers.openai import OpenAIDelegate
from genesis_router.providers.groq import GroqDelegate

# Map provider types to their SDK delegates
delegate = MultiDelegate(
    delegates={
        "anthropic": AnthropicDelegate(api_key="sk-..."),
        "openai": OpenAIDelegate(api_key="sk-..."),
        "groq": GroqDelegate(api_key="gsk-..."),
    },
    config=config,
)

# OpenAI-compatible providers (lmstudio, ollama, etc.) automatically
# route to the "openai" delegate using the base_url from config.
```

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Router.route_call()                   │
│                                                          │
│  For each provider in the call site's chain:             │
│                                                          │
│    ┌─────────────┐   ┌───────────┐   ┌──────────────┐   │
│    │   Circuit    │──▶│   Rate    │──▶│    Retry     │   │
│    │   Breaker    │   │   Gate    │   │   (backoff)  │   │
│    │             │   │           │   │              │   │
│    │ CLOSED: ✓   │   │ RPM-aware │   │ Transient:   │   │
│    │ OPEN: skip  │   │ serialize │   │  retry + jit │   │
│    │ HALF: probe │   │ requests  │   │ Permanent:   │   │
│    └─────────────┘   └───────────┘   │  fail fast   │   │
│                                      └──────┬───────┘   │
│                                             │            │
│    ┌──────────────┐                         ▼            │
│    │ CallDelegate │◀── provider.call(messages)           │
│    │  (Protocol)  │                                      │
│    │              │──▶ CallResult(success, content, ...) │
│    └──────────────┘                                      │
│                                                          │
│    On success: record cost, update breaker, return       │
│    On failure: classify error, update breaker, next      │
│    All exhausted: emit event, return failure             │
└──────────────────────────────────────────────────────────┘
```

## Config Reference

```yaml
# Providers — LLM backends with health and cost metadata
providers:
  my-provider:
    type: anthropic          # Provider SDK type
    model: claude-sonnet-4-6 # Model identifier
    free: false              # Cost flag (affects budget enforcement)
    rpm_limit: 60            # Requests per minute (null = unlimited)
    open_duration_s: 120     # Circuit breaker base cooldown (seconds)
    base_url: null           # For OpenAI-compatible endpoints
    enabled: true            # Can be env var: ${ENABLE_PROVIDER:-true}

# Call sites — named routing configurations
call_sites:
  my-site:
    chain: [provider-a, provider-b, provider-c]  # Priority order
    never_pays: false       # If true, skip non-free providers
    retry_profile: default  # Which retry policy to use

# Retry profiles — backoff configuration
retry:
  default:
    max_retries: 3
    base_delay_ms: 500
    max_delay_ms: 30000
    backoff_multiplier: 2.0
    jitter_pct: 0.25
```

## API Reference

### Router

| Method | Description |
|--------|-------------|
| `route_call(call_site_id, messages, **kwargs)` | Route a call through the provider chain. Returns `RoutingResult`. |
| `reload_config(new_config)` | Hot-swap config, preserving circuit breaker state. |
| `set_activity_tracker(tracker)` | Inject optional metrics tracker. |

### CircuitBreaker

| Method | Description |
|--------|-------------|
| `is_available()` | True if provider can accept requests (not OPEN). |
| `record_success()` | Record successful call. HALF_OPEN → CLOSED after threshold. |
| `record_failure(category)` | Record failure. Returns True if breaker tripped. |
| `probe_suspect()` | Downgrade CLOSED → HALF_OPEN for verification. |

### CostTracker

| Method | Description |
|--------|-------------|
| `record(call_site_id, provider, result)` | Log an LLM call cost. |
| `check_budget()` | Check all budget periods. Returns `BudgetStatus`. |
| `set_budget(type, limit_usd, warning_pct)` | Set daily/weekly/monthly limit. |
| `get_period_cost(period)` | Get total cost for a period. |

## Demo

```bash
# Interactive chat — type messages, see routing decisions
python examples/demo.py

# Scripted resilience scenario — watch circuit breakers in action
python examples/demo.py --scenario resilience
```

## License

MIT — Extracted from [GENesis-AGI](https://github.com/WingedGuardian/GENesis-AGI).
