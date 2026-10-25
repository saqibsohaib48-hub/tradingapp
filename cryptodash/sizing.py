"""Position sizing from the position-sizing skill.

Fixed-fractional risk scaled by the regime multiplier, then capped by the
position limit and the cash available. Fees and slippage on both fills are
counted as part of the loss at the stop.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .settings import Settings
from .signals import Plan


@dataclass
class Size:
    qty: float
    notional: float
    risk_usd: float          # loss if the stop fills, fees included
    limited_by: str

    def to_dict(self) -> dict:
        return asdict(self)


def size_trade(plan: Plan, equity: float, cash: float, regime_mult: float, s: Settings) -> Size:
    cost_rate = (s.fee_pct + s.slippage_pct) / 100
    loss_per_unit = plan.entry - plan.stop + cost_rate * (plan.entry + plan.stop)
    if loss_per_unit <= 0 or plan.entry <= 0:
        return Size(0.0, 0.0, 0.0, "invalid stop")

    budget_risk = equity * s.risk_per_trade_pct / 100 * regime_mult
    limits = {
        f"risk {s.risk_per_trade_pct:g}% x regime {regime_mult:g}": budget_risk / loss_per_unit,
        f"position cap {s.max_position_pct:g}%": equity * s.max_position_pct / 100 / plan.entry,
        # mirrors PaperAccount.open: fill = entry*(1+slip), plus fee on the fill
        "cash available": cash / (plan.entry * (1 + s.slippage_pct / 100) * (1 + s.fee_pct / 100)),
    }
    limited_by = min(limits, key=limits.get)
    qty = max(limits[limited_by], 0.0) * 0.9999   # round down, never up past a limit
    qty = float(f"{qty:.6g}")
    return Size(qty, round(float(qty * plan.entry), 2), round(float(qty * loss_per_unit), 2), limited_by)
