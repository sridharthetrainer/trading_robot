"""
backtest_aroon_crossover.py -- TradingView built-in "Aroon" strategy adapted
to this project's single-leg intraday option-buying framework.

Standard rule (TradingView default: period=25):
  - Aroon Up crosses above Aroon Down -> buy ATM CE.
  - Aroon Down crosses above Aroon Up -> buy ATM PE.
One position/day (first crossover wins), same generic exit defaults as
every other seminar-style single-leg strategy in this batch
(+Rs30,000/-Rs20,000 unrealized, 3:10pm square-off).

Same per-day indicator-warmup convention as the other TradingView-strategy
backtests in this batch -- no cross-day carry. Aroon(25) needs 26 bars of
warmup, the longest of this batch's five indicators, so on a ~75-bar
intraday session this fires latest in the day of the five.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_aroon
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest


def _make_signal_fn(period: int):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        need = period + 2
        if len(window) < need:
            return None
        up, down, _ = calculate_aroon(window, period=period)
        up_now, up_prev = up.iloc[-1], up.iloc[-2]
        down_now, down_prev = down.iloc[-1], down.iloc[-2]
        if pd.isna(up_now) or pd.isna(up_prev) or pd.isna(down_now) or pd.isna(down_prev):
            return None
        crossed_up = up_prev <= down_prev and up_now > down_now
        crossed_down = up_prev >= down_prev and up_now < down_now
        if crossed_up:
            return "CE"
        if crossed_down:
            return "PE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_aroon_crossover(
    period: int = 25,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(period),
        strike_fn=_strike_fn,
        strategy_name=f"aroon_crossover(period={period})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=period + 2,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_aroon_crossover()
