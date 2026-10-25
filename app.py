"""Crypto Skill Dashboard (Streamlit).

    streamlit run app.py

Scans the top coins, classifies each one's market regime, plans long trades
with entry / stop-loss / take-profit, sizes them, runs the risk gate and
paper-trades them on a demo account. Nothing here touches a real exchange
account: it only reads public price data.
"""
from __future__ import annotations

import json
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from cryptodash.data import DataError, MarketData
from cryptodash.paper import PaperAccount
from cryptodash.pipeline import run_cycle
from cryptodash.settings import Settings, data_dir

st.set_page_config(page_title="Crypto Skill Dashboard", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ state
def load_clock() -> float:
    try:
        return float(json.loads((data_dir() / "demo_clock.json").read_text())["offset_hours"])
    except Exception:
        return 0.0


def save_clock(hours: float) -> None:
    f = data_dir() / "demo_clock.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"offset_hours": hours}))


ss = st.session_state
ss.setdefault("settings", Settings.load())
ss.setdefault("offset", load_clock())
ss.setdefault("cycle", None)
ss.setdefault("need_run", True)
ss.setdefault("last_run_ts", 0.0)
ss.setdefault("log", [])
s: Settings = ss["settings"]

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("📈 Crypto Skill Dashboard")
    mode_label = st.radio("Market data", ["Live (Binance)", "Demo market (offline)"],
                          index=1 if ss.get("mode") == "demo" else 0,
                          help="Live reads Binance public candles, no API key. Demo is a synthetic market for testing.")
    mode = "demo" if mode_label.startswith("Demo") else "live"
    if ss.get("mode") != mode:
        ss["mode"], ss["need_run"] = mode, True

    if mode == "demo":
        st.caption(f"Demo clock: **+{ss['offset']:.0f} h** ahead of now")
        c1, c2, c3 = st.columns(3)
        for col, h in zip((c1, c2, c3), (1, 6, 24)):
            if col.button(f"+{h}h", use_container_width=True, help="Fast-forward the demo market"):
                ss["offset"] += h
                save_clock(ss["offset"])
                ss["need_run"] = True

    auto_trade = st.toggle("Auto paper-trade approved setups", value=ss.get("auto_trade", False))
    ss["auto_trade"] = auto_trade
    auto_refresh = st.toggle("Auto-refresh", value=ss.get("auto_refresh", False))
    ss["auto_refresh"] = auto_refresh
    refresh_min = st.number_input("Refresh every (minutes)", 1, 240, ss.get("refresh_min", 15),
                                  disabled=not auto_refresh)
    ss["refresh_min"] = refresh_min
    if st.button("🔄 Scan now", type="primary", use_container_width=True):
        ss["need_run"] = True

    with st.expander("⚙️ Rules (skills)", expanded=False):
        with st.form("rules"):
            st.markdown("**Position sizing**")
            starting = st.number_input("Demo balance ($)", 10.0, 1_000_000.0, s.starting_balance, 10.0)
            risk_pct = st.number_input("Risk per trade (% of equity)", 0.1, 10.0, s.risk_per_trade_pct, 0.1)
            pos_pct = st.number_input("Max position (% of equity)", 5.0, 100.0, s.max_position_pct, 5.0)
            st.markdown("**Risk management**")
            daily = st.number_input("Max loss per day ($)", 0.5, 1_000_000.0, s.max_daily_loss_usd, 0.5)
            max_open = st.number_input("Max open trades", 1, 20, s.max_open_trades)
            max_day = st.number_input("Max trades per day", 1, 50, s.max_trades_per_day)
            min_rr = st.number_input("Min reward:risk", 0.5, 5.0, s.min_reward_risk, 0.1)
            max_dd = st.number_input("Circuit breaker drawdown (%)", 1.0, 90.0, s.max_drawdown_pct, 1.0)
            cool = st.number_input("Cooldown after a loss (min)", 0, 1440, s.cooldown_minutes_after_loss)
            st.markdown("**Exit strategies**")
            stop_k = st.number_input("Trend stop (x ATR)", 0.5, 6.0, s.stop_atr_mult, 0.25)
            mr_k = st.number_input("Mean-reversion stop (x ATR)", 0.5, 6.0, s.mr_stop_atr_mult, 0.25)
            tgt_r = st.number_input("Trend target (R)", 0.5, 10.0, s.target_r, 0.25)
            hold = st.number_input("Time stop (candles)", 1, 500, s.max_hold_bars)
            st.markdown("**Scanner**")
            top_n = st.number_input("Coins to scan (by volume)", 5, 100, s.top_n)
            interval = st.selectbox("Candle size", ["15m", "1h", "4h", "1d"],
                                    index=["15m", "1h", "4h", "1d"].index(s.interval))
            min_score = st.number_input("Min score to auto-trade", 0.0, 100.0, s.min_score, 5.0)
            if st.form_submit_button("Save rules"):
                s.starting_balance, s.risk_per_trade_pct, s.max_position_pct = starting, risk_pct, pos_pct
                s.max_daily_loss_usd, s.max_open_trades, s.max_trades_per_day = daily, int(max_open), int(max_day)
                s.min_reward_risk, s.max_drawdown_pct, s.cooldown_minutes_after_loss = min_rr, max_dd, int(cool)
                s.stop_atr_mult, s.mr_stop_atr_mult, s.target_r, s.max_hold_bars = stop_k, mr_k, tgt_r, int(hold)
                s.top_n, s.interval, s.min_score = int(top_n), interval, min_score
                s.save()
                ss["need_run"] = True
                st.success("Saved")

# --------------------------------------------------------- auto refresh
if auto_refresh:
    @st.fragment(run_every=30)
    def _ticker():
        if time.time() - ss["last_run_ts"] >= ss["refresh_min"] * 60:
            ss["need_run"] = True
            st.rerun(scope="app")
    _ticker()

# ---------------------------------------------------------------- cycle
account = PaperAccount.load(starting_balance=s.starting_balance)
md = MarketData(mode, ss["offset"])

if ss["need_run"]:
    with st.spinner("Scanning coins and running the skills..."):
        try:
            ss["cycle"] = run_cycle(md, s, account, auto_trade)
            ss["last_run_ts"] = time.time()
            stamp = ss["cycle"]["now"].strftime("%Y-%m-%d %H:%M UTC")
            for t in ss["cycle"]["opened"]:
                ss["log"].insert(0, f"{stamp} · OPENED {t['symbol']} {t['strategy']} @ {t['entry']:.6g}")
            for t in ss["cycle"]["closed"]:
                ss["log"].insert(0, f"{stamp} · CLOSED {t['symbol']} {t['exit_reason']} P&L ${t['pnl']:+.2f}")
        except DataError as e:
            st.error(f"{e}. Your network may block Binance. Switch to **Demo market** in the sidebar to test.")
        ss["need_run"] = False

cycle = ss["cycle"]
st.warning("**Paper trading only. Not financial advice.** Signals are rule checks from the installed skills, "
           "not predictions. Demo-market results come from synthetic prices and say nothing about real performance.",
           icon="⚠️")
if not cycle:
    st.stop()

results, plans, now = cycle["results"], cycle["plans"], cycle["now"]
ok = [r for r in results if r.regime]

tab_mkt, tab_plans, tab_chart, tab_acct, tab_skills = st.tabs(
    ["🌍 Market", "🎯 Trade plans", "📊 Chart", "💼 Demo account", "🧠 Skills"])

# ---------------------------------------------------------------- market
with tab_mkt:
    st.caption(f"{'Demo market' if mode == 'demo' else 'Binance'} · {len(ok)} of {len(results)} coins "
               f"analysed · last candle closed before {now:%Y-%m-%d %H:%M} UTC · {s.interval} candles")
    counts = pd.Series([r.regime.quadrant for r in ok]).value_counts()
    cols = st.columns(5)
    for col, q in zip(cols, ["Clean trend", "Volatile trend", "Quiet range", "Choppy chaos", "Transition"]):
        col.metric(q, int(counts.get(q, 0)))
    ups = sum(r.regime.direction == "up" for r in ok)
    downs = sum(r.regime.direction == "down" for r in ok)
    st.markdown(f"**Breadth:** {ups} coins trending up, {downs} trending down, {len(ok) - ups - downs} neutral.")
    table = pd.DataFrame([r.row() for r in results])
    st.dataframe(table, use_container_width=True, hide_index=True,
                 column_config={"Price": st.column_config.NumberColumn(format="%.6g"),
                                "Vol pctile": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})

# ----------------------------------------------------------------- plans
with tab_plans:
    if not plans:
        st.info("No coin matches a setup right now. The skills say: wait. Re-scan later or fast-forward the demo.")
    else:
        rows = []
        for r in plans:
            p = r.plan
            rows.append({
                "Verdict": "✅ PASS" if r.approved else "⛔ BLOCK", "Coin": r.base, "Setup": p.strategy,
                "Score": p.score, "Entry": p.entry, "Stop-loss": p.stop, "Take-profit": p.target,
                "R:R": round(p.reward_risk, 2), "Qty": r.size.qty, "Size $": r.size.notional,
                "Loss at stop $": r.size.risk_usd, "Regime": r.regime.quadrant,
                "Why / blocked by": " · ".join(r.blocks) if r.blocks else " · ".join(p.reasons),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={c: st.column_config.NumberColumn(format="%.6g")
                                    for c in ("Entry", "Stop-loss", "Take-profit", "Qty")})
        approved = [r for r in plans if r.approved]
        if approved:
            c1, c2 = st.columns([3, 1])
            pick = c1.selectbox("Paper-trade a passed setup", [r.base for r in approved])
            if c2.button("Open demo trade", use_container_width=True):
                r = next(x for x in approved if x.base == pick)
                t = account.open(r.plan, r.size, s, now, r.regime.quadrant)
                account.save()
                ss["log"].insert(0, f"{now:%Y-%m-%d %H:%M UTC} · OPENED {t['symbol']} (manual) @ {t['entry']:.6g}")
                ss["need_run"] = True
                st.rerun()
    st.caption("Entry = close of the last completed candle. Long spot only. A stop is an order, not a promise: "
               "in a fast market it can fill below the stop price.")

# ----------------------------------------------------------------- chart
with tab_chart:
    names = [r.base for r in ok]
    if names:
        default = plans[0].base if plans else names[0]
        coin = st.selectbox("Coin", names, index=names.index(default))
        r = next(x for x in ok if x.base == coin)
        d = r.ind.tail(150)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.03)
        fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name=coin), 1, 1)
        for col, name, color in (("ema20", "EMA 20", "#f5a623"), ("ema50", "EMA 50", "#7b61ff"),
                                 ("bb_up", "BB upper", "rgba(128,128,128,.5)"),
                                 ("bb_lo", "BB lower", "rgba(128,128,128,.5)")):
            fig.add_trace(go.Scatter(x=d.index, y=d[col], name=name, line=dict(width=1, color=color)), 1, 1)
        if r.plan:
            for y, label, color in ((r.plan.entry, "Entry", "#2f80ed"), (r.plan.stop, "Stop", "#eb5757"),
                                    (r.plan.target, "Target", "#27ae60")):
                fig.add_hline(y=y, line_dash="dash", line_color=color, annotation_text=f"{label} {y:.6g}", row=1, col=1)
        for pos in account.positions:
            if pos["symbol"] == r.symbol:
                fig.add_hline(y=pos["stop"], line_color="#eb5757", annotation_text="open trade stop", row=1, col=1)
                fig.add_hline(y=pos["target"], line_color="#27ae60", annotation_text="open trade target", row=1, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=d.adx, name="ADX", line=dict(color="#333")), 2, 1)
        fig.add_trace(go.Scatter(x=d.index, y=d.plus_di, name="+DI", line=dict(color="#27ae60")), 2, 1)
        fig.add_trace(go.Scatter(x=d.index, y=d.minus_di, name="-DI", line=dict(color="#eb5757")), 2, 1)
        fig.add_hline(y=25, line_dash="dot", row=2, col=1)
        fig.update_layout(height=620, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.02))
        st.plotly_chart(fig, use_container_width=True)
        g = r.regime
        a, b, c, e = st.columns(4)
        a.metric("Regime", g.quadrant, g.direction)
        b.metric("ADX (trend strength)", g.adx)
        c.metric("Hurst", g.hurst, g.behaviour.replace("_", " "))
        e.metric("Volatility percentile", f"{g.atr_rank:.0%}", g.volatility)
        st.info(f"**Playbook:** {g.playbook}")

# --------------------------------------------------------------- account
with tab_acct:
    stt = account.stats()
    m = st.columns(6)
    m[0].metric("Equity", f"${stt['equity']:.2f}", f"{stt['return_pct']:+.2f}%")
    m[1].metric("Cash", f"${stt['cash']:.2f}")
    m[2].metric("Closed trades", int(stt["closed_trades"]))
    m[3].metric("Win rate", f"{stt['win_rate']:.0f}%")
    m[4].metric("Avg R", f"{stt['avg_r']:+.2f}")
    m[5].metric("Max drawdown", f"{stt['max_drawdown_pct']:.1f}%")
    used = account.realized_loss_today(now) + account.open_risk()
    st.progress(min(used / s.max_daily_loss_usd, 1.0),
                text=f"Daily loss budget used: ${used:.2f} of ${s.max_daily_loss_usd:g} (realized losses + open risk)")

    if account.equity_history:
        eq = pd.DataFrame(account.equity_history)
        eq["time"] = pd.to_datetime(eq["time"], format="ISO8601")
        st.line_chart(eq.set_index("time")["equity"], height=220)

    st.subheader("Open trades")
    if account.positions:
        op = pd.DataFrame(account.positions)
        op["last"] = op["symbol"].map(account.last_prices)
        op["unrealized $"] = (op["last"] - op["entry"]) * op["qty"]
        st.dataframe(op[["id", "symbol", "strategy", "entry", "stop", "target", "last", "qty", "risk_usd",
                         "unrealized $", "bars_held", "opened_at"]], use_container_width=True, hide_index=True)
        c1, c2 = st.columns([3, 1])
        pid = c1.selectbox("Close a trade at the last price", op["id"] + " · " + op["symbol"])
        if c2.button("Close trade", use_container_width=True):
            t = account.close_manually(pid.split(" · ")[0], s, now)
            account.save()
            ss["log"].insert(0, f"{now:%Y-%m-%d %H:%M UTC} · CLOSED {t['symbol']} manual P&L ${t['pnl']:+.2f}")
            st.rerun()
    else:
        st.caption("No open trades.")

    st.subheader("Closed trades")
    if account.closed:
        cl = pd.DataFrame(account.closed)[["symbol", "strategy", "entry", "exit", "exit_reason", "pnl",
                                           "r_multiple", "opened_at", "closed_at"]]
        st.dataframe(cl.iloc[::-1], use_container_width=True, hide_index=True)
        st.download_button("Download trades (CSV)", cl.to_csv(index=False), "demo_trades.csv", "text/csv")
    else:
        st.caption("No closed trades yet.")

    if ss["log"]:
        with st.expander("Activity log"):
            st.text("\n".join(ss["log"][:200]))

    with st.expander("Reset demo account"):
        sure = st.checkbox(f"Yes, wipe all demo trades and restart at ${s.starting_balance:g}")
        if st.button("Reset", disabled=not sure):
            account.reset(s.starting_balance)
            ss["log"] = []
            if mode == "demo":
                ss["offset"] = 0.0
                save_clock(0.0)
            ss["need_run"] = True
            st.rerun()

# ---------------------------------------------------------------- skills
with tab_skills:
    st.markdown(f"""
### How each skill is used

| Skill | What it does here | Your setting |
|---|---|---|
| **regime-detection** | Volatility (ATR percentile) x trend (ADX) gives 4 regimes plus "Transition". Direction from the 20 EMA slope and ±DI. Each regime sets a size multiplier: clean trend 1.0, volatile trend 0.5, quiet range 1.0, choppy 0.25, transition 0.25. | - |
| **mean-reversion** | Hurst exponent on returns (< 0.45 reverting, > 0.6 trending) and a 20-bar z-score. A long fires in a quiet range when z ≤ −2 and RSI ≤ 35; target = the mean. | stop {s.mr_stop_atr_mult:g} x ATR |
| **exit-strategies** | ATR stops, an R-multiple target, a time stop, and a conservative "stop first" rule when one candle hits both. | stop {s.stop_atr_mult:g} x ATR, target {s.target_r:g}R, {s.max_hold_bars} candles |
| **position-sizing** | Fixed-fractional risk x regime multiplier, capped by max position and cash. Fees and slippage count toward the loss at the stop. | {s.risk_per_trade_pct:g}% risk, {s.max_position_pct:g}% cap |
| **risk-management** | Daily loss limit (realized + open risk), max open trades, trades per day, drawdown circuit breaker, minimum R:R, no doubling up on a coin. | ${s.max_daily_loss_usd:g}/day, {s.max_open_trades} open, {s.max_trades_per_day}/day, {s.max_drawdown_pct:g}% DD |
| **pre-trade-gate** | Cooldown after a loss (no revenge trading) and the $5 minimum order. | {s.cooldown_minutes_after_loss} min |
| **coingecko-api / data** | Live candles come from Binance's public API (no key). The coin list is the top {s.top_n} USDT pairs by 24h volume, minus stablecoins and leveraged tokens. | {s.interval} candles |

### The three setups (long spot only)

* **Trend pullback**: clean uptrend (ADX > 25, +DI > −DI, price > EMA20 > EMA50), price dipped to the 20 EMA and held, RSI 40 to 70.
* **Breakout**: close above the prior 20-candle high on 1.5x volume with ADX rising; scores higher after a Bollinger squeeze.
* **Mean reversion**: quiet range, price 2+ standard deviations under its 20-candle mean, RSI oversold.

Coins in a downtrend get no long setup at all. That's on purpose.
""")
