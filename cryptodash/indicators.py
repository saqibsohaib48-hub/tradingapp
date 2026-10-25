"""Technical indicators used by the regime, mean-reversion and exit rules.

Smoothing follows Wilder's original definitions (alpha = 1/period) for ATR, ADX
and RSI, which is what most charting platforms show.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (ADX, +DI, -DI)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), period)
    plus_di = 100 * wilder(plus_dm, period) / tr
    minus_di = 100 * wilder(minus_dm, period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx.fillna(0), period), plus_di, minus_di


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), period)
    loss = wilder(-delta.clip(upper=0), period)
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return out.fillna(100.0)


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0):
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std()
    upper, lower = mid + k * sd, mid - k * sd
    return mid, upper, lower, (upper - lower) / mid


def zscore(close: pd.Series, period: int = 20) -> pd.Series:
    return (close - close.rolling(period).mean()) / close.rolling(period).std()


def pct_rank(series: pd.Series, window: int = 100) -> pd.Series:
    """Percentile (0..1) of the latest value inside its own trailing window."""
    return series.rolling(window, min_periods=window // 2).apply(
        lambda x: float((x <= x[-1]).mean()), raw=True
    )


def hurst(returns: pd.Series) -> float:
    """Hurst exponent by rescaled range (R/S) on RETURNS (not prices).

    < 0.45 mean-reverting, 0.45..0.6 random walk, > 0.6 trending. Small samples
    read a little high (Anis-Lloyd bias), so treat values near the edges loosely.
    """
    x = np.asarray(pd.Series(returns).dropna(), dtype=float)
    n = len(x)
    if n < 32:
        return 0.5
    sizes = np.unique(np.logspace(np.log10(8), np.log10(n // 2), 8).astype(int))
    log_n, log_rs = [], []
    for size in sizes:
        rs = []
        for start in range(0, n - size + 1, size):
            chunk = x[start:start + size]
            sd = chunk.std(ddof=1)
            if sd > 0:
                dev = np.cumsum(chunk - chunk.mean())
                rs.append((dev.max() - dev.min()) / sd)
        if rs:
            log_n.append(np.log(size))
            log_rs.append(np.log(np.mean(rs)))
    if len(log_n) < 3:
        return 0.5
    return float(np.polyfit(log_n, log_rs, 1)[0])


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of an OHLCV frame with every indicator column the rules need."""
    out = df.copy()
    c = out["close"]
    out["atr"] = atr(out)
    out["atr_pct"] = out["atr"] / c
    out["atr_rank"] = pct_rank(out["atr_pct"], 100)
    out["adx"], out["plus_di"], out["minus_di"] = adx(out)
    out["ema20"], out["ema50"] = ema(c, 20), ema(c, 50)
    out["rsi"] = rsi(c)
    out["bb_mid"], out["bb_up"], out["bb_lo"], out["bb_width"] = bollinger(c)
    out["bb_width_rank"] = pct_rank(out["bb_width"], 100)
    out["z"] = zscore(c)
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(20).mean().shift(1)
    out["hh20"] = out["high"].rolling(20).max().shift(1)   # prior 20-bar high, excludes this bar
    out["ll10"] = out["low"].rolling(10).min().shift(1)
    return out
