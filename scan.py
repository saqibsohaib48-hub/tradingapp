"""Headless scanner: one cycle from the terminal (good for cron or a scheduled task).

    python scan.py                 # live Binance, show plans
    python scan.py --auto-trade    # also open approved demo trades
    python scan.py --demo --offset 24 --auto-trade
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from cryptodash.data import DataError, MarketData
from cryptodash.paper import PaperAccount
from cryptodash.pipeline import run_cycle
from cryptodash.settings import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="use the synthetic demo market")
    ap.add_argument("--offset", type=float, default=0, help="demo clock offset in hours")
    ap.add_argument("--auto-trade", action="store_true", help="open approved trades on the demo account")
    ap.add_argument("--top", type=int, help="coins to scan")
    args = ap.parse_args()

    s = Settings.load()
    if args.top:
        s.top_n = args.top
    account = PaperAccount.load(starting_balance=s.starting_balance)
    try:
        out = run_cycle(MarketData("demo" if args.demo else "live", args.offset), s, account, args.auto_trade)
    except DataError as e:
        print(f"{e}. Try --demo.", file=sys.stderr)
        return 1

    pd.set_option("display.width", 200, "display.max_columns", 20)
    ok = [r for r in out["results"] if r.regime]
    print(f"\n{len(ok)} coins analysed at {out['now']:%Y-%m-%d %H:%M} UTC")
    print(pd.Series([r.regime.quadrant for r in ok]).value_counts().to_string())

    print("\nTRADE PLANS")
    if not out["plans"]:
        print("  none: no coin matches a setup right now")
    for r in out["plans"]:
        p = r.plan
        verdict = "PASS " if r.approved else "BLOCK"
        print(f"  {verdict} {r.base:6s} {p.strategy:15s} entry {p.entry:<10.6g} stop {p.stop:<10.6g} "
              f"target {p.target:<10.6g} R:R {p.reward_risk:.2f}  ${r.size.notional:.2f}  "
              f"loss@stop ${r.size.risk_usd:.2f}  score {p.score}")
        if r.blocks:
            print("        blocked: " + "; ".join(r.blocks))

    for t in out["opened"]:
        print(f"OPENED {t['symbol']} @ {t['entry']:.6g}  stop {t['stop']:.6g}  target {t['target']:.6g}")
    for t in out["closed"]:
        print(f"CLOSED {t['symbol']} {t['exit_reason']}  P&L ${t['pnl']:+.2f} ({t['r_multiple']:+.2f}R)")
    st = account.stats()
    print(f"\nDemo account: equity ${st['equity']:.2f} ({st['return_pct']:+.2f}%), "
          f"{int(st['closed_trades'])} closed, win rate {st['win_rate']:.0f}%, open {len(account.positions)}")
    print("Paper trading only. Not financial advice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
