"""Tests for rate gate — RPM-based request serialization."""

import time

import pytest

from genesis_router.rate_gate import ProviderRateGate, RateGateRegistry


class TestProviderRateGate:
    @pytest.mark.asyncio
    async def test_first_call_no_wait(self):
        gate = ProviderRateGate("test", rpm=60)
        wait = await gate.acquire()
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_interval_computed_from_rpm(self):
        gate = ProviderRateGate("test", rpm=30)
        assert gate.interval == pytest.approx(2.0)  # 60/30

    @pytest.mark.asyncio
    async def test_second_call_waits(self):
        gate = ProviderRateGate("test", rpm=600)  # 0.1s interval
        await gate.acquire()
        t0 = time.monotonic()
        await gate.acquire()
        elapsed = time.monotonic() - t0
        # Should have waited approximately 0.1s
        assert elapsed >= 0.05  # some tolerance


class TestRateGateRegistry:
    @pytest.mark.asyncio
    async def test_no_gate_returns_zero(self):
        registry = RateGateRegistry()
        wait = await registry.acquire("unknown")
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_registered_gate_works(self):
        registry = RateGateRegistry()
        registry.register("test", rpm=60)
        assert registry.has_gate("test")
        wait = await registry.acquire("test")
        assert wait == 0.0  # first call

    def test_len(self):
        registry = RateGateRegistry()
        assert len(registry) == 0
        registry.register("a", rpm=10)
        registry.register("b", rpm=20)
        assert len(registry) == 2
