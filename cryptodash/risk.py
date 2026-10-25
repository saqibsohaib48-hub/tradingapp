"""Pre-trade gate from the risk-management (and pre-trade-gate) skills.

Every check returns a plain reason. A trade is PASS only when all checks pass.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .settings import Settings
from .signals import Plan
from .sizing import Size


def check(plan: Plan, size: Size, regime_mult: float, account, s: Settings,
          now: datetime | None = None) -> tuple[bool, list[str]]:
    now = now or datetime.now(timezone.utc)
    fails: list[str] = []

    if plan.stop >= plan.entry:
        fails.append("Stop is not below entry")
    if plan.target <= plan.entry:
        fails.append("Target is not above entry")
    if plan.reward_risk < s.min_reward_risk:
        fails.append(f"Reward:risk {plan.reward_risk:.2f} < {s.min_reward_risk}")
    if regime_mult <= 0:
        fails.append("Regime says sit out")
    if size.qty <= 0 or size.notional < s.min_order_usd:
        fails.append(f"Order ${size.notional:.2f} below ${s.min_order_usd:g} minimum ({size.limited_by})")

    if account is not None:
        if any(p["symbol"] == plan.symbol for p in account.positions):
            fails.append("Already holding this coin")
        if len(account.positions) >= s.max_open_trades:
            fails.append(f"Max {s.max_open_trades} open trades reached")
        if account.trades_opened_today(now) >= s.max_trades_per_day:
            fails.append(f"Max {s.max_trades_per_day} trades today reached")
        used = account.realized_loss_today(now) + account.open_risk()
        if used + size.risk_usd > s.max_daily_loss_usd:
            fails.append(f"Daily loss limit: ${used:.2f} used + ${size.risk_usd:.2f} > ${s.max_daily_loss_usd:g}")
        dd = account.drawdown_pct()
        if dd >= s.max_drawdown_pct:
            fails.append(f"Circuit breaker: drawdown {dd:.1f}% >= {s.max_drawdown_pct:g}%")
        last_loss = account.last_loss_time()
        if last_loss and now - last_loss < timedelta(minutes=s.cooldown_minutes_after_loss):
            fails.append("Cooling down after a loss (no revenge trading)")

    return (not fails), fails
