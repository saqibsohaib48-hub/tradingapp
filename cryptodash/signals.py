"""Trade setups and exits.

Three long-only spot setups, each tied to the regime that suits it:

* trend_pullback  (regime-detection: clean trend, up)       stop = entry - k*ATR, target = entry + R*risk
* breakout        (regime-detection: BB squeeze -> expansion) stop = entry - k*ATR, target = entry + R*risk
* mean_reversion  (mean-reversion skill: range, z <= -2)     stop = entry - k_mr*ATR, target = 20-bar mean

Entries are at the close of the last COMPLETED candle. Nothing here places an order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .regime import Regime
from .settings import Settings


@dataclass
class Plan:
    symbol: str
    strategy: str
    side: str
    entry: float
    stop: float
    target: float
    score: float
    reasons: list[str] = field(default_factory=list)
    bar_time: str = ""

    @property
    def risk_per_unit(self) -> float:
        return self.entry - self.stop

    @property
    def reward_risk(self) -> float:
        r = self.risk_per_unit
        return (self.target - self.entry) / r if r > 0 else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reward_risk"] = round(self.reward_risk, 2)
        return d


def _round_price(x: float) -> float:
    """Keep about 6 significant figures so cheap coins keep their precision."""
    if x <= 0 or np.isnan(x):
        return x
    digits = max(2, 6 - int(np.floor(np.log10(x))) - 1)
    return round(float(x), digits)


def _trend_pullback(ind: pd.DataFrame, reg: Regime, s: Settings) -> Plan | None:
    last = ind.iloc[-1]
    recent = ind.tail(3)
    touched = (recent["low"] <= recent["ema20"] + 0.5 * recent["atr"]).any()
    if not (reg.trend == "trending" and reg.direction == "up"):
        return None
    if not (last["close"] > last["ema20"] > last["ema50"] and touched and 40 <= last["rsi"] <= 70):
        return None
    entry = last["close"]
    stop = entry - s.stop_atr_mult * last["atr"]
    score = 55 + min(last["adx"] - 25, 20) + (10 if reg.volatility != "high" else 0) \
        + (10 if reg.behaviour == "trending" else 0)
    return Plan("", "trend_pullback", "long", entry, stop, entry + s.target_r * (entry - stop), score,
                [f"ADX {last['adx']:.0f} uptrend, +DI {last['plus_di']:.0f} > -DI {last['minus_di']:.0f}",
                 "Pulled back to the 20 EMA and held above it",
                 f"RSI {last['rsi']:.0f} (not overbought)"])


def _breakout(ind: pd.DataFrame, reg: Regime, s: Settings) -> Plan | None:
    last = ind.iloc[-1]
    squeeze_before = ind["bb_width_rank"].iloc[-6:-1].min() <= 0.25
    rising_adx = last["adx"] > ind["adx"].iloc[-4]
    if reg.direction == "down" or np.isnan(last["hh20"]):
        return None
    if not (last["close"] > last["hh20"] and last["vol_ratio"] >= 1.5 and rising_adx):
        return None
    entry = last["close"]
    stop = entry - s.stop_atr_mult * last["atr"]
    score = 50 + min((last["vol_ratio"] - 1.5) * 10, 15) + (15 if squeeze_before else 0) \
        + (10 if reg.behaviour == "trending" else 0)
    return Plan("", "breakout", "long", entry, stop, entry + s.target_r * (entry - stop), score,
                [f"Closed above the 20-bar high ({last['hh20']:.6g})",
                 f"Volume {last['vol_ratio']:.1f}x average, ADX rising",
                 "Came out of a volatility squeeze" if squeeze_before else "No prior squeeze"])


def _mean_reversion(ind: pd.DataFrame, reg: Regime, s: Settings) -> Plan | None:
    last = ind.iloc[-1]
    if not (reg.trend == "ranging" and reg.volatility != "high" and reg.behaviour != "trending"):
        return None
    if not (last["z"] <= -2.0 and last["rsi"] <= 35):
        return None
    entry = last["close"]
    stop = entry - s.mr_stop_atr_mult * last["atr"]
    target = last["bb_mid"]        # revert to the 20-bar mean
    score = 55 + min((abs(last["z"]) - 2) * 15, 20) + (15 if reg.behaviour == "mean_reverting" else 0)
    return Plan("", "mean_reversion", "long", entry, stop, target, score,
                [f"Range market (ADX {last['adx']:.0f}), Hurst {reg.hurst}",
                 f"Price {abs(last['z']):.1f} std devs below its 20-bar mean",
                 f"RSI {last['rsi']:.0f} oversold; target = the mean"])


def build_plan(symbol: str, ind: pd.DataFrame, reg: Regime, s: Settings) -> Plan | None:
    """Best setup on the last completed bar, or None."""
    if len(ind) < 60 or np.isnan(ind["atr"].iloc[-1]):
        return None
    candidates = [p for p in (_trend_pullback(ind, reg, s), _breakout(ind, reg, s),
                              _mean_reversion(ind, reg, s)) if p]
    if not candidates:
        return None
    plan = max(candidates, key=lambda p: p.score)
    plan.symbol = symbol
    plan.entry, plan.stop, plan.target = map(_round_price, (plan.entry, plan.stop, plan.target))
    plan.score = round(float(min(plan.score, 100)), 1)
    plan.bar_time = str(ind.index[-1])
    return plan
