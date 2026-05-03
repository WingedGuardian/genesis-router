"""Core router — walks fallback chains with circuit breakers, budgets, and rate limiting.

Extracted from the Genesis AGI routing engine. This is the real production
chain walk algorithm with all resilience layers intact.
"""

from __future__ import annotations

import asyncio
import logging
import time

from genesis_router.circuit_breaker import CircuitBreakerRegistry
from genesis_router.cost_tracker import CostTracker
from genesis_router.protocols import ActivityTracker, EventHook
from genesis_router.rate_gate import RateGateRegistry
from genesis_router.retry import classify_error, compute_delay
from genesis_router.types import (
    BudgetStatus,
    CallDelegate,
    CallResult,
    ErrorCategory,
    RoutingConfig,
    RoutingResult,
)

logger = logging.getLogger(__name__)


class Router:
    """Routes LLM calls through provider fallback chains with resilience.

    For each call site, walks the provider chain in priority order:
    1. Skip providers with open circuit breakers
    2. Skip paid providers if budget is exceeded
    3. Rate-gate requests per provider RPM limit
    4. Call the delegate with retries (exponential backoff + jitter)
    5. On success: record cost, update circuit breaker, emit events
    6. On failure: classify error, update circuit breaker, try next provider
    7. If all providers exhausted: return failure result
    """

    def __init__(
        self,
        config: RoutingConfig,
        breakers: CircuitBreakerRegistry,
        delegate: CallDelegate,
        cost_tracker: CostTracker | None = None,
        event_hook: EventHook | None = None,
    ) -> None:
        self.config = config
        self.breakers = breakers
        self.delegate = delegate
        self.cost_tracker = cost_tracker
        self._event_hook = event_hook
        self._activity_tracker: ActivityTracker | None = None
        self._rate_gates = self._build_rate_gates(config)

    @staticmethod
    def _build_rate_gates(config: RoutingConfig) -> RateGateRegistry:
        """Create rate gates for providers with RPM limits."""
        registry = RateGateRegistry()
        for name, provider in config.providers.items():
            if provider.rpm_limit is not None and provider.rpm_limit > 0:
                registry.register(name, provider.rpm_limit)
        return registry

    def set_activity_tracker(self, tracker: ActivityTracker) -> None:
        """Inject activity tracker for per-provider call metrics."""
        self._activity_tracker = tracker

    def reload_config(self, new_config: RoutingConfig) -> None:
        """Hot-swap routing config. Preserves circuit breaker state."""
        self.config = new_config
        self._rate_gates = self._build_rate_gates(new_config)
        self.breakers.update_providers(new_config.providers)
        for name in new_config.providers:
            self.breakers.get(name)  # get-or-create

    async def route_call(
        self,
        call_site_id: str,
        messages: list[dict],
        *,
        budget_override: bool = False,
        **kwargs,
    ) -> RoutingResult:
        """Route a call through the provider chain for the given call site."""
        # 1. Check call site exists
        if call_site_id not in self.config.call_sites:
            return RoutingResult(
                success=False,
                call_site_id=call_site_id,
                error=f"Unknown call site: {call_site_id}",
            )

        site = self.config.call_sites[call_site_id]
        policy = self.config.retry_profiles.get(site.retry_profile)
        if policy is None:
            policy = self.config.retry_profiles["default"]

        # 2. Filter chain
        chain = self._filter_chain(site)
        if not chain:
            return RoutingResult(
                success=False,
                call_site_id=call_site_id,
                error="No providers available in chain after filtering",
            )

        # 3. Check budget once (shared across all providers)
        budget_status = BudgetStatus.UNDER_LIMIT
        if not budget_override and self.cost_tracker:
            budget_status = await self.cost_tracker.check_budget()

        attempts = 0
        first_provider = chain[0]
        failed_providers: list[str] = []

        for provider_name in chain:
            provider_cfg = self.config.providers[provider_name]

            # Skip if circuit breaker is open
            cb = self.breakers.get(provider_name)
            if not cb.is_available():
                failed_providers.append(provider_name)
                continue

            # Skip paid providers if budget exceeded (unless override)
            if (
                not provider_cfg.is_free
                and not budget_override
                and budget_status == BudgetStatus.EXCEEDED
            ):
                failed_providers.append(provider_name)
                continue

            # Rate gate
            await self._rate_gates.acquire(provider_name)

            # Try with retry
            t0 = time.monotonic()
            result = await self._try_with_retry(
                provider_name, provider_cfg.model_id, messages, policy, **kwargs,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            attempts += 1

            # Activity tracking (fire-and-forget)
            if self._activity_tracker:
                try:
                    self._activity_tracker.record(
                        f"llm.{provider_name}",
                        latency_ms=latency_ms,
                        success=result.success,
                    )
                except Exception:
                    logger.warning(
                        "Activity tracker record failed for llm.%s",
                        provider_name, exc_info=True,
                    )

            if result.success:
                # Record cost
                if self.cost_tracker and (result.cost_usd > 0 or not result.cost_known):
                    try:
                        await self.cost_tracker.record(
                            call_site_id, provider_name, result,
                            model_id=provider_cfg.model_id,
                            cost_known=result.cost_known,
                        )
                    except Exception:
                        logger.warning(
                            "Cost recording failed for %s/%s ($%.4f)",
                            call_site_id, provider_name, result.cost_usd, exc_info=True,
                        )

                # Emit cost-unknown warning
                if not result.cost_known and result.output_tokens > 0 and self._event_hook:
                    await self._event_hook.emit(
                        "provider.cost_unknown",
                        f"Call site {call_site_id}: {provider_name} returned "
                        f"{result.output_tokens} tokens but cost is unknown",
                        severity="warning",
                        call_site=call_site_id,
                        provider=provider_name,
                    )

                cb.record_success()

                # Emit fallback event
                if provider_name != first_provider and self._event_hook:
                    await self._event_hook.emit(
                        "provider.fallback",
                        f"Call site {call_site_id}: primary '{first_provider}' failed, "
                        f"using fallback '{provider_name}' (attempt {attempts}/{len(chain)})",
                        severity="warning",
                        call_site=call_site_id,
                        provider=provider_name,
                        failed_providers=failed_providers,
                    )

                return RoutingResult(
                    success=True,
                    call_site_id=call_site_id,
                    provider_used=provider_name,
                    model_id=provider_cfg.model_id,
                    content=result.content,
                    attempts=attempts,
                    fallback_used=(provider_name != first_provider),
                    failed_providers=tuple(failed_providers),
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                )
            else:
                # Record failure
                failed_providers.append(provider_name)
                category = classify_error(result.status_code, result.error or "")
                tripped = cb.record_failure(category)
                if tripped and self._event_hook:
                    await self._event_hook.emit(
                        "breaker.tripped",
                        f"Circuit breaker tripped for {provider_name}",
                        severity="warning",
                        provider=provider_name,
                        call_site=call_site_id,
                    )

        # All exhausted
        if self._event_hook:
            await self._event_hook.emit(
                "all_exhausted",
                f"All providers exhausted for {call_site_id}",
                severity="error",
                call_site=call_site_id,
                attempts=attempts,
            )

        return RoutingResult(
            success=False,
            call_site_id=call_site_id,
            attempts=attempts,
            error="All providers exhausted",
            failed_providers=tuple(failed_providers),
        )

    def _filter_chain(self, site) -> list[str]:
        """Filter chain based on never_pays constraint."""
        if site.never_pays:
            return [p for p in site.chain if self.config.providers[p].is_free]
        return list(site.chain)

    async def _try_with_retry(
        self, provider: str, model_id: str, messages: list[dict], policy, **kwargs,
    ) -> CallResult:
        """Try calling a provider with retries. Returns last result."""
        last_result = CallResult(success=False, error="no attempts made")
        max_attempts = policy.max_retries + 1

        for attempt in range(max_attempts):
            result = await self.delegate.call(provider, model_id, messages, **kwargs)
            if result.success:
                return result

            last_result = result
            category = classify_error(result.status_code, result.error or "")

            # Permanent or quota errors: stop retrying
            if category in (ErrorCategory.PERMANENT, ErrorCategory.QUOTA_EXHAUSTED):
                return result

            # Transient/degraded: retry with delay (skip delay on last attempt)
            if attempt < max_attempts - 1:
                delay = compute_delay(policy, attempt)
                if delay > 0:
                    await asyncio.sleep(delay)

        return last_result
