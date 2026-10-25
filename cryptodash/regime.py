"""Four-quadrant regime model from the regime-detection skill.

Volatility (ATR percentile) x trend (ADX), plus direction (EMA slope / DI) and
mean-reversion tendency (Hurst). Each regime carries a size multiplier and a
playbook, exactly as in the skill's quick-reference table.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .indicators import hurst


@dataclass
class Regime:
    volatility: str        # low | normal | high
    trend: str             # trending | ranging | transitional
    direction: str         # up | down | neutral
    behaviour: str         # mean_reverting | random | trending  (Hurst)
    quadrant: str          # human label
    size_mult: float       # 0..1 applied to risk per trade
    playbook: str          # what the skill says to do
    adx: float
    atr_rank: float
    hurst: float

    def to_dict(self) -> dict:
        return asdict(self)


def classify(ind: pd.DataFrame) -> Regime:
    last = ind.iloc[-1]
    atr_rank = float(last["atr_rank"]) if not np.isnan(last["atr_rank"]) else 0.5
    adx_v = float(last["adx"])
    returns = np.log(ind["close"]).diff().dropna().tail(200)
    h = hurst(returns)

    vol = "low" if atr_rank < 0.30 else "high" if atr_rank > 0.70 else "normal"
    trend = "trending" if adx_v > 25 else "ranging" if adx_v < 20 else "transitional"

    slope = ind["ema20"].diff(5).iloc[-1]
    if slope > 0 and last["close"] > last["ema20"] and last["plus_di"] > last["minus_di"]:
        direction = "up"
    elif slope < 0 and last["close"] < last["ema20"] and last["minus_di"] > last["plus_di"]:
        direction = "down"
    else:
        direction = "neutral"

    behaviour = "mean_reverting" if h < 0.45 else "trending" if h > 0.60 else "random"

    if trend == "trending":
        if vol == "high":
            quadrant, mult, play = "Volatile trend", 0.5, "Half-size momentum, wide stops"
        else:
            quadrant, mult, play = "Clean trend", 1.0, "Full-size trend following"
        if direction == "down":
            play = "Downtrend: no spot longs, wait for a reversal"
    elif trend == "ranging":
        if vol == "high":
            quadrant, mult, play = "Choppy chaos", 0.25, "Reduce to 25% or sit out"
        else:
            quadrant, mult, play = "Quiet range", 1.0, "Mean-reversion at the band edges"
    else:
        quadrant, mult, play = "Transition", 0.25, "Regime changing: minimum size only"

    return Regime(vol, trend, direction, behaviour, quadrant, mult, play,
                  round(adx_v, 1), round(atr_rank, 2), round(h, 2))
