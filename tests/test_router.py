"""Tests for the core router — chain walk with mock delegates."""

import pytest

from genesis_router import (
    CircuitBreakerRegistry,
    Router,
    load_config_from_string,
)
from genesis_router.providers.mock import MockDelegate
from genesis_router.types import ErrorCategory

BASIC_CONFIG = """\
providers:
  primary:
    type: mock
    model: m1
    free: false
    open_duration_s: 60
  secondary:
    type: mock
    model: m2
    free: false
    open_duration_s: 60
  fallback:
    type: mock
    model: m3
    free: true
    open_duration_s: 60

call_sites:
  chat:
    chain: [primary, secondary, fallback]
  free_only:
    chain: [primary, secondary, fallback]
    never_pays: true

retry:
  default:
    max_retries: 1
    base_delay_ms: 10
    max_delay_ms: 50
"""


def _make_router(failure_rates=None, **kw):
    config = load_config_from_string(BASIC_CONFIG, check_api_keys=False)
    breakers = CircuitBreakerRegistry(config.providers, state_file="/dev/null")
    delegate = MockDelegate(failure_rates=failure_rates or {}, latency_range=(0.001, 0.005))
    return Router(config, breakers, delegate, **kw), config, breakers


class TestRouteCall:
    @pytest.mark.asyncio
    async def test_success_uses_primary(self):
        router, _, _ = _make_router()
        result = await router.route_call("chat", [{"role": "user", "content": "hi"}])
        assert result.success
        assert result.provider_used == "primary"
        assert not result.fallback_used
        assert result.content is not None

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self):
        router, _, _ = _make_router(failure_rates={"primary": 1.0})
        result = await router.route_call("chat", [{"role": "user", "content": "hi"}])
        assert result.success
        assert result.provider_used == "secondary"
        assert result.fallback_used
        assert "primary" in result.failed_providers

    @pytest.mark.asyncio
    async def test_cascading_fallback(self):
        router, _, _ = _make_router(
            failure_rates={"primary": 1.0, "secondary": 1.0}
        )
        result = await router.route_call("chat", [{"role": "user", "content": "hi"}])
        assert result.success
        assert result.provider_used == "fallback"
        assert result.fallback_used

    @pytest.mark.asyncio
    async def test_all_exhausted(self):
        router, _, _ = _make_router(
            failure_rates={"primary": 1.0, "secondary": 1.0, "fallback": 1.0}
        )
        result = await router.route_call("chat", [{"role": "user", "content": "hi"}])
        assert not result.success
        assert "exhausted" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_call_site(self):
        router, _, _ = _make_router()
        result = await router.route_call("nonexistent", [{"role": "user", "content": "hi"}])
        assert not result.success
        assert "Unknown call site" in result.error

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_open(self):
        router, config, breakers = _make_router()
        # Trip primary circuit breaker
        cb = breakers.get("primary")
        for _ in range(3):
            cb.record_failure(ErrorCategory.TRANSIENT)
        assert not cb.is_available()

        result = await router.route_call("chat", [{"role": "user", "content": "hi"}])
        assert result.success
        assert result.provider_used == "secondary"

    @pytest.mark.asyncio
    async def test_never_pays_filters_paid(self):
        router, _, _ = _make_router()
        result = await router.route_call("free_only", [{"role": "user", "content": "hi"}])
        assert result.success
        assert result.provider_used == "fallback"  # only free provider

    @pytest.mark.asyncio
    async def test_event_hook_called(self):
        events = []

        class Recorder:
            async def emit(self, event_type, message, **kw):
                events.append(event_type)

        router, _, _ = _make_router(
            failure_rates={"primary": 1.0},
        )
        router._event_hook = Recorder()
        result = await router.route_call("chat", [{"role": "user", "content": "hi"}])
        assert result.success
        assert "provider.fallback" in events


class TestAliases:
    def test_exact_match(self):
        from genesis_router.aliases import resolve_model

        model_id, key = resolve_model("sonnet")
        assert model_id == "anthropic/claude-sonnet-4-6"
        assert key == "sonnet"

    def test_passthrough_full_id(self):
        from genesis_router.aliases import resolve_model

        model_id, key = resolve_model("anthropic/claude-haiku-4-5")
        assert model_id == "anthropic/claude-haiku-4-5"

    def test_fuzzy_match(self):
        from genesis_router.aliases import resolve_model

        model_id, key = resolve_model("sonet")  # typo
        assert model_id is not None  # should fuzzy match

    def test_unknown(self):
        from genesis_router.aliases import resolve_model

        model_id, msg = resolve_model("totallyunknown")
        assert model_id is None
        assert "Unknown model" in msg
