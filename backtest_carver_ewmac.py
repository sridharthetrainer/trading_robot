"""
backtest_carver_ewmac.py -- Robert Carver's "Systematic Trading" EWMAC
(exponentially-weighted moving-average crossover) trend system with
volatility-targeted position sizing, tested on NIFTY as a futures-style
underlying-directional system (Carver's own book scope -- futures, not
options; a real-option execution translation would be a separate, later
step, same as this project's other underlying-proxy backtests).

Methodology (Carver's own published book + blog, borrowed as methodology
only, not code, per this project's standing rule):
  - Compute several EWMAC forecasts (fast EMA - slow EMA of price) at
    different speed pairs. Reduced to 3 variants -- EWMAC(4,16),
    EWMAC(8,32), EWMAC(16,64) -- given this project's available daily
    NIFTY history (334 bars) is far shorter than Carver's typical
    multi-decade futures datasets; the slower published variants
    (EWMAC(32,128), EWMAC(64,256)) would leave almost no live window
    after their own warmup. A disclosed simplification, not a hidden one.
  - Normalize each raw EWMAC by a rolling estimate of price volatility
    (in points, not %), then apply Carver's own published forecast
    scalars (calibrated so average |forecast| ~= 10 over his own
    reference dataset): 7.5 for (4,16), 5.3 for (8,32), 3.75 for (16,64).
    Cap each variant's scaled forecast at +/-20.
  - Combine (equal-weight average across the 3) and apply a Forecast
    Diversification Multiplier (Carver's published approximate value for
    a 3-variant same-asset-class trend combo, ~1.2, to counter the
    variance-shrinkage of averaging correlated signals). Cap the
    combined forecast at +/-20.
  - Volatility-targeted position: position_units = (target_annual_risk_pct
    * capital) / annualized_price_vol * (forecast / 10). TARGET_RISK_PCT
    = 0.20 (Carver's own commonly-cited default starting point).
  - Daily rebalance. Real FUT-instrument transaction costs
    (nse_cost_model.py's single_leg_cost, NOT round_trip_cost -- this is
    a continuously-rebalanced running position, not a discrete one-entry-
    one-exit trade, so each day's incremental turnover is priced as a
    single leg in the direction of that day's position CHANGE) applied
    to the day's turnover. No no-trade buffer/threshold is applied (a
    real implementation would only rebalance on a meaningful forecast
    change), so this likely OVERSTATES real trading costs -- a
    conservative simplification, not an optimistic one.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

import numpy as np
import pandas as pd

from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import DEFAULT_LOT_SIZE

CANDLE_DB = "candle_cache.db"
TARGET_RISK_PCT = 0.20
CAPITAL = 1_000_000.0
VOL_LOOKBACK = 25
EWMAC_VARIANTS = [(4, 16, 7.5), (8, 32, 5.3), (16, 64, 3.75)]
FDM = 1.2
FORECAST_CAP = 20.0
LOT_SIZE = DEFAULT_LOT_SIZE   # position sizing expressed in real, tradeable lots
                              # (n_lots), not raw fractional units -- more
                              # interpretable/auditable than a bare rupee-per-point
                              # sensitivity factor, though algebraically equivalent


def _load_daily(symbol: str = "NIFTY") -> pd.Series:
    con = sqlite3.connect(CANDLE_DB)
    df = pd.read_sql_query(
        "SELECT timestamp, close FROM candles WHERE symbol=? AND interval='1d' ORDER BY timestamp",
        con, params=(symbol,))
    con.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.set_index("timestamp")["close"]


def _combined_forecast(price: pd.Series):
    daily_diff = price.diff()
    price_vol = daily_diff.ewm(span=VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std()

    scaled_forecasts = []
    for fast, slow, scalar in EWMAC_VARIANTS:
        ema_fast = price.ewm(span=fast, adjust=False).mean()
        ema_slow = price.ewm(span=slow, adjust=False).mean()
        raw = ema_fast - ema_slow
        normalized = raw / price_vol.replace(0, np.nan)
        scaled = (normalized * scalar).clip(-FORECAST_CAP, FORECAST_CAP)
        scaled_forecasts.append(scaled)

    combined = sum(scaled_forecasts) / len(scaled_forecasts)
    combined = (combined * FDM).clip(-FORECAST_CAP, FORECAST_CAP)
    return combined, price_vol


def backtest_carver_ewmac(symbol: str = "NIFTY", capital: float = CAPITAL,
                           target_risk_pct: float = TARGET_RISK_PCT,
                           verbose: bool = True) -> Dict[str, Any]:
    price = _load_daily(symbol)
    forecast, price_vol = _combined_forecast(price)
    ann_vol_points = price_vol * np.sqrt(252)

    instrument_value_vol_per_lot = LOT_SIZE * ann_vol_points.replace(0, np.nan)
    n_lots = (target_risk_pct * capital / instrument_value_vol_per_lot) * (forecast / 10.0)
    position = n_lots * LOT_SIZE   # index-point-equivalent units for P&L calc below
    valid = position.notna() & price.notna()
    position = position[valid]
    price_v = price[valid]

    cost_model = get_cost_model()
    rows = []
    prev_pos = 0.0
    prev_price = None
    for ts in position.index:
        pos = float(position.loc[ts])
        px = float(price_v.loc[ts])
        gross = prev_pos * (px - prev_price) if prev_price is not None else 0.0
        delta = pos - prev_pos
        turnover = abs(delta) * px
        if turnover > 0:
            side = "BUY" if delta > 0 else "SELL"
            cost = cost_model.single_leg_cost(turnover, "FUT", side, symbol).total
        else:
            cost = 0.0
        rows.append({"date": str(ts.date()), "position": pos, "gross_pnl": gross,
                      "cost": cost, "net_pnl": gross - cost})
        prev_pos, prev_price = pos, px

    df = pd.DataFrame(rows)
    if df.empty:
        return {"strategy": "carver_ewmac", "error": "no valid days"}

    n = len(df)
    net = df["net_pnl"].values
    total_pnl = float(net.sum())
    mean = float(net.mean())
    sd = float(net.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(mean / sd * np.sqrt(252)) if sd > 0 else 0.0
    win_rate = float((net > 0).mean())
    equity = np.cumsum(net)
    max_dd = float((np.maximum.accumulate(equity) - equity).max())

    if verbose:
        print(f"\n{'='*60}\ncarver_ewmac -- EWMAC trend + vol-targeted sizing (Systematic Trading)\n{'='*60}")
        print(f"Days: {n}  Capital: Rs{capital:,.0f}  Target risk: {target_risk_pct:.0%}/yr")
        print(f"NET P&L: Rs{total_pnl:,.0f}  Sharpe: {sharpe:.3f}  Win rate: {win_rate:.2%}")
        print(f"Max drawdown: Rs{max_dd:,.0f}")
        print(f"Total costs: Rs{df['cost'].sum():,.0f}")
        print(f"Avg |position| (notional): Rs{df['position'].abs().mean():,.0f}")

    return {"strategy": "carver_ewmac", "n_days": n, "total_pnl": round(total_pnl, 2),
            "sharpe": round(sharpe, 4), "win_rate": round(win_rate, 4),
            "max_drawdown": round(max_dd, 2), "total_cost": round(float(df["cost"].sum()), 2),
            "daily": df.to_dict("records")}


if __name__ == "__main__":
    backtest_carver_ewmac()
