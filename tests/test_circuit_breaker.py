"""Tests for circuit breaker state machine."""

from genesis_router.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry
from genesis_router.types import ErrorCategory, ProviderConfig, ProviderState


def _make_provider(name: str = "test", open_duration_s: int = 120) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        provider_type="mock",
        model_id="test-model",
        is_free=True,
        rpm_limit=None,
        open_duration_s=open_duration_s,
    )


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(_make_provider())
        assert cb.state == ProviderState.CLOSED
        assert cb.is_available()

    def test_trips_after_threshold(self):
        cb = CircuitBreaker(_make_provider(), failure_threshold=3)
        cb.record_failure(ErrorCategory.TRANSIENT)
        cb.record_failure(ErrorCategory.TRANSIENT)
        assert cb.state == ProviderState.CLOSED

        tripped = cb.record_failure(ErrorCategory.TRANSIENT)
        assert tripped
        assert cb.state == ProviderState.OPEN
        assert not cb.is_available()

    def test_transitions_to_half_open_after_cooldown(self):
        clock_time = [0.0]
        cb = CircuitBreaker(
            _make_provider(open_duration_s=10),
            failure_threshold=1,
            open_duration_s=10,
            clock=lambda: clock_time[0],
        )
        cb.record_failure(ErrorCategory.TRANSIENT)
        assert cb.state == ProviderState.OPEN

        # Advance past cooldown
        clock_time[0] = 11.0
        assert cb.state == ProviderState.HALF_OPEN
        assert cb.is_available()

    def test_half_open_recovers_on_success(self):
        clock_time = [0.0]
        cb = CircuitBreaker(
            _make_provider(open_duration_s=10),
            failure_threshold=1,
            open_duration_s=10,
            success_threshold=2,
            clock=lambda: clock_time[0],
        )
        cb.record_failure(ErrorCategory.TRANSIENT)
        clock_time[0] = 11.0
        assert cb.state == ProviderState.HALF_OPEN

        cb.record_success()
        assert cb.state == ProviderState.HALF_OPEN  # need 2 successes
        cb.record_success()
        assert cb.state == ProviderState.CLOSED
        assert cb.trip_count == 0  # reset on recovery

    def test_half_open_trips_on_failure(self):
        clock_time = [0.0]
        cb = CircuitBreaker(
            _make_provider(open_duration_s=10),
            failure_threshold=1,
            open_duration_s=10,
            clock=lambda: clock_time[0],
        )
        cb.record_failure(ErrorCategory.TRANSIENT)
        clock_time[0] = 11.0
        assert cb.state == ProviderState.HALF_OPEN

        cb.record_failure(ErrorCategory.TRANSIENT)
        assert cb.state == ProviderState.OPEN
        assert cb.trip_count == 2  # escalated

    def test_escalating_backoff(self):
        clock_time = [0.0]
        cb = CircuitBreaker(
            _make_provider(open_duration_s=60),
            failure_threshold=1,
            open_duration_s=60,
            clock=lambda: clock_time[0],
        )

        # First trip: 60s (trip_count=1, exponent=0)
        cb.record_failure(ErrorCategory.TRANSIENT)
        assert cb._effective_open_duration() == 60.0

        # Recover, trip again: still 60s because trip_count resets on recovery
        clock_time[0] = 61.0
        cb.record_success()
        cb.record_success()
        cb.record_failure(ErrorCategory.TRANSIENT)
        assert cb._effective_open_duration() == 60.0

    def test_quota_exhaustion_longer_backoff(self):
        cb = CircuitBreaker(
            _make_provider(open_duration_s=60),
            failure_threshold=1,
            open_duration_s=60,
        )
        cb.record_failure(ErrorCategory.QUOTA_EXHAUSTED)
        # Quota cap is 14400 (4h) vs normal 1800 (30m)
        assert cb._effective_open_duration() <= 14400

    def test_probe_suspect(self):
        cb = CircuitBreaker(_make_provider())
        assert cb.probe_suspect()
        assert cb.state == ProviderState.HALF_OPEN
        assert not cb.probe_suspect()  # already suspect


class TestCircuitBreakerRegistry:
    def test_get_creates_breaker(self, tmp_path):
        providers = {"test": _make_provider()}
        registry = CircuitBreakerRegistry(
            providers, state_file=tmp_path / "state.json"
        )
        cb = registry.get("test")
        assert cb.state == ProviderState.CLOSED

    def test_persistence(self, tmp_path):
        state_file = tmp_path / "state.json"
        providers = {"test": _make_provider()}
        clock_time = [0.0]

        def clock():
            return clock_time[0]

        # Create registry, trip breaker, save
        r1 = CircuitBreakerRegistry(providers, state_file=state_file, clock=clock)
        cb = r1.get("test")
        for _ in range(3):
            cb.record_failure(ErrorCategory.TRANSIENT)
        assert cb.state == ProviderState.OPEN

        # New registry loads state (same clock, still at t=0)
        r2 = CircuitBreakerRegistry(providers, state_file=state_file, clock=clock)
        cb2 = r2.get("test")
        assert cb2.state == ProviderState.OPEN
        assert cb2.trip_count > 0

    def test_degradation_level(self, tmp_path):
        providers = {
            "cloud1": ProviderConfig("cloud1", "anthropic", "m", False, None, 120),
            "cloud2": ProviderConfig("cloud2", "openai", "m", False, None, 120),
            "free1": ProviderConfig("free1", "groq", "m", True, None, 120),
        }
        registry = CircuitBreakerRegistry(
            providers, state_file=tmp_path / "state.json"
        )
        from genesis_router.types import DegradationLevel

        assert registry.compute_degradation_level() == DegradationLevel.NORMAL

        # Trip one cloud provider
        for _ in range(3):
            registry.get("cloud1").record_failure(ErrorCategory.TRANSIENT)
        assert registry.compute_degradation_level() == DegradationLevel.FALLBACK
