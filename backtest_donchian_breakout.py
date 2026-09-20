"""
backtest_donchian_breakout.py -- TradingView built-in "Donchian Channels
Strategy" (classic Turtle-style N-period breakout) adapted to this
project's single-leg intraday option-buying framework.

Standard rule (TradingView/Turtle default: period=20):
  - Close breaks above the PRIOR period-N Donchian upper channel (highest
    high of the last N bars, computed WITHOUT the current bar) -> buy
    ATM CE.
  - Close breaks below the PRIOR period-N Donchian lower channel -> buy
    ATM PE.
One position/day (first breakout wins), same generic exit defaults as
every other seminar-style single-leg strategy in this batch
(+Rs30,000/-Rs20,000 unrealized, 3:10pm square-off).

Lookahead note: the channel is deliberately computed on window.iloc[:-1]
(every bar EXCEPT the current one) before comparing it to the current
bar's close. Including the current bar in its own rolling max/min would
make "today's high breaks today's rolling max" mathematically impossible
by construction -- the same class of self-referential lookahead bug this
session already found and fixed once this session (backtest_supertrend_
mtf.py's leaky resample, causal_htf.py's whole reason for existing).

Same per-day indicator-warmup convention as the other TradingView-strategy
backtests in this batch -- no cross-day carry.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_donchian_channel
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest


def _make_signal_fn(period: int):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        need = period + 2
        if len(window) < need:
            return None
        prior = window.iloc[:-1]
        upper, lower, _ = calculate_donchian_channel(prior, period=period)
        prior_upper, prior_lower = upper.iloc[-1], lower.iloc[-1]
        if pd.isna(prior_upper) or pd.isna(prior_lower):
            return None
        last_close = float(window["close"].iloc[-1])
        if last_close > prior_upper:
            return "CE"
        if last_close < prior_lower:
            return "PE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_donchian_breakout(
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
        strategy_name=f"donchian_breakout(period={period})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=period + 2,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_donchian_breakout()
