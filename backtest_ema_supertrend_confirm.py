"""
backtest_ema_supertrend_confirm.py -- a documented Zerodha Streak public
strategy example, adapted to this project's single-leg intraday
option-buying framework.

Source (found via web search of Zerodha Streak's publicly documented
strategy-builder examples, 2026-09-20): "Alert when 5 period EMA crosses
20 period EMA and Supertrend is on uptrend and vice versa for sell
order." This is a combined-CONFIRMATION rule, not a single indicator --
the entry only fires when TWO independent signals agree, which is a
genuinely different hypothesis from testing either indicator alone (both
already tested and rejected earlier today: ma_cross-style EMA crossover
is already in this system's 79-strategy registry, and Supertrend alone
was tested as supertrend_flip in the Chartink batch). The premise behind
combining them is that requiring agreement filters out false crossover
signals -- worth testing on its own terms rather than assumed to fail
just because its components did.

Rule:
  - EMA(5) crosses above EMA(20) AND Supertrend(10,3) direction is
    already +1 (uptrend) -> buy ATM CE.
  - EMA(5) crosses below EMA(20) AND Supertrend(10,3) direction is
    already -1 (downtrend) -> buy ATM PE.
One position/day (first qualifying confirmation wins), same generic exit
defaults as every other seminar-style single-leg strategy in this batch
(+Rs30,000/-Rs20,000 unrealized, 3:10pm square-off).

DRIFT-CONFOUND DISCIPLINE: same as every strategy tested today after the
TradingView-built-ins false positives -- report is only trusted after
comparing against the already-established naive always-CE/always-PE
baselines (naive PE: net +Rs234,294/Sharpe 2.428; naive CE: net
+Rs28,892/Sharpe 0.316, both measured 2026-09-20 over this identical
candle_cache.db window), not read as a raw number in isolation.

Same per-day indicator-warmup convention as every other single-leg
backtest in this batch -- no cross-day carry.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_ema, calculate_supertrend
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest

FAST_EMA = 5
SLOW_EMA = 20
ST_PERIOD = 10
ST_MULT = 3.0
MIN_BARS = 35


def signal_fn(window: pd.DataFrame) -> Optional[str]:
    if len(window) < MIN_BARS:
        return None
    ema_fast = calculate_ema(window["close"], FAST_EMA)
    ema_slow = calculate_ema(window["close"], SLOW_EMA)
    f_now, f_prev = ema_fast.iloc[-1], ema_fast.iloc[-2]
    s_now, s_prev = ema_slow.iloc[-1], ema_slow.iloc[-2]
    if pd.isna(f_now) or pd.isna(f_prev) or pd.isna(s_now) or pd.isna(s_prev):
        return None
    _, direction = calculate_supertrend(window, period=ST_PERIOD, multiplier=ST_MULT)
    st_now = direction.iloc[-1]
    if pd.isna(st_now):
        return None

    crossed_up = f_prev <= s_prev and f_now > s_now
    crossed_down = f_prev >= s_prev and f_now < s_now

    if crossed_up and st_now == 1:
        return "CE"
    if crossed_down and st_now == -1:
        return "PE"
    return None


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_ema_supertrend_confirm(
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=signal_fn,
        strike_fn=_strike_fn,
        strategy_name=f"ema_supertrend_confirm(ema={FAST_EMA}/{SLOW_EMA},st={ST_PERIOD})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=MIN_BARS,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_ema_supertrend_confirm()
