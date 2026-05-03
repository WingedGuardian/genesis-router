"""Mock provider for testing and demos — no API keys needed."""

from __future__ import annotations

import asyncio
import random

from genesis_router.types import CallResult


class MockDelegate:
    """Simulates LLM calls with configurable failure rates.

    Use for testing circuit breakers, chain walks, and demos without
    real API keys. Each provider name can have its own failure rate.
    """

    def __init__(
        self,
        failure_rates: dict[str, float] | None = None,
        latency_range: tuple[float, float] = (0.05, 0.3),
    ) -> None:
        self._failure_rates = failure_rates or {}
        self._latency_range = latency_range

    async def call(
        self,
        provider: str,
        model_id: str,
        messages: list[dict],
        **kwargs,
    ) -> CallResult:
        # Simulate latency
        latency = random.uniform(*self._latency_range)  # noqa: S311
        await asyncio.sleep(latency)

        # Check failure rate for this provider
        rate = self._failure_rates.get(provider, 0.0)
        if rate > 0 and random.random() < rate:  # noqa: S311
            return CallResult(
                success=False,
                error=f"Simulated failure for {provider}",
                status_code=500,
            )

        prompt = messages[-1].get("content", "") if messages else ""
        reply = f"[mock:{provider}/{model_id}] Response to: {prompt[:60]}"
        tokens_in = max(1, len(prompt.split()))
        tokens_out = max(1, len(reply.split()))

        return CallResult(
            success=True,
            content=reply,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=0.0,
        )
