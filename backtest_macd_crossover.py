"""
backtest_macd_crossover.py -- TradingView built-in "MACD Strategy" adapted to
this project's single-leg intraday option-buying framework.

Standard rule (TradingView default params: fast=12, slow=26, signal=9):
  - MACD histogram crosses from <=0 to >0 (bullish crossover) -> buy ATM CE.
  - MACD histogram crosses from >=0 to <0 (bearish crossover) -> buy ATM PE.
One position/day (first crossover wins), same generic exit defaults as every
other seminar-style single-leg strategy in this batch (+Rs30,000/-Rs20,000
unrealized, 3:10pm square-off) -- these are NOT a TradingView-native rule,
just this project's established default for adapting an underlying-chart
signal into an option-buying context.

Indicator warmup caveat: like every strategy built on
single_leg_intraday_option_backtest.py, each day's window resets at 9:15am
with no cross-day indicator carry (same convention as backtest_sma20_atm_
option.py). MACD(12,26,9) needs 36 5-min bars of warmup, so on a ~75-bar
intraday session the first possible signal each day is mid-morning at the
earliest -- a real constraint on trade frequency, not a bug.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_macd
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest


def _make_signal_fn(fast: int, slow: int, signal: int):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        need = slow + signal + 1
        if len(window) < need:
            return None
        _, _, hist = calculate_macd(window["close"], fast=fast, slow=slow, signal=signal)
        h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
        if pd.isna(h_now) or pd.isna(h_prev):
            return None
        if h_prev <= 0 and h_now > 0:
            return "CE"
        if h_prev >= 0 and h_now < 0:
            return "PE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_macd_crossover(
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(fast, slow, signal),
        strike_fn=_strike_fn,
        strategy_name=f"macd_crossover(fast={fast},slow={slow},signal={signal})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=slow + signal + 1,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_macd_crossover()
