"""
backtest_di_momentum_call.py — "DI Bullish Momentum Trend Entry" (seminar
strategy, 2026-08-11).

Rules as given:
  - NIFTY 5-min: DI > 25 AND Momentum > 0 -> buy ATM CE (bull-only, no PE
    side is described anywhere in this strategy).
  - One trade/day, exit at +Rs30,000 / -Rs20,000 unrealized, else 3:10 PM.

Two interpretation calls, flagged rather than silently assumed:
  - "DI" is read as +DI (the Plus Directional Indicator from Wilder's DMI
    system), NOT ADX. ADX is non-directional (trend STRENGTH, not
    direction), which doesn't fit a strategy titled "Bullish" that only
    ever buys calls; +DI>25 is a directional bullish-strength reading,
    which does. calculate_adx(..., return_di=True) exposes it directly.
  - "Momentum" is read as the textbook MOM indicator: close - close[n bars
    ago] (raw price difference, not %). No calculate_momentum() existed in
    indicators.py to reuse, so it's computed inline here -- one line,
    standard formula, not worth adding a new indicators.py function for.

Both periods were "configurable" with no numbers given -> DI period=14
(Wilder's standard), Momentum period=10 (the common MOM/ROC default),
swept via the param grid rather than hardcoded as gospel.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_adx
from option_intraday_pricer import nearest_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest

DI_PERIOD = 14
MOM_PERIOD = 10
DI_THRESHOLD = 25.0


def _make_signal_fn(di_period: int, mom_period: int, di_threshold: float):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        if len(window) < max(di_period * 2, mom_period + 1):
            return None
        _, plus_di, _ = calculate_adx(window, period=di_period, return_di=True)
        last_di = plus_di.iloc[-1]
        momentum = window["close"].diff(mom_period)
        last_mom = momentum.iloc[-1]
        if pd.isna(last_di) or pd.isna(last_mom):
            return None
        if float(last_di) > di_threshold and float(last_mom) > 0:
            return "CE"
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return nearest_strike(spot)


def backtest_di_momentum_call(
    di_period: int = DI_PERIOD,
    mom_period: int = MOM_PERIOD,
    di_threshold: float = DI_THRESHOLD,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 10,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(di_period, mom_period, di_threshold),
        strike_fn=_strike_fn,
        strategy_name=f"di_momentum_call(di={di_period},mom={mom_period},thr={di_threshold})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=max(di_period * 2, mom_period + 1),
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_di_momentum_call()
