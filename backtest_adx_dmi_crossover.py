"""
backtest_adx_dmi_crossover.py -- TradingView built-in "Directional Movement
Index" (ADX/DMI) strategy adapted to this project's single-leg intraday
option-buying framework.

Standard rule (TradingView/Wilder defaults: period=14, ADX trend-strength
filter=20):
  - +DI crosses above -DI AND ADX >= 20 (a trending, not choppy, market) ->
    buy ATM CE.
  - -DI crosses above +DI AND ADX >= 20 -> buy ATM PE.
One position/day (first qualifying crossover wins), same generic exit
defaults as every other seminar-style single-leg strategy in this batch
(+Rs30,000/-Rs20,000 unrealized, 3:10pm square-off).

Same per-day indicator-warmup convention as the other TradingView-strategy
backtests in this batch -- no cross-day carry. Wilder's smoothing needs
roughly 2x the base period to stabilize, so min_bars_for_signal is set to
2*period+1, not just period+1.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_adx
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest


def _make_signal_fn(period: int, adx_threshold: float):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        need = 2 * period + 1
        if len(window) < need:
            return None
        adx, plus_di, minus_di = calculate_adx(window, period=period, return_di=True)
        adx_now = adx.iloc[-1]
        plus_now, plus_prev = plus_di.iloc[-1], plus_di.iloc[-2]
        minus_now, minus_prev = minus_di.iloc[-1], minus_di.iloc[-2]
        if pd.isna(adx_now) or pd.isna(plus_now) or pd.isna(plus_prev) or pd.isna(minus_now) or pd.isna(minus_prev):
            return None
        if adx_now < adx_threshold:
            return None
        crossed_up = plus_prev <= minus_prev and plus_now > minus_now
        crossed_down = plus_prev >= minus_prev and plus_now < minus_now
        if crossed_up:
            return "CE"
        if crossed_down:
            return "PE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_adx_dmi_crossover(
    period: int = 14,
    adx_threshold: float = 20.0,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(period, adx_threshold),
        strike_fn=_strike_fn,
        strategy_name=f"adx_dmi_crossover(period={period},adx_min={adx_threshold})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=2 * period + 1,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_adx_dmi_crossover()
