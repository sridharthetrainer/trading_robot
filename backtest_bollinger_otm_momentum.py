"""
backtest_bollinger_otm_momentum.py — "Bollinger Band High Volatility OTM
Option Buy" (seminar strategy, 2026-08-11).

This is the MOMENTUM/breakout-continuation mirror of backtest_bollinger_otm_
reversal.py, NOT a duplicate -- same trigger (5-min close outside the BB),
opposite direction and different strike distance:

  - Reversal (strategy #1): close ABOVE upper band -> buy PE (fade the spike).
  - Momentum (this one):    close ABOVE upper band -> buy CE (ride the spike).
                            close BELOW lower band -> buy PE (ride the drop).
  - Strike: 1-strike OTM (vs 10-strike OTM for the reversal version).
  - Quantity: "10 qty" stated explicitly in the seminar description -> 10
    lots default here (contrast with the reversal version, where quantity
    was left unspecified).

Same exit rules (+Rs30,000 / -Rs20,000 unrealized, else 3:10 PM square-off,
one trade/day, no re-entry) and same Black-Scholes-anchored-to-real-EOD-
settle pricing caveats as the reversal version.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_bollinger_bands
from option_intraday_pricer import otm_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest

N_OTM_STRIKES = 1


def _make_signal_fn(period: int, std_mult: float):
    def signal_fn(window: pd.DataFrame) -> Optional[str]:
        if len(window) < period + 1:
            return None
        lower, _, upper = calculate_bollinger_bands(window["close"], period, std_mult)
        last_close = float(window["close"].iloc[-1])
        last_upper = upper.iloc[-1]
        last_lower = lower.iloc[-1]
        if pd.isna(last_upper) or pd.isna(last_lower):
            return None
        if last_close > last_upper:
            return "CE"   # ride the spike up
        if last_close < last_lower:
            return "PE"   # ride the drop
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return otm_strike(spot, direction, N_OTM_STRIKES)


def backtest_bollinger_otm_momentum(
    period: int = 20,
    std_mult: float = 2.0,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 10,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(period, std_mult),
        strike_fn=_strike_fn,
        strategy_name=f"bollinger_otm_momentum(period={period},std={std_mult})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=period + 1,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_bollinger_otm_momentum()
