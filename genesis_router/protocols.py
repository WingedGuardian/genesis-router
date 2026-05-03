"""Protocol interfaces for optional integrations.

These allow the router to emit events and track metrics without coupling
to any specific observability framework. All are optional — pass None
and the router works standalone with zero dependencies beyond the core.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EventHook(Protocol):
    """Optional event emission for observability.

    Implement this to receive routing events: fallback usage, circuit
    breaker trips, budget warnings, chain exhaustion, etc.
    """

    async def emit(
        self,
        event_type: str,
        message: str,
        *,
        severity: str = "info",
        **kwargs,
    ) -> None: ...


@runtime_checkable
class ActivityTracker(Protocol):
    """Optional per-provider call metrics.

    Implement this to track latency and success rates per provider.
    """

    def record(self, metric_name: str, *, latency_ms: float, success: bool) -> None: ...


@runtime_checkable
class ApiKeyChecker(Protocol):
    """Checks whether a provider has an API key configured.

    Used by the config loader to auto-disable providers without keys.
    Local providers (ollama, lmstudio) don't need keys.
    """

    def __call__(self, provider_type: str) -> bool: ...


def default_api_key_checker(provider_type: str) -> bool:
    """Default checker: assume all providers have keys (accept all)."""
    return True
