"""Cost tracking and budget enforcement for compute routing.

Logs every LLM call to SQLite and checks configurable budget limits
(daily/weekly/monthly). Entirely optional — the router works without it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import aiosqlite

from genesis_router.protocols import EventHook
from genesis_router.types import BudgetStatus, CallResult

_BUDGET_PERIOD = {
    "daily": "today",
    "weekly": "this_week",
    "monthly": "this_month",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL DEFAULT 'llm_call',
    provider TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    cost_known INTEGER DEFAULT 1,
    call_site TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    id TEXT PRIMARY KEY,
    budget_type TEXT NOT NULL,
    limit_usd REAL NOT NULL,
    warning_pct REAL NOT NULL DEFAULT 0.8,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cost_events_created ON cost_events(created_at);
"""


class CostTracker:
    """Tracks LLM call costs and enforces budget limits.

    Initialize with an aiosqlite connection. Call ``ensure_tables()`` once
    before first use to create the schema.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        clock=None,
        event_hook: EventHook | None = None,
    ):
        self.db = db
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_hook = event_hook
        self._emitted_events: set[str] = set()

    async def ensure_tables(self) -> None:
        """Create cost tracking tables if they don't exist."""
        await self.db.executescript(_SCHEMA)
        await self.db.commit()

    async def record(
        self,
        call_site_id: str,
        provider: str,
        result: CallResult,
        *,
        model_id: str = "",
        cost_known: bool = True,
    ) -> None:
        """Record an LLM call as a cost event."""
        await self.db.execute(
            "INSERT INTO cost_events (id, provider, model, input_tokens, output_tokens, "
            "cost_usd, cost_known, call_site, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                provider,
                model_id or provider,
                result.input_tokens,
                result.output_tokens,
                result.cost_usd,
                1 if cost_known else 0,
                call_site_id,
                self._clock().isoformat(),
            ),
        )
        await self.db.commit()

    async def check_budget(self) -> BudgetStatus:
        """Check all budget periods and return the worst status."""
        worst = BudgetStatus.UNDER_LIMIT
        for budget_type in ("daily", "weekly", "monthly"):
            status = await self._check_period(budget_type)
            if status == BudgetStatus.EXCEEDED:
                return BudgetStatus.EXCEEDED
            if status == BudgetStatus.WARNING:
                worst = BudgetStatus.WARNING
        return worst

    async def get_period_cost(self, period: str) -> float:
        """Get total cost for a period: 'today', 'this_week', 'this_month'."""
        since = self._period_start(period)
        return await self._sum_cost(since)

    async def set_budget(
        self,
        budget_type: str,
        limit_usd: float,
        warning_pct: float = 0.8,
    ) -> None:
        """Set a budget limit. Replaces any existing budget of the same type."""
        if budget_type not in _BUDGET_PERIOD:
            msg = f"Invalid budget type: {budget_type}. Use: daily, weekly, monthly"
            raise ValueError(msg)
        # Deactivate existing
        await self.db.execute(
            "UPDATE budgets SET active=0 WHERE budget_type=? AND active=1",
            (budget_type,),
        )
        # Insert new
        await self.db.execute(
            "INSERT INTO budgets (id, budget_type, limit_usd, warning_pct) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), budget_type, limit_usd, warning_pct),
        )
        await self.db.commit()

    async def _check_period(self, budget_type: str) -> BudgetStatus:
        """Check a single budget period."""
        cursor = await self.db.execute(
            "SELECT limit_usd, warning_pct FROM budgets WHERE budget_type=? AND active=1 LIMIT 1",
            (budget_type,),
        )
        row = await cursor.fetchone()
        if not row:
            return BudgetStatus.UNDER_LIMIT

        limit_usd, warning_pct = row
        period = _BUDGET_PERIOD[budget_type]
        since = self._period_start(period)
        total = await self._sum_cost(since)

        if total >= limit_usd:
            event_key = f"{budget_type}_exceeded@{since}"
            if event_key not in self._emitted_events:
                if self._event_hook:
                    await self._event_hook.emit(
                        "budget.exceeded",
                        f"{budget_type} budget exceeded: ${total:.4f} >= ${limit_usd:.4f}",
                        severity="error",
                        budget_type=budget_type,
                        total=total,
                        limit=limit_usd,
                    )
                self._emitted_events.add(event_key)
            return BudgetStatus.EXCEEDED

        if total >= limit_usd * warning_pct:
            event_key = f"{budget_type}_warning@{since}"
            if event_key not in self._emitted_events:
                if self._event_hook:
                    await self._event_hook.emit(
                        "budget.warning",
                        f"{budget_type} budget warning: ${total:.4f} / ${limit_usd:.4f}",
                        severity="warning",
                        budget_type=budget_type,
                        total=total,
                        limit=limit_usd,
                    )
                self._emitted_events.add(event_key)
            return BudgetStatus.WARNING

        return BudgetStatus.UNDER_LIMIT

    async def _sum_cost(self, since: str) -> float:
        """Sum cost_usd for events since the given ISO timestamp."""
        cursor = await self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_events WHERE created_at >= ?",
            (since,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0

    def _period_start(self, period: str) -> str:
        """Return ISO timestamp for start of the given period."""
        now = self._clock()
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "this_week":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start -= timedelta(days=start.weekday())
        elif period == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            msg = f"Unknown period: {period}"
            raise ValueError(msg)
        return start.isoformat()
