"""Demo (paper) trading account, saved to data/paper_account.json.

Fills are simulated from completed candles: the stop and target are checked
bar by bar after entry. If one candle touches both, the stop is assumed to have
filled first (the conservative choice). A candle that opens beyond the stop
fills at its open (a gap). Fees and slippage apply to every fill.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .settings import Settings, data_dir
from .signals import Plan
from .sizing import Size


def _ts(x) -> datetime:
    return pd.Timestamp(x).to_pydatetime() if not isinstance(x, datetime) else x


class PaperAccount:
    def __init__(self, path: Path | None = None, starting_balance: float = 50.0):
        self.path = path or data_dir() / "paper_account.json"
        self.starting_balance = starting_balance
        self.cash = starting_balance
        self.peak_equity = starting_balance
        self.positions: list[dict] = []
        self.closed: list[dict] = []
        self.equity_history: list[dict] = []
        self.last_prices: dict[str, float] = {}

    # ------------------------------------------------------------ storage
    @classmethod
    def load(cls, path: Path | None = None, starting_balance: float = 50.0) -> "PaperAccount":
        acct = cls(path, starting_balance)
        if acct.path.exists():
            raw = json.loads(acct.path.read_text())
            for k in ("starting_balance", "cash", "peak_equity", "positions", "closed",
                      "equity_history", "last_prices"):
                if k in raw:
                    setattr(acct, k, raw[k])
        return acct

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: getattr(self, k) for k in ("starting_balance", "cash", "peak_equity", "positions",
                                              "closed", "equity_history", "last_prices")}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def reset(self, starting_balance: float) -> None:
        self.__init__(self.path, starting_balance)
        self.save()

    # ------------------------------------------------------------ figures
    def equity(self) -> float:
        held = sum(p["qty"] * self.last_prices.get(p["symbol"], p["entry"]) for p in self.positions)
        return round(float(self.cash + held), 4)

    def drawdown_pct(self) -> float:
        peak = max(self.peak_equity, self.equity())
        return 0.0 if peak <= 0 else (peak - self.equity()) / peak * 100

    def open_risk(self) -> float:
        return sum(p["risk_usd"] for p in self.positions)

    def realized_loss_today(self, now: datetime) -> float:
        day = now.astimezone(timezone.utc).date()
        pnl = sum(t["pnl"] for t in self.closed if _ts(t["closed_at"]).date() == day)
        return max(-pnl, 0.0)

    def trades_opened_today(self, now: datetime) -> int:
        day = now.astimezone(timezone.utc).date()
        opened = [p["opened_at"] for p in self.positions] + [t["opened_at"] for t in self.closed]
        return sum(1 for t in opened if _ts(t).date() == day)

    def last_loss_time(self) -> datetime | None:
        losses = [_ts(t["closed_at"]) for t in self.closed if t["pnl"] < 0]
        return max(losses) if losses else None

    def stats(self) -> dict:
        pnls = [t["pnl"] for t in self.closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        eq = [e["equity"] for e in self.equity_history] or [self.equity()]
        peak, max_dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak * 100 if peak else 0)
        out = {
            "equity": self.equity(),
            "cash": round(self.cash, 4),
            "return_pct": (self.equity() / self.starting_balance - 1) * 100,
            "closed_trades": len(pnls),
            "win_rate": len(wins) / len(pnls) * 100 if pnls else 0.0,
            "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf") if wins else 0.0,
            "avg_r": sum(t["r_multiple"] for t in self.closed) / len(pnls) if pnls else 0.0,
            "realized_pnl": sum(pnls),
            "max_drawdown_pct": max_dd,
        }
        return {k: float(v) for k, v in out.items()}

    # ------------------------------------------------------------ trading
    def open(self, plan: Plan, size: Size, s: Settings, now: datetime, strategy_note: str = "") -> dict:
        slip = s.slippage_pct / 100
        fill = plan.entry * (1 + slip)
        fee = size.qty * fill * s.fee_pct / 100
        cost = size.qty * fill + fee
        if cost > self.cash + 1e-9:
            raise ValueError("Not enough demo cash")
        self.cash -= cost
        pos = {
            "id": uuid.uuid4().hex[:8], "symbol": plan.symbol, "strategy": plan.strategy,
            "entry": float(fill), "planned_entry": float(plan.entry), "stop": float(plan.stop),
            "target": float(plan.target), "qty": float(size.qty), "risk_usd": float(size.risk_usd),
            "fees": float(fee),
            "opened_at": now.isoformat(), "signal_bar": plan.bar_time, "bars_held": 0,
            "note": strategy_note,
        }
        self.cash = float(self.cash)
        self.positions.append(pos)
        self.last_prices[plan.symbol] = float(plan.entry)
        return pos

    def _close(self, pos: dict, price: float, reason: str, when, s: Settings) -> dict:
        fill = float(price) * (1 - s.slippage_pct / 100)
        fee = pos["qty"] * fill * s.fee_pct / 100
        self.cash = float(self.cash + pos["qty"] * fill - fee)
        pnl = pos["qty"] * (fill - pos["entry"]) - pos["fees"] - fee
        risk = pos["risk_usd"] or 1e-9
        trade = {**pos, "exit": fill, "exit_reason": reason, "closed_at": pd.Timestamp(when).isoformat(),
                 "fees": float(pos["fees"] + fee), "pnl": round(float(pnl), 4),
                 "r_multiple": round(float(pnl / risk), 2)}
        self.closed.append(trade)
        return trade

    def update(self, candles: dict[str, pd.DataFrame], s: Settings, now: datetime) -> list[dict]:
        """Walk each open position through the candles completed since it opened."""
        events = []
        still_open = []
        for pos in self.positions:
            df = candles.get(pos["symbol"])
            if df is None or df.empty:
                still_open.append(pos)
                continue
            # Entry fills at the signal candle's close, so every later completed candle counts.
            # The walk restarts from the signal candle each time, which makes update() idempotent.
            after = df[df.index > pd.Timestamp(pos["signal_bar"])]
            closed = None
            pos["bars_held"] = 0
            for t, bar in after.iterrows():
                pos["bars_held"] += 1
                if bar["open"] <= pos["stop"]:
                    closed = self._close(pos, bar["open"], "stop (gap)", t, s); break
                if bar["low"] <= pos["stop"]:
                    closed = self._close(pos, pos["stop"], "stop", t, s); break
                if bar["high"] >= pos["target"]:
                    closed = self._close(pos, pos["target"], "target", t, s); break
                if pos["bars_held"] >= s.max_hold_bars:
                    closed = self._close(pos, bar["close"], "time stop", t, s); break
            if closed:
                events.append(closed)
            else:
                still_open.append(pos)
            self.last_prices[pos["symbol"]] = float(df["close"].iloc[-1])
        self.positions = still_open
        eq = self.equity()
        self.peak_equity = max(self.peak_equity, eq)
        if not self.equity_history or self.equity_history[-1]["equity"] != eq:
            self.equity_history.append({"time": now.isoformat(), "equity": eq})
        return events

    def close_manually(self, pos_id: str, s: Settings, now: datetime) -> dict | None:
        for pos in self.positions:
            if pos["id"] == pos_id:
                price = self.last_prices.get(pos["symbol"], pos["entry"])
                trade = self._close(pos, price, "manual", now, s)
                self.positions = [p for p in self.positions if p["id"] != pos_id]
                return trade
        return None
