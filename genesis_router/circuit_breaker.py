"""Circuit breaker for provider health tracking.

Three-state machine: CLOSED → OPEN → HALF_OPEN with escalating backoff.
Persists state to disk so circuit breaker memory survives restarts.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from genesis_router.types import (
    DegradationLevel,
    ErrorCategory,
    ProviderConfig,
    ProviderState,
)

logger = logging.getLogger(__name__)

_MAX_OPEN_S = 1800  # 30-minute cap on escalating backoff
_MAX_QUOTA_OPEN_S = 14400  # 4-hour cap for quota/billing exhaustion


class CircuitBreaker:
    """Per-provider circuit breaker with escalating backoff.

    Open duration doubles on each consecutive trip (base * 2^(trip-1)),
    capped at _MAX_OPEN_S (30 min) or _MAX_QUOTA_OPEN_S (4 hours) for
    quota exhaustion. Resets when the provider recovers to CLOSED.
    """

    def __init__(
        self,
        provider: ProviderConfig,
        failure_threshold: int = 3,
        open_duration_s: int = 120,
        success_threshold: int = 2,
        clock: object = None,
        on_state_change: object = None,
    ) -> None:
        self._provider = provider
        self._failure_threshold = failure_threshold
        self._open_duration_s = open_duration_s
        self._success_threshold = success_threshold
        self._clock = clock or time.monotonic
        self._on_state_change = on_state_change

        self._state = ProviderState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at: float = 0.0
        self._trip_count: int = 0
        self._last_failure_category: ErrorCategory | None = None

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def trip_count(self) -> int:
        return self._trip_count

    def _effective_open_duration(self) -> float:
        exponent = max(0, self._trip_count - 1)
        cap = (
            _MAX_QUOTA_OPEN_S
            if self._last_failure_category == ErrorCategory.QUOTA_EXHAUSTED
            else _MAX_OPEN_S
        )
        return min(self._open_duration_s * (2**exponent), cap)

    @property
    def state(self) -> ProviderState:
        """Current state, auto-transitioning OPEN → HALF_OPEN when expired."""
        if (
            self._state == ProviderState.OPEN
            and self._clock() - self._opened_at >= self._effective_open_duration()
        ):
            self._state = ProviderState.HALF_OPEN
            self._consecutive_successes = 0
        return self._state

    def is_available(self) -> bool:
        """True if the provider can accept requests (not OPEN)."""
        return self.state != ProviderState.OPEN

    def _notify_change(self) -> None:
        if self._on_state_change:
            self._on_state_change()

    def record_success(self) -> None:
        """Record a successful call."""
        old = self._state
        self._consecutive_failures = 0
        self._last_failure_category = None
        if self.state == ProviderState.HALF_OPEN:
            self._consecutive_successes += 1
            if self._consecutive_successes >= self._success_threshold:
                self._state = ProviderState.CLOSED
                self._consecutive_successes = 0
                self._trip_count = 0
        else:
            self._state = ProviderState.CLOSED
        if self._state != old:
            self._notify_change()

    def probe_suspect(self) -> bool:
        """Move CLOSED → HALF_OPEN for verification. Returns True if state changed."""
        if self._state == ProviderState.CLOSED:
            self._state = ProviderState.HALF_OPEN
            self._consecutive_successes = 0
            self._notify_change()
            return True
        return False

    def record_failure(self, category: ErrorCategory) -> bool:
        """Record a failed call. Returns True if the breaker tripped OPEN."""
        self._last_failure_category = category
        self._consecutive_successes = 0
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._trip_count += 1
            self._state = ProviderState.OPEN
            self._opened_at = self._clock()
            self._consecutive_failures = 0
            self._notify_change()
            return True
        if self.state == ProviderState.HALF_OPEN:
            self._trip_count += 1
            self._state = ProviderState.OPEN
            self._opened_at = self._clock()
            self._consecutive_failures = 0
            self._notify_change()
            return True
        return False


class CircuitBreakerRegistry:
    """Registry of circuit breakers, one per provider."""

    def __init__(
        self,
        providers: dict[str, ProviderConfig],
        clock: object = None,
        state_file: Path | str | None = None,
    ) -> None:
        self._providers = providers
        self._clock = clock
        self._state_file = Path(state_file) if state_file else None
        self._breakers: dict[str, CircuitBreaker] = {}
        self.load_state()

    def update_providers(self, providers: dict[str, ProviderConfig]) -> None:
        """Merge new provider configs (for hot-reload)."""
        self._providers.update(providers)

    def get(self, provider: str) -> CircuitBreaker:
        """Get or create a circuit breaker for the named provider."""
        if provider not in self._breakers:
            cfg = self._providers[provider]
            self._breakers[provider] = CircuitBreaker(
                provider=cfg,
                open_duration_s=cfg.open_duration_s,
                clock=self._clock,
                on_state_change=self.save_state,
            )
        return self._breakers[provider]

    def save_state(self) -> None:
        """Persist breaker states to disk so they survive restarts."""
        if self._state_file is None:
            return
        data = {}
        for name, cb in self._breakers.items():
            data[name] = {
                "state": cb._state.value,
                "consecutive_failures": cb._consecutive_failures,
                "trip_count": cb._trip_count,
                "last_failure_category": (
                    cb._last_failure_category.value if cb._last_failure_category else None
                ),
            }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception:
            logger.error("Failed to save circuit breaker state", exc_info=True)

    def load_state(self) -> None:
        """Restore breaker states from disk after restart."""
        if self._state_file is None:
            return
        if not self._state_file.is_file():
            return
        try:
            data = json.loads(self._state_file.read_text())
            for name, info in data.items():
                if name in self._providers:
                    cb = self.get(name)
                    saved_state = info.get("state", "closed").lower()
                    if saved_state == "open":
                        cb._state = ProviderState.OPEN
                        cb._opened_at = cb._clock()
                    elif saved_state == "half_open":
                        cb._state = ProviderState.HALF_OPEN
                        cb._consecutive_successes = 0
                    cb._consecutive_failures = info.get("consecutive_failures", 0)
                    cb._trip_count = info.get("trip_count", 0)
                    if saved_state == "open":
                        cb._trip_count = min(cb._trip_count, 3)
                    saved_cat = info.get("last_failure_category")
                    cb._last_failure_category = ErrorCategory(saved_cat) if saved_cat else None
            logger.info("Circuit breaker state restored from %s", self._state_file)
        except Exception:
            logger.warning("Failed to load circuit breaker state", exc_info=True)

    def compute_degradation_level(self) -> DegradationLevel:
        """Compute system-wide degradation based on provider availability."""
        cloud_providers = [
            name
            for name, cfg in self._providers.items()
            if cfg.provider_type != "ollama" and not cfg.is_free
        ]
        ollama_providers = [
            name for name, cfg in self._providers.items() if cfg.provider_type == "ollama"
        ]

        cloud_down = sum(1 for name in cloud_providers if not self.get(name).is_available())
        ollama_down = sum(1 for name in ollama_providers if not self.get(name).is_available())

        if ollama_providers and ollama_down == len(ollama_providers):
            return DegradationLevel.LOCAL_COMPUTE_DOWN
        if cloud_providers and cloud_down == len(cloud_providers):
            return DegradationLevel.ESSENTIAL
        if cloud_down > 1:
            return DegradationLevel.REDUCED
        if cloud_down == 1:
            return DegradationLevel.FALLBACK
        return DegradationLevel.NORMAL
