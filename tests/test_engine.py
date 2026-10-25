import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from cryptodash.data import MarketData
from cryptodash.indicators import add_all, atr, hurst, rsi
from cryptodash.paper import PaperAccount
from cryptodash.pipeline import run_cycle
from cryptodash.risk import check
from cryptodash.settings import Settings
from cryptodash.signals import Plan
from cryptodash.sizing import size_trade

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def bars(closes, spread=1.0, start="2026-10-20"):
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.date_range(start, periods=len(c), freq="1h", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread,
                         "close": c, "volume": 1000.0}, index=idx)


def plan(entry=100.0, stop=98.0, target=104.0, symbol="TESTUSDT", bar="2026-10-25 10:00:00+00:00"):
    return Plan(symbol, "trend_pullback", "long", entry, stop, target, 80.0, [], bar)


# ---------------------------------------------------------------- indicators
def test_atr_constant_range():
    assert atr(bars([100] * 50, spread=1.0)).iloc[-1] == pytest.approx(2.0)


def test_rsi_only_rising_is_100():
    assert rsi(pd.Series(np.arange(1, 60, dtype=float))).iloc[-1] == pytest.approx(100.0)


def test_hurst_separates_regimes():
    rng = np.random.default_rng(0)
    noise = rng.normal(size=2000)
    reverting = np.diff(noise)                      # strongly anti-persistent returns
    trending = np.convolve(noise, np.ones(20) / 20, mode="valid")   # persistent returns
    h_rand, h_rev, h_tr = hurst(pd.Series(noise)), hurst(pd.Series(reverting)), hurst(pd.Series(trending))
    assert 0.4 < h_rand < 0.65
    assert h_rev < h_rand < h_tr


def test_hh20_excludes_current_bar():
    ind = add_all(bars(list(range(100, 160))))
    assert ind["close"].iloc[-1] > ind["hh20"].iloc[-1] - 1.0   # prior high, not this bar's


# ---------------------------------------------------------------- sizing
def test_risk_limited_size():
    s = Settings(fee_pct=0, slippage_pct=0, max_position_pct=100)
    sz = size_trade(plan(100, 98, 104), equity=1000, cash=1000, regime_mult=1.0, s=s)
    assert sz.qty == pytest.approx(10.0, rel=1e-3)        # $20 risk / $2 per unit
    assert sz.risk_usd == pytest.approx(20.0, rel=1e-3)
    assert sz.limited_by.startswith("risk")


def test_regime_multiplier_quarters_risk():
    s = Settings(fee_pct=0, slippage_pct=0, max_position_pct=100)
    full = size_trade(plan(), 1000, 1000, 1.0, s)
    quarter = size_trade(plan(), 1000, 1000, 0.25, s)
    assert quarter.qty == pytest.approx(full.qty / 4, rel=1e-3)


def test_cash_cap_includes_costs():
    s = Settings(max_position_pct=100)
    sz = size_trade(plan(100, 99.9, 101), equity=50, cash=50, regime_mult=1.0, s=s)
    assert sz.limited_by == "cash available"
    acct = PaperAccount(path=None, starting_balance=50)
    acct.open(plan(100, 99.9, 101), sz, s, NOW)                     # must not raise
    assert acct.cash >= 0


# ---------------------------------------------------------------- risk
def test_risk_blocks_bad_rr_and_daily_limit(tmp_path):
    s = Settings(max_daily_loss_usd=1.0, min_reward_risk=1.5)
    acct = PaperAccount(tmp_path / "a.json", 100)
    p = plan(100, 98, 101)                                          # R:R 0.5
    sz = size_trade(p, 100, 100, 1.0, s)                            # risks ~$2 > $1 limit
    ok, why = check(p, sz, 1.0, acct, s, NOW)
    assert not ok
    assert any("Reward:risk" in w for w in why)
    assert any("Daily loss limit" in w for w in why)


def test_risk_blocks_duplicate_and_cooldown(tmp_path):
    s = Settings()
    acct = PaperAccount(tmp_path / "a.json", 1000)
    acct.open(plan(), size_trade(plan(), 1000, 1000, 0.1, s), s, NOW)
    acct.closed.append({"pnl": -1.0, "closed_at": (NOW - timedelta(minutes=5)).isoformat(),
                        "opened_at": NOW.isoformat()})
    ok, why = check(plan(), size_trade(plan(), 1000, 1000, 1.0, s), 1.0, acct, s, NOW)
    assert not ok
    assert "Already holding this coin" in why
    assert any("Cooling down" in w for w in why)


def test_pass_when_everything_fits(tmp_path):
    s = Settings()
    acct = PaperAccount(tmp_path / "a.json", 50)
    p = plan(100, 98, 104)
    ok, why = check(p, size_trade(p, 50, 50, 1.0, s), 1.0, acct, s, NOW)
    assert ok, why


# ---------------------------------------------------------------- paper fills
def _after_signal(rows):
    idx = pd.date_range("2026-10-25 11:00", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


@pytest.mark.parametrize("rows,reason", [
    ([(100, 101, 97.5, 98)], "stop"),                    # wick through the stop
    ([(100, 105, 99, 104)], "target"),
    ([(100, 105, 97, 100)], "stop"),                     # both touched: assume the stop
    ([(96, 97, 95, 96)], "stop (gap)"),                  # opens below the stop
])
def test_paper_fills(tmp_path, rows, reason):
    s = Settings(max_position_pct=100)
    acct = PaperAccount(tmp_path / "a.json", 1000)
    p = plan()
    acct.open(p, size_trade(p, 1000, 1000, 1.0, s), s, NOW)
    closed = acct.update({"TESTUSDT": _after_signal(rows)}, s, NOW)
    assert closed and closed[0]["exit_reason"] == reason
    assert not acct.positions


def test_signal_candle_is_not_replayed(tmp_path):
    s = Settings()
    acct = PaperAccount(tmp_path / "a.json", 1000)
    p = plan()
    acct.open(p, size_trade(p, 1000, 1000, 1.0, s), s, NOW)
    signal = pd.DataFrame({"open": [100], "high": [110], "low": [90], "close": [100], "volume": [1]},
                          index=pd.DatetimeIndex([pd.Timestamp(p.bar_time)]))
    assert acct.update({"TESTUSDT": signal}, s, NOW) == []


def test_time_stop_and_idempotent_update(tmp_path):
    s = Settings(max_hold_bars=3)
    acct = PaperAccount(tmp_path / "a.json", 1000)
    p = plan()
    acct.open(p, size_trade(p, 1000, 1000, 1.0, s), s, NOW)
    two = _after_signal([(100, 101, 99, 100)] * 2)
    assert acct.update({"TESTUSDT": two}, s, NOW) == []
    assert acct.update({"TESTUSDT": two}, s, NOW) == []            # same candles again: still open
    three = _after_signal([(100, 101, 99, 100)] * 3)
    assert acct.update({"TESTUSDT": three}, s, NOW)[0]["exit_reason"] == "time stop"


def test_save_load_keeps_numbers_numeric(tmp_path):
    s = Settings()
    path = tmp_path / "a.json"
    acct = PaperAccount(path, 50)
    p = plan(np.float64(100.0), np.float64(98.0), np.float64(104.0))
    acct.open(p, size_trade(p, 50, 50, 1.0, s), s, NOW)
    acct.save()
    raw = json.loads(path.read_text())
    assert isinstance(raw["cash"], float)
    assert all(isinstance(raw["positions"][0][k], float) for k in ("entry", "stop", "target", "qty", "risk_usd"))
    again = PaperAccount.load(path)
    assert again.equity() == pytest.approx(acct.equity())


# ---------------------------------------------------------------- end to end
def test_demo_cycle_end_to_end(tmp_path):
    s = Settings()
    acct = PaperAccount(tmp_path / "a.json", s.starting_balance)
    seen_plan = False
    for h in range(0, 48, 6):
        out = run_cycle(MarketData("demo", h), s, acct, auto_trade=True)
        assert len(out["results"]) == s.top_n
        assert not [r.error for r in out["results"] if r.error]
        for r in out["plans"]:
            seen_plan = True
            assert r.plan.stop < r.plan.entry < r.plan.target
        assert acct.open_risk() + acct.realized_loss_today(out["now"]) <= s.max_daily_loss_usd + 1e-6
        assert len(acct.positions) <= s.max_open_trades
        assert acct.cash >= -1e-9
    assert seen_plan
