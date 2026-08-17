"""
backtest_adx_long_straddle.py — "High ADX Option Buying" (seminar strategy,
2026-08-11).

Rules as given:
  - ADX(14) on 1-MINUTE NIFTY candles above 50, before 10:00 AM -> buy ATM CE
    + buy ATM PE simultaneously (long straddle, 10 lots each leg).
  - If no entry by 10:00 AM, no trade that day.
  - Exit at combined P&L +Rs30,000 / -Rs20,000, else 3:25 PM square-off.
  - No re-entry same session.

ADX>50 is an unusually high bar (ADX readings above 50 are rare even on
daily charts; on 1-min bars they're noisier and can spike more often, but
this is still meant to catch genuine strong-trend bursts, not a routine
daily event like the Bollinger/SMA signals turned out to be) -- worth
checking how often this actually fires before reading the P&L number.

Uses multi_leg_intraday_option_backtest.py (both legs Black-Scholes-priced,
anchored to the previous day's real EOD settle, real transaction costs via
nse_cost_model.py applied per leg).
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Any, Dict

import pandas as pd

from indicators import calculate_adx
from option_intraday_pricer import nearest_strike
from multi_leg_intraday_option_backtest import LegSpec, run_multi_leg_backtest

ADX_PERIOD = 14
ADX_THRESHOLD = 50.0
ENTRY_DEADLINE = dtime(10, 0)
SQUARE_OFF = dtime(15, 25)


def _make_entry_fn(period: int, threshold: float):
    def entry_fn(window: pd.DataFrame) -> bool:
        if len(window) < period * 2:
            return False
        adx = calculate_adx(window, period=period)
        last = adx.iloc[-1]
        if pd.isna(last):
            return False
        return float(last) > threshold
    return entry_fn


def backtest_adx_long_straddle(
    period: int = ADX_PERIOD,
    threshold: float = ADX_THRESHOLD,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 10,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    legs = [
        LegSpec("call_leg", "BUY", "CE", lambda spot: nearest_strike(spot)),
        LegSpec("put_leg", "BUY", "PE", lambda spot: nearest_strike(spot)),
    ]
    return run_multi_leg_backtest(
        entry_fn=_make_entry_fn(period, threshold),
        leg_specs=legs,
        strategy_name=f"adx_long_straddle(period={period},thr={threshold})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        entry_deadline=ENTRY_DEADLINE,
        square_off_time=SQUARE_OFF,
        lots=lots,
        min_bars_for_signal=period * 2,
        candle_interval="1m",
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_adx_long_straddle()
