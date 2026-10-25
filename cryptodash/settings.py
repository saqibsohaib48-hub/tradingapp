"""All tunable rules in one place, saved to data/settings.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    """Where settings and the demo account live. Override with CRYPTODASH_DATA."""
    return Path(os.environ.get("CRYPTODASH_DATA", _DEFAULT_DATA_DIR))


@dataclass
class Settings:
    # --- account (position-sizing / risk-management skills) ---
    starting_balance: float = 50.0       # demo account size in USD
    risk_per_trade_pct: float = 2.0      # fixed-fractional risk: % of equity lost if the stop fills
    max_position_pct: float = 50.0       # largest single position as % of equity (2 trades can coexist)
    max_daily_loss_usd: float = 10.0     # realized losses + open risk may not exceed this per UTC day
    max_open_trades: int = 2
    max_trades_per_day: int = 3
    min_reward_risk: float = 1.5         # target distance / stop distance
    max_drawdown_pct: float = 20.0       # circuit breaker: no new trades below this drawdown from peak
    cooldown_minutes_after_loss: int = 60  # anti revenge-trading pause
    min_order_usd: float = 5.0           # Binance minimum notional

    # --- costs (assumed for the paper account) ---
    fee_pct: float = 0.10                # per side
    slippage_pct: float = 0.05           # per fill

    # --- exits (exit-strategies skill) ---
    stop_atr_mult: float = 2.0           # trend / breakout stop = entry - k * ATR
    mr_stop_atr_mult: float = 1.5        # mean-reversion stop
    target_r: float = 2.0                # trend / breakout target = entry + R * risk
    max_hold_bars: int = 48              # time stop

    # --- scanner ---
    interval: str = "1h"
    bars: int = 300
    top_n: int = 30                      # coins by 24h USDT volume
    quote: str = "USDT"
    min_score: float = 60.0              # signals below this score are listed but not auto-traded

    def save(self, path: Path | None = None) -> None:
        path = path or data_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or data_dir() / "settings.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})
