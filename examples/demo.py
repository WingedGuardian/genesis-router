#!/usr/bin/env python3
"""Interactive demo showing the Genesis Router resilience stack.

Demonstrates:
  - Provider fallback chains
  - Circuit breaker tripping and recovery
  - Rate limiting
  - Cost tracking

Run:
    python examples/demo.py
    python examples/demo.py --scenario resilience
"""

from __future__ import annotations

import argparse
import asyncio

from genesis_router import (
    CircuitBreakerRegistry,
    Router,
    load_config_from_string,
)
from genesis_router.providers.mock import MockDelegate

# Inline config for the demo — no external files needed
DEMO_CONFIG = """
providers:
  primary:
    type: anthropic
    model: claude-sonnet-4-6
    free: false
    rpm_limit: 10
    open_duration_s: 5

  secondary:
    type: openai
    model: gpt-4o-mini
    free: false
    open_duration_s: 5

  fallback:
    type: groq
    model: llama-3.1-8b-instant
    free: true
    open_duration_s: 5

call_sites:
  chat:
    chain:
      - primary
      - secondary
      - fallback

retry:
  default:
    max_retries: 2
    base_delay_ms: 100
    max_delay_ms: 1000
"""


class DemoEventHook:
    """Prints routing events to stdout."""

    async def emit(self, event_type: str, message: str, *, severity: str = "info", **kw):
        icon = {"error": "!!!", "warning": " ! "}.get(severity, "   ")
        print(f"  [{icon}] {event_type}: {message}")


async def run_interactive():
    """Interactive chat loop — type messages, see routing decisions."""
    config = load_config_from_string(DEMO_CONFIG, check_api_keys=False)
    breakers = CircuitBreakerRegistry(config.providers, state_file="/dev/null")
    delegate = MockDelegate()
    hook = DemoEventHook()
    router = Router(config, breakers, delegate, event_hook=hook)

    print("Genesis Router — Interactive Demo")
    print("=" * 50)
    print("Type a message to route it through the provider chain.")
    print("Commands: /status, /trip <provider>, /reset <provider>, /quit")
    print()

    while True:
        try:
            user_input = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input == "/quit":
            break
        if user_input == "/status":
            for name in config.providers:
                cb = breakers.get(name)
                print(f"  {name}: {cb.state.value} (trips: {cb.trip_count})")
            continue
        if user_input.startswith("/trip "):
            provider = user_input.split(maxsplit=1)[1]
            if provider in config.providers:
                from genesis_router.types import ErrorCategory

                cb = breakers.get(provider)
                for _ in range(3):
                    cb.record_failure(ErrorCategory.TRANSIENT)
                print(f"  Tripped circuit breaker for {provider}")
            else:
                print(f"  Unknown provider: {provider}")
            continue
        if user_input.startswith("/reset "):
            provider = user_input.split(maxsplit=1)[1]
            if provider in config.providers:
                cb = breakers.get(provider)
                cb.record_success()
                print(f"  Reset circuit breaker for {provider}")
            continue

        result = await router.route_call(
            "chat", [{"role": "user", "content": user_input}]
        )

        if result.success:
            fallback_note = " (FALLBACK)" if result.fallback_used else ""
            print(f"\n  Provider: {result.provider_used}{fallback_note}")
            print(f"  Model:    {result.model_id}")
            print(f"  Tokens:   {result.input_tokens}→{result.output_tokens}")
            print(f"  Response: {result.content}")
        else:
            print(f"\n  FAILED: {result.error}")
            print(f"  Tried: {', '.join(result.failed_providers)}")
        print()


async def run_resilience_scenario():
    """Scripted demo showing circuit breaker trip → fallback → recovery."""
    config = load_config_from_string(DEMO_CONFIG, check_api_keys=False)
    breakers = CircuitBreakerRegistry(config.providers, state_file="/dev/null")

    # Primary fails 100%, secondary fails 50%, fallback always works
    delegate = MockDelegate(
        failure_rates={"primary": 1.0, "secondary": 0.5, "fallback": 0.0},
        latency_range=(0.01, 0.05),
    )
    hook = DemoEventHook()
    router = Router(config, breakers, delegate, event_hook=hook)

    print("Genesis Router — Resilience Scenario")
    print("=" * 50)
    print("Primary: 100% failure rate")
    print("Secondary: 50% failure rate")
    print("Fallback: 0% failure rate")
    print()

    for i in range(1, 8):
        print(f"--- Request {i} ---")
        result = await router.route_call(
            "chat", [{"role": "user", "content": f"Request {i}"}]
        )
        if result.success:
            fallback = " (FALLBACK)" if result.fallback_used else ""
            print(f"  OK via {result.provider_used}{fallback} "
                  f"(attempt {result.attempts}/{len(config.call_sites['chat'].chain)})")
        else:
            print(f"  FAILED: {result.error}")

        # Show circuit breaker states
        states = {name: breakers.get(name).state.value for name in config.providers}
        print(f"  Breakers: {states}")
        print()

    # Now fix the primary and show recovery
    print("--- Fixing primary (0% failure rate) ---")
    delegate._failure_rates["primary"] = 0.0
    # Manually move to half-open to simulate cooldown expiry
    breakers.get("primary")._state = __import__(
        "genesis_router.types", fromlist=["ProviderState"]
    ).ProviderState.HALF_OPEN
    print()

    for i in range(8, 11):
        print(f"--- Request {i} ---")
        result = await router.route_call(
            "chat", [{"role": "user", "content": f"Request {i}"}]
        )
        if result.success:
            fallback = " (FALLBACK)" if result.fallback_used else ""
            print(f"  OK via {result.provider_used}{fallback}")

        states = {name: breakers.get(name).state.value for name in config.providers}
        print(f"  Breakers: {states}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Genesis Router Demo")
    parser.add_argument(
        "--scenario",
        choices=["interactive", "resilience"],
        default="interactive",
        help="Demo scenario to run",
    )
    args = parser.parse_args()

    if args.scenario == "resilience":
        asyncio.run(run_resilience_scenario())
    else:
        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
