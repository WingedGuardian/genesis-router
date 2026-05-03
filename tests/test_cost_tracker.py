"""Tests for cost tracking and budget enforcement."""


import aiosqlite
import pytest

from genesis_router.cost_tracker import CostTracker
from genesis_router.types import BudgetStatus, CallResult


@pytest.fixture
async def tracker(tmp_path):
    """Create a CostTracker with an in-memory-like temp DB."""
    db_path = tmp_path / "test_costs.db"
    db = await aiosqlite.connect(str(db_path))
    ct = CostTracker(db)
    await ct.ensure_tables()
    yield ct
    await db.close()


class TestCostTracker:
    @pytest.mark.asyncio
    async def test_record_stores_values(self, tracker):
        result = CallResult(
            success=True,
            content="hello",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.005,
        )
        await tracker.record("chat", "anthropic-sonnet", result, model_id="claude-sonnet-4-6")

        cursor = await tracker.db.execute(
            "SELECT provider, model, input_tokens, output_tokens, "
            "cost_usd, call_site FROM cost_events"
        )
        row = await cursor.fetchone()
        assert row[0] == "anthropic-sonnet"  # provider
        assert row[1] == "claude-sonnet-4-6"  # model (not provider!)
        assert row[2] == 100  # input_tokens
        assert row[3] == 50  # output_tokens
        assert row[4] == pytest.approx(0.005)  # cost_usd
        assert row[5] == "chat"  # call_site

    @pytest.mark.asyncio
    async def test_record_without_model_id_uses_provider(self, tracker):
        result = CallResult(success=True, cost_usd=0.001)
        await tracker.record("chat", "test-provider", result)

        cursor = await tracker.db.execute("SELECT model FROM cost_events")
        row = await cursor.fetchone()
        assert row[0] == "test-provider"  # fallback

    @pytest.mark.asyncio
    async def test_no_budget_returns_under_limit(self, tracker):
        status = await tracker.check_budget()
        assert status == BudgetStatus.UNDER_LIMIT

    @pytest.mark.asyncio
    async def test_budget_exceeded(self, tracker):
        await tracker.set_budget("daily", limit_usd=0.01, warning_pct=0.8)

        # Record enough cost to exceed
        result = CallResult(success=True, cost_usd=0.02)
        await tracker.record("chat", "test", result)

        status = await tracker.check_budget()
        assert status == BudgetStatus.EXCEEDED

    @pytest.mark.asyncio
    async def test_budget_warning(self, tracker):
        await tracker.set_budget("daily", limit_usd=1.00, warning_pct=0.8)

        # Record 85% of budget
        result = CallResult(success=True, cost_usd=0.85)
        await tracker.record("chat", "test", result)

        status = await tracker.check_budget()
        assert status == BudgetStatus.WARNING

    @pytest.mark.asyncio
    async def test_get_period_cost(self, tracker):
        result = CallResult(success=True, cost_usd=0.123)
        await tracker.record("chat", "test", result)

        cost = await tracker.get_period_cost("today")
        assert cost == pytest.approx(0.123)

    @pytest.mark.asyncio
    async def test_set_budget_replaces_existing(self, tracker):
        await tracker.set_budget("daily", limit_usd=1.00)
        await tracker.set_budget("daily", limit_usd=2.00)

        cursor = await tracker.db.execute(
            "SELECT limit_usd FROM budgets WHERE budget_type='daily' AND active=1"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 2.00
