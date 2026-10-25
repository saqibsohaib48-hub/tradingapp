# Crypto Skill Dashboard

A dashboard that scans the top crypto coins, works out each one's **market regime**, plans **long trades with entry, stop-loss and take-profit**, sizes them to your budget, runs a **risk gate**, and paper-trades them on a **demo account**.

It is built from the rules in these Claude skills: `regime-detection`, `mean-reversion`, `exit-strategies`, `position-sizing`, `risk-management` and `pre-trade-gate`.

> **Paper trading only. Not financial advice.** It reads public price data and never touches a real exchange account. Signals are rule checks, not predictions. Most people who trade short-term lose money.

## What you get

| Tab | What it shows |
|---|---|
| 🌍 **Market** | Every scanned coin: regime (clean trend, volatile trend, quiet range, choppy, transition), direction, ADX, volatility percentile, Hurst, z-score, RSI, and what the skill says to do |
| 🎯 **Trade plans** | Coins with a setup: **entry, stop-loss, take-profit**, reward:risk, size, loss at the stop, and **PASS / BLOCK** with the reason |
| 📊 **Chart** | Candles with EMA 20/50, Bollinger bands, ADX/±DI, and the plan's entry, stop and target lines |
| 💼 **Demo account** | Equity curve, open and closed trades, win rate, average R, drawdown, daily-loss meter, CSV export, reset |
| 🧠 **Skills** | How each skill's method is applied, with your current settings |

## Install and run

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/)).

```bash
git clone https://github.com/<your-username>/crypto-skill-dashboard.git
cd crypto-skill-dashboard
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Your browser opens at `http://localhost:8501`.

* **Live (Binance)** reads Binance's public candles. No API key or account needed.
* **Demo market (offline)** is a synthetic market for testing. Use the **+1h / +6h / +24h** buttons to fast-forward and watch demo trades hit their stop or target.

Turn on **Auto paper-trade** to have every PASS setup opened on the demo account automatically, and **Auto-refresh** to re-scan on a timer.

### From the terminal

```bash
python scan.py                      # live scan, print the trade plans
python scan.py --auto-trade         # also open PASS setups on the demo account
python scan.py --demo --offset 24   # demo market, 24 hours ahead
```

## How a trade is planned

1. **Scan**: the top 30 USDT pairs by 24h volume (stablecoins and leveraged tokens removed), 300 completed candles each.
2. **Regime** (`regime-detection`): ATR percentile × ADX gives the quadrant. Each regime sets a size multiplier: clean trend 1.0, volatile trend 0.5, quiet range 1.0, choppy 0.25, transition 0.25.
3. **Setup** (long spot only, at the last completed candle's close):
   * **Trend pullback**: ADX > 25, +DI > −DI, price > EMA20 > EMA50, dipped to the EMA20 and held, RSI 40 to 70.
   * **Breakout**: close above the prior 20-candle high, volume ≥ 1.5× average, ADX rising. Scores higher after a Bollinger squeeze.
   * **Mean reversion** (`mean-reversion`): quiet range, Hurst not trending, z-score ≤ −2, RSI ≤ 35. The target is the 20-candle mean.
4. **Exits** (`exit-strategies`): stop = entry − 2×ATR (1.5×ATR for mean reversion), target = 2R. A time stop after 48 candles.
5. **Size** (`position-sizing`): risk 2% of equity × the regime multiplier, capped at 50% of equity and by cash. Fees (0.1%) and slippage (0.05%) count toward the loss.
6. **Risk gate** (`risk-management`, `pre-trade-gate`): reward:risk ≥ 1.5, $5 minimum order, max 2 open trades, max 3 trades a day, daily loss limit ($10 = realized losses + open risk), 20% drawdown circuit breaker, a 60-minute cooldown after a loss, and no doubling up on a coin.

Every number above is editable in the sidebar under **Rules** and is saved to `data/settings.json`.

### How the demo account fills trades

Each open trade is walked through every completed candle after its signal candle. A low at or below the stop closes it at the stop; a high at or above the target closes it at the target. **If one candle touches both, the stop is assumed to fill first.** A candle that opens beyond the stop fills at its open (a gap). The account is saved in `data/paper_account.json`.

## Honest limits

* **Demo-market results mean nothing about real trading.** The synthetic market has trends built in, so trend setups tend to win there. Use it to see how the app behaves, then judge the rules on live data over weeks.
* Candles are completed candles; nothing is tick-by-tick. A real stop can fill worse than its price in a fast market.
* Long spot only: in a downtrend the dashboard plans nothing, on purpose.
* Binance public data may be blocked in some countries. If live mode shows "Binance unreachable", use the demo market or a different network.
* The Hurst exponent reads a little high on short samples.

## Project layout

```
app.py                  Streamlit dashboard
scan.py                 one scan from the terminal
cryptodash/
  data.py               Binance public candles + deterministic demo market
  indicators.py         ATR, ADX/±DI, RSI, EMA, Bollinger, z-score, Hurst
  regime.py             regime-detection 4-quadrant model
  signals.py            trend pullback, breakout, mean-reversion setups + exits
  sizing.py             position-sizing
  risk.py               risk-management / pre-trade gate
  paper.py              demo account
  pipeline.py           scan → regime → plan → size → gate → paper trade
  settings.py           every rule in one place
tests/                  pytest suite (python -m pytest)
```

Set `CRYPTODASH_DATA=/some/folder` to keep settings and the demo account somewhere else, for example to run two demo accounts side by side.

## License

MIT
