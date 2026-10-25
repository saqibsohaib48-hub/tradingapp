"""Scan -> regime -> setup -> size -> risk gate -> (optional) paper trade."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pandas as pd

from .data import MarketData
from .indicators import add_all
from .paper import PaperAccount
from .regime import Regime, classify
from .risk import check
from .settings import Settings
from .signals import Plan, build_plan
from .sizing import Size, size_trade


@dataclass
class CoinResult:
    symbol: str
    base: str
    price: float
    change_24h: float
    quote_volume: float
    ind: pd.DataFrame | None = None
    regime: Regime | None = None
    plan: Plan | None = None
    size: Size | None = None
    approved: bool = False
    blocks: list[str] = field(default_factory=list)
    error: str = ""

    def row(self) -> dict:
        last = self.ind.iloc[-1] if self.ind is not None else {}
        r = self.regime
        return {
            "Coin": self.base, "Price": self.price, "24h %": round(self.change_24h, 2),
            "Regime": r.quadrant if r else "-", "Direction": r.direction if r else "-",
            "ADX": r.adx if r else None, "Vol pctile": r.atr_rank if r else None,
            "Hurst": r.hurst if r else None,
            "Z-score": round(float(last["z"]), 2) if r else None,
            "RSI": round(float(last["rsi"]), 1) if r else None,
            "Playbook": r.playbook if r else self.error,
            "Setup": self.plan.strategy if self.plan else "",
        }


def scan(md: MarketData, s: Settings, workers: int = 8) -> list[CoinResult]:
    coins = md.universe(s.top_n, s.quote)

    def one(c: dict) -> CoinResult:
        res = CoinResult(c["symbol"], c["base"], c["price"], c["change_24h"], c["quote_volume"])
        try:
            df = md.klines(c["symbol"], s.interval, s.bars)
            if len(df) < 120:
                res.error = f"only {len(df)} candles"
                return res
            res.ind = add_all(df)
            res.price = float(df["close"].iloc[-1])
            res.regime = classify(res.ind)
            res.plan = build_plan(c["symbol"], res.ind, res.regime, s)
        except Exception as e:  # one bad coin must not stop the scan
            res.error = f"{e.__class__.__name__}: {e}"
        return res

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, coins))


def evaluate(results: list[CoinResult], account: PaperAccount, s: Settings, now) -> list[CoinResult]:
    """Size and risk-check every plan against the account as it is now."""
    eq = account.equity()
    for r in results:
        if not r.plan:
            continue
        r.size = size_trade(r.plan, eq, account.cash, r.regime.size_mult, s)
        r.approved, r.blocks = check(r.plan, r.size, r.regime.size_mult, account, s, now)
    return sorted([r for r in results if r.plan], key=lambda r: (not r.approved, -r.plan.score))


def candles_for_positions(md: MarketData, account: PaperAccount, s: Settings,
                          cache: dict[str, pd.DataFrame] | None = None) -> dict[str, pd.DataFrame]:
    cache = dict(cache or {})
    for pos in account.positions:
        if pos["symbol"] not in cache:
            try:
                cache[pos["symbol"]] = md.klines(pos["symbol"], s.interval, s.bars)
            except Exception:
                pass
    return cache


def run_cycle(md: MarketData, s: Settings, account: PaperAccount, auto_trade: bool) -> dict:
    """One full cycle: update open trades, scan, plan, and open approved trades if asked."""
    now = md.now()
    results = scan(md, s)
    candles = {r.symbol: r.ind for r in results if r.ind is not None}
    closed = account.update(candles_for_positions(md, account, s, candles), s, now)

    opened = []
    plans = evaluate(results, account, s, now)
    if auto_trade:
        for r in plans:
            if r.plan.score < s.min_score:
                continue
            # re-check against the account after each fill: limits are shared
            r.size = size_trade(r.plan, account.equity(), account.cash, r.regime.size_mult, s)
            r.approved, r.blocks = check(r.plan, r.size, r.regime.size_mult, account, s, now)
            if r.approved:
                opened.append(account.open(r.plan, r.size, s, now, r.regime.quadrant))
        plans = evaluate(results, account, s, now)
    account.save()
    return {"now": now, "results": results, "plans": plans, "closed": closed, "opened": opened}
