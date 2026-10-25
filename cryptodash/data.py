"""Market data: live Binance public candles, or a deterministic demo market.

Live mode needs no API key. It reads Binance's public market-data endpoints
(data-api.binance.vision first, api.binance.com as fallback).

Demo mode generates a synthetic market that switches between trends, ranges
and volatile chop. It is deterministic per coin and grows one candle per real
hour, so the paper account can be tested offline. A clock offset lets you
fast-forward the demo market to see trades resolve.
"""
from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

BASE_URLS = ["https://data-api.binance.vision", "https://api.binance.com"]
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "AEUR", "USDE", "PYUSD",
           "XUSD", "USD1", "BFUSD", "RLUSD", "EURI", "PAXG", "WBTC", "WBETH", "BNSOL"}
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")
INTERVAL_HOURS = {"15m": 0.25, "1h": 1, "4h": 4, "1d": 24}


class DataError(RuntimeError):
    pass


def _get(path: str, params: dict) -> object:
    last = None
    for base in BASE_URLS:
        try:
            r = requests.get(base + path, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
            last = f"{base}: HTTP {r.status_code}"
        except requests.RequestException as e:
            last = f"{base}: {e.__class__.__name__}"
    raise DataError(f"Binance unreachable ({last})")


# ---------------------------------------------------------------- live ----
def live_universe(top_n: int, quote: str = "USDT") -> list[dict]:
    rows = _get("/api/v3/ticker/24hr", {})
    out = []
    for t in rows:
        sym = t["symbol"]
        if not sym.endswith(quote):
            continue
        base = sym[: -len(quote)]
        if base in STABLES or base.endswith(LEVERAGED) or float(t["quoteVolume"]) <= 0:
            continue
        out.append({"symbol": sym, "base": base, "price": float(t["lastPrice"]),
                    "change_24h": float(t["priceChangePercent"]),
                    "quote_volume": float(t["quoteVolume"])})
    out.sort(key=lambda r: r["quote_volume"], reverse=True)
    return out[:top_n]


def live_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    rows = _get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": min(limit + 1, 1000)})
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "qv", "n", "tb", "tq", "ignore"])
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    df = df[df["close_time"] < now_ms]                       # completed candles only
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df.tail(limit)


# ---------------------------------------------------------------- demo ----
DEMO_COINS = {
    "BTC": 80000, "ETH": 2350, "SOL": 100, "XRP": 1.3, "BNB": 600, "DOGE": 0.18,
    "ADA": 0.55, "AVAX": 24, "LINK": 14, "DOT": 4.2, "TRX": 0.32, "LTC": 85,
    "SHIB": 0.000013, "TON": 3.1, "NEAR": 2.6, "APT": 5.2, "SUI": 2.4, "ARB": 0.42,
    "OP": 0.75, "PEPE": 0.0000095, "INJ": 13, "ATOM": 4.6, "FIL": 2.7, "UNI": 7.5,
    "AAVE": 260, "HBAR": 0.19, "ETC": 17, "XLM": 0.33, "RNDR": 4.4, "SEI": 0.21,
}
DEMO_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEMO_BARS = 24 * 365 * 2


@lru_cache(maxsize=64)
def _demo_series(base: str) -> pd.DataFrame:
    """Two years of hourly bars, generated once per coin and sliced by time."""
    rng = np.random.default_rng(zlib.crc32(base.encode()))
    price0 = DEMO_COINS.get(base, 1.0)
    n = DEMO_BARS
    rets = np.empty(n)
    vols = np.empty(n)
    vmult = np.empty(n)
    i = 0
    while i < n:
        seg = int(rng.integers(30, 160))
        kind = rng.choice(["up", "down", "range", "chop", "squeeze"], p=[0.24, 0.24, 0.24, 0.13, 0.15])
        sigma = {"up": 0.006, "down": 0.007, "range": 0.004, "chop": 0.014, "squeeze": 0.0022}[kind]
        drift = {"up": 0.0011, "down": -0.0011}.get(kind, 0.0)
        level = 0.0
        for j in range(min(seg, n - i)):
            shock = rng.standard_t(4) * sigma / np.sqrt(2)
            if kind in ("range", "squeeze"):
                r = -0.08 * level + shock       # Ornstein-Uhlenbeck pull back to the range mid
                level += r
            else:
                r = drift + shock
            rets[i + j], vols[i + j] = r, sigma
            vmult[i + j] = 1.0 + 4.0 * (abs(shock) > 2.2 * sigma)
        i += seg
    close = price0 * np.exp(np.cumsum(rets) - rets.sum() * 0.5)   # centre the path near today's level
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, 1, (2, n))) * vols * 0.6
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])
    volume = rng.lognormal(10, 0.35, n) * vmult * (1 + 40 * vols)
    idx = pd.date_range(DEMO_START, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def demo_klines(symbol: str, interval: str, limit: int, now: datetime) -> pd.DataFrame:
    df = _demo_series(symbol.replace("USDT", ""))
    df = df[df.index + pd.Timedelta(hours=1) <= now]         # completed candles only
    hours = INTERVAL_HOURS.get(interval, 1)
    if hours > 1:
        df = df.resample(f"{int(hours)}h").agg({"open": "first", "high": "max", "low": "min",
                                                "close": "last", "volume": "sum"}).dropna()
    return df.tail(limit)


def demo_universe(top_n: int, now: datetime) -> list[dict]:
    out = []
    for base in DEMO_COINS:
        df = demo_klines(base + "USDT", "1h", 25, now)
        last, prev = df["close"].iloc[-1], df["close"].iloc[0]
        out.append({"symbol": base + "USDT", "base": base, "price": float(last),
                    "change_24h": float((last / prev - 1) * 100),
                    "quote_volume": float((df["close"] * df["volume"]).sum())})
    out.sort(key=lambda r: r["quote_volume"], reverse=True)
    return out[:top_n]


# ------------------------------------------------------------- facade -----
class MarketData:
    def __init__(self, mode: str = "live", clock_offset_hours: float = 0.0):
        if mode not in ("live", "demo"):
            raise ValueError("mode must be 'live' or 'demo'")
        self.mode = mode
        self.offset = timedelta(hours=clock_offset_hours if mode == "demo" else 0)

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self.offset

    def universe(self, top_n: int, quote: str = "USDT") -> list[dict]:
        if self.mode == "demo":
            return demo_universe(top_n, self.now())
        return live_universe(top_n, quote)

    def klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if self.mode == "demo":
            return demo_klines(symbol, interval, limit, self.now())
        return live_klines(symbol, interval, limit)
