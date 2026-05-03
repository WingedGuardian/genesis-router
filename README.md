# genesis-router

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-passing-green.svg)]()

**Production-grade LLM routing with circuit breakers, fallback chains, rate limiting, and cost tracking.**

Extracted from [GENesis-AGI](https://github.com/WingedGuardian/GENesis-AGI) — an autonomous cognitive architecture that routes ~40 heterogeneous LLM call sites across 15+ providers. This is the standalone routing engine.

---

## Why This Exists

Most LLM routers are glorified try/except blocks. When your primary provider goes down at 3am, they fail. When you hit a rate limit, they fail. When your provider starts returning garbage, they keep sending traffic.

Genesis Router handles all of this:

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
  → Background: recovery probe on anthropic
    → Succeeds → HALF_OPEN → 2 successes → CLOSED
```

Every resilience layer is real, tested, and battle-hardened from production use.

## Features

- **3-state circuit breaker** — CLOSED → OPEN → HALF_OPEN with escalating backoff (doubles per consecutive trip, caps at 30min / 4h for quota exhaustion)
- **Per-call-site fallback chains** — different provider priorities for different use cases (cheap model for summarization, powerful model for reasoning)
- **RPM-aware rate limiting** — serializes requests to respect provider RPM limits, prevents thundering herd on fallback
- **Error classification** — distinguishes transient (retry), permanent (don't retry), quota exhaustion (long backoff), and degraded (retry once)
- **Exponential backoff with jitter** — configurable retry policies per call site
- **Cost tracking + budget enforcement** — SQLite-backed daily/weekly/monthly budget limits with warning thresholds
- **YAML config with env var expansion** — `${API_KEY}` and `${VAR:-default}` syntax, local overlay support
- **Hot-reloadable config** — swap routing config without restart, circuit breaker state preserved
- **Model alias resolution** — type "sonnet" instead of "anthropic/claude-sonnet-4-6", fuzzy matching included
- **Protocol-based extensibility** — implement `CallDelegate` for any LLM backend, optional `EventHook` for observability

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
