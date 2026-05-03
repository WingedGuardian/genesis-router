"""Genesis Router — Production-grade LLM routing.

Extracted from the Genesis AGI routing engine. Provides multi-provider
fallback chains with circuit breakers, rate limiting, retry logic,
cost tracking, and budget enforcement.

Quick start::

    from genesis_router import Router, load_config, CircuitBreakerRegistry
    from genesis_router.providers import MultiDelegate

    config = load_config("config.yaml")
    breakers = CircuitBreakerRegistry(config.providers)
    delegate = MultiDelegate.from_config(config)  # auto-detects API keys
    router = Router(config, breakers, delegate)
    result = await router.route_call("chat", [{"role": "user", "content": "Hello"}])
"""

from genesis_router.aliases import MODEL_ALIASES, resolve_model
from genesis_router.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry
from genesis_router.config import load_config, load_config_from_string
from genesis_router.cost_tracker import CostTracker
from genesis_router.protocols import ActivityTracker, ApiKeyChecker, EventHook
from genesis_router.providers.multi import MultiDelegate
from genesis_router.rate_gate import ProviderRateGate, RateGateRegistry
from genesis_router.retry import classify_error, compute_delay
from genesis_router.router import Router
from genesis_router.types import (
    BudgetStatus,
    CallDelegate,
    CallResult,
    CallSiteConfig,
    DegradationLevel,
    ErrorCategory,
    ProviderConfig,
    ProviderState,
    RetryPolicy,
    RoutingConfig,
    RoutingResult,
)

__version__ = "2.0.0"

__all__ = [
    # Core
    "Router",
    "CallDelegate",
    "CallResult",
    "RoutingResult",
    # Config
    "load_config",
    "load_config_from_string",
    "RoutingConfig",
    "ProviderConfig",
    "CallSiteConfig",
    "RetryPolicy",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    # Rate limiting
    "ProviderRateGate",
    "RateGateRegistry",
    # Retry
    "classify_error",
    "compute_delay",
    "ErrorCategory",
    # Cost
    "CostTracker",
    "BudgetStatus",
    # Protocols
    "EventHook",
    "ActivityTracker",
    "ApiKeyChecker",
    # Enums
    "ProviderState",
    "DegradationLevel",
    # Aliases
    "MODEL_ALIASES",
    "resolve_model",
    # Multi-provider
    "MultiDelegate",
]
