"""
backtest_stochastic_crossover.py -- TradingView built-in "Stochastic"
strategy adapted to this project's single-leg intraday option-buying
framework.

Standard rule (TradingView defaults: %K period=14, %D period=3,
oversold=20, overbought=80):
  - %K crosses above %D while %K was below the oversold line (20) ->
    buy ATM CE.
  - %K crosses below %D while %K was above the overbought line (80) ->
    buy ATM PE.
One position/day (first qualifying crossover wins), same generic exit
defaults as every other seminar-style single-leg strategy in this batch
(+Rs30,000/-Rs20,000 unrealized, 3:10pm square-off).

Same per-day indicator-warmup convention as backtest_sma20_atm_option.py /
backtest_macd_crossover.py -- no cross-day carry, so the first signal each
day can't arrive before bar (k_period + d_period + 1).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_stochastic
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest


def _make_signal_fn(k_period: int, d_period: int, oversold: float, overbought: float):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        need = k_period + d_period + 1
        if len(window) < need:
            return None
        k, d = calculate_stochastic(window, k_period=k_period, d_period=d_period)
        k_now, k_prev = k.iloc[-1], k.iloc[-2]
        d_now, d_prev = d.iloc[-1], d.iloc[-2]
        if pd.isna(k_now) or pd.isna(k_prev) or pd.isna(d_now) or pd.isna(d_prev):
            return None
        crossed_up = k_prev <= d_prev and k_now > d_now
        crossed_down = k_prev >= d_prev and k_now < d_now
        if crossed_up and k_prev < oversold:
            return "CE"
        if crossed_down and k_prev > overbought:
            return "PE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_stochastic_crossover(
    k_period: int = 14,
    d_period: int = 3,
    oversold: float = 20.0,
    overbought: float = 80.0,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(k_period, d_period, oversold, overbought),
        strike_fn=_strike_fn,
        strategy_name=f"stochastic_crossover(k={k_period},d={d_period})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=k_period + d_period + 1,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_stochastic_crossover()
