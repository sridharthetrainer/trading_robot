"""
backtest_bollinger_otm_reversal.py — "Bollinger Bend Reversal Based OTM Buy"
(seminar strategy, 2026-08-11).

Rules as given:
  - NIFTY 5-min close outside the Bollinger Band -> buy the OTM option betting
    on reversal: close ABOVE upper band -> buy 10-strike-OTM PE (fade the
    spike up); close BELOW lower band -> buy 10-strike-OTM CE (fade the drop).
  - One position at a time, no re-entry same day after square-off.
  - Exit at +Rs30,000 / -Rs20,000 unrealized P&L, or 3:10 PM square-off.

"Configured quantity" was left unspecified in the seminar description --
defaults to 1 lot here (see single_leg_intraday_option_backtest.DEFAULT_LOT_
SIZE); rescale `total_pnl` linearly for a different lot count. Bollinger
period/deviation were also "configurable" with no numbers given -- defaults
to the industry-standard 20-period / 2 std-dev; swept in the param grid.

Option premium path is Black-Scholes, anchored to the previous trading day's
real NIFTY EOD settlement (see option_intraday_pricer.py) -- NOT real
intraday tick data, which doesn't exist for this history. Every skipped
(unpriceable) candidate day is reported, not silently dropped.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from indicators import calculate_bollinger_bands
from option_intraday_pricer import otm_strike
from single_leg_intraday_option_backtest import run_single_leg_backtest

N_OTM_STRIKES = 10


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
            return "PE"   # fade the spike up
        if last_close < last_lower:
            return "CE"   # fade the drop
        return None
    return signal_fn


def _strike_fn(spot: float, direction: str) -> float:
    return otm_strike(spot, direction, N_OTM_STRIKES)


def backtest_bollinger_otm_reversal(
    period: int = 20,
    std_mult: float = 2.0,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    lots: int = 1,
    verbose: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    return run_single_leg_backtest(
        signal_fn=_make_signal_fn(period, std_mult),
        strike_fn=_strike_fn,
        strategy_name=f"bollinger_otm_reversal(period={period},std={std_mult})",
        profit_target=profit_target,
        loss_limit=loss_limit,
        lots=lots,
        min_bars_for_signal=period + 1,
        verbose=verbose,
        **kwargs,
    )


if __name__ == "__main__":
    backtest_bollinger_otm_reversal()
