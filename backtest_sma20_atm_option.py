"""
backtest_sma20_atm_option.py — "SMA 20 Crossover ATM Option Buying" (seminar
strategy, 2026-08-11).

Rules as given:
  - NIFTY 5-min candle closes above SMA20 -> buy ATM CE; closes below SMA20
    -> buy ATM PE. One position/day, no re-entry after square-off.
  - Exit at +Rs30,000 / -Rs20,000 unrealized P&L, or 3:10 PM square-off.

Literal-reading caveat: "close above/below SMA20" is evaluated fresh every
bar, not as a crossover event. Price is almost never exactly AT its 20-bar
average, so this fires on close to the FIRST evaluable bar of the day
(9:15 + 20 bars) every single day, in whichever direction price happens to
sit relative to its SMA at that moment -- there's no real "signal" filtering
WHEN to trade, only which side. Flagging this rather than silently
substituting an actual crossover-detection rule the seminar didn't specify.

Same lot-size/quantity and Black-Scholes-anchored-to-real-EOD-settle caveats
as backtest_bollinger_otm_reversal.py apply here.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_sma
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest


def _make_signal_fn(period: int):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        if len(window) < period + 1:
            return None
        sma = calculate_sma(window["close"], period)
        last_close = float(window["close"].iloc[-1])
        last_sma = sma.iloc[-1]
        if pd.isna(last_sma):
            return None
        if last_close > last_sma:
            return "CE"
        if last_close < last_sma:
            return "PE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_sma20_atm_option(
    period: int = 20,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(period),
        strike_fn=_strike_fn,
        strategy_name=f"sma20_atm_option(period={period})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=period + 1,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_sma20_atm_option()
