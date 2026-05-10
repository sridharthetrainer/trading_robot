"""
regime.py

Market regime detector used by signal_engine.py and live_signal_engine.py.

Returns one of:
    "TREND"        — strong directional move, ADX >= 25
    "EARLY_TREND"  — developing trend, ADX 18-25
    "RANGE"        — low ADX, price coiling
    "BREAKOUT"     — sharp single-bar price move
    "VOLATILE"     — high ATR relative to price (avoid)
    "NO_TRADE"     — safety fallback (bad data, pre-market, etc.)

Fixes applied
-------------
- Original code used lowercase .get("close") / .get("adx") on a pandas Series
  that has Title-Case column names ("Close", "adx").
  .get() on a Series does NOT behave like dict.get() — unknown keys raise
  KeyError or return NaN rather than the supplied default.
  All accesses now use _safe_get() which handles both conventions.
- Added explicit NaN / zero guards before every division.
- Added _is_valid_bar() to reject rows with impossible OHLC values.
- Added optional time-of-day NO_TRADE windows (opening 5 min, pre-close 15 min).
- Added ema_spread direction check so TREND regime requires price to be on
  the correct side of the moving averages, not just a wide spread.
- Module-level logger added; silent except → logged warning.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regime thresholds — tweak here, not inside the function
# ---------------------------------------------------------------------------
ADX_STRONG_TREND     = 25.0   # ADX >= this → TREND
ADX_EARLY_TREND_LOW  = 18.0   # ADX in [18, 25) → EARLY_TREND
ADX_RANGE_MAX        = 20.0   # ADX <  this (and not early trend) → RANGE

EMA_SPREAD_TREND     = 0.002  # |ema20 - ema50| / close for TREND
EMA_SPREAD_EARLY     = 0.001  # same for EARLY_TREND

ATR_VOLATILE_PCT     = 0.015  # ATR / close above this → VOLATILE
BREAKOUT_PCT         = 0.003  # |close - prev_close| / prev_close for BREAKOUT

# Optional intraday no-trade windows (set to None to disable)
OPENING_NO_TRADE_END = dtime(9, 20)   # first 5 minutes of NSE session
PRE_CLOSE_NO_TRADE   = dtime(15, 15)  # last 15 minutes of NSE session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(row: pd.Series, *keys, default: float = 0.0) -> float:
    """
    Try each key in order; return the first non-NaN float found.
    Handles both Title-Case ("Close") and lowercase ("close") column names.
    """
    for key in keys:
        try:
            val = row[key]
            if pd.notna(val):
                return float(val)
        except (KeyError, TypeError, ValueError):
            continue
    return default


def _is_valid_bar(close: float, high: float, low: float) -> bool:
    """Reject bars with impossible values."""
    if close <= 0 or high <= 0 or low <= 0:
        return False
    if high < low:
        return False
    if high < close or low > close:
        return False
    return True


def _in_no_trade_window() -> bool:
    """
    Return True if current time falls inside a known no-trade window.
    Only relevant for live/intraday use — backtests should pass
    check_time=False.
    """
    now = datetime.now().time()

    if OPENING_NO_TRADE_END is not None and now <= OPENING_NO_TRADE_END:
        return True

    if PRE_CLOSE_NO_TRADE is not None and now >= PRE_CLOSE_NO_TRADE:
        return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_market_regime(
    df: pd.DataFrame,
    check_time: bool = False,
) -> str:
    """
    Detect the current market regime from the last two rows of df.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV dataframe with indicator columns:
        Close / close, adx, ema20, ema50, atr
        At least 2 rows required.
    check_time : bool
        When True, returns "NO_TRADE" during opening / pre-close windows.
        Enable for live trading; leave False for backtests.

    Returns
    -------
    str
        One of: "TREND", "EARLY_TREND", "RANGE", "BREAKOUT",
                "VOLATILE", "NO_TRADE"
    """
    try:
        if df is None or len(df) < 2:
            return "NO_TRADE"

        # ------------------------------------------------------------------
        # Optional live-only time gate
        # ------------------------------------------------------------------
        if check_time and _in_no_trade_window():
            return "NO_TRADE"

        latest = df.iloc[-1]
        prev   = df.iloc[-2]

        # ------------------------------------------------------------------
        # Extract values — handle both column-name conventions
        # ------------------------------------------------------------------
        close  = _safe_get(latest, "Close", "close")
        high   = _safe_get(latest, "High",  "high")
        low    = _safe_get(latest, "Low",   "low")
        adx    = _safe_get(latest, "adx",   "ADX",  "Adx")
        ema20  = _safe_get(latest, "ema20", "EMA20")
        ema50  = _safe_get(latest, "ema50", "EMA50")
        atr    = _safe_get(latest, "atr",   "ATR",  "Atr")

        prev_close = _safe_get(prev, "Close", "close")

        # ------------------------------------------------------------------
        # Basic sanity
        # ------------------------------------------------------------------
        if not _is_valid_bar(close, high, low):
            logger.debug("Invalid bar values: close=%.2f high=%.2f low=%.2f", close, high, low)
            return "NO_TRADE"

        if prev_close <= 0:
            logger.debug("Invalid prev_close: %.2f", prev_close)
            return "NO_TRADE"

        # ------------------------------------------------------------------
        # VOLATILE — avoid trading in extremely choppy conditions
        # ------------------------------------------------------------------
        if atr > 0:
            atr_pct = atr / close
            if atr_pct > ATR_VOLATILE_PCT:
                return "VOLATILE"

        # ------------------------------------------------------------------
        # EMA spread (normalised)
        # ------------------------------------------------------------------
        ema_spread = 0.0
        if ema20 > 0 and ema50 > 0:
            ema_spread = abs(ema20 - ema50) / close

        # EMA direction: is price on the correct side of both EMAs?
        bullish_stack = close > ema20 > ema50
        bearish_stack = close < ema20 < ema50
        aligned_trend = bullish_stack or bearish_stack

        # ------------------------------------------------------------------
        # STRONG TREND
        # ------------------------------------------------------------------
        if adx >= ADX_STRONG_TREND and ema_spread >= EMA_SPREAD_TREND and aligned_trend:
            return "TREND"

        # Accept TREND even without perfect EMA alignment if ADX is very strong
        if adx >= 30 and ema_spread >= EMA_SPREAD_TREND:
            return "TREND"

        # ------------------------------------------------------------------
        # EARLY TREND
        # ------------------------------------------------------------------
        if ADX_EARLY_TREND_LOW <= adx < ADX_STRONG_TREND and ema_spread >= EMA_SPREAD_EARLY:
            return "EARLY_TREND"

        # ------------------------------------------------------------------
        # BREAKOUT — sharp single-bar price move vs previous close
        # ------------------------------------------------------------------
        bar_move_pct = abs(close - prev_close) / prev_close
        if bar_move_pct >= BREAKOUT_PCT:
            return "BREAKOUT"

        # ------------------------------------------------------------------
        # RANGE — low directional conviction
        # ------------------------------------------------------------------
        if adx < ADX_RANGE_MAX:
            return "RANGE"

        # ------------------------------------------------------------------
        # Fallback: treat moderate ADX without clear trend as early trend
        # ------------------------------------------------------------------
        return "EARLY_TREND"

    except Exception:
        logger.exception("detect_market_regime failed")
        return "NO_TRADE"


# ---------------------------------------------------------------------------
# Convenience wrapper used by some callers that pass a single-row dict
# ---------------------------------------------------------------------------

def regime_from_indicators(
    close: float,
    adx: float,
    ema20: float,
    ema50: float,
    atr: float,
    prev_close: float,
) -> str:
    """
    Lightweight version that accepts pre-extracted indicator values directly.
    Useful for unit tests and callers that don't have a full DataFrame.
    """
    if close <= 0 or prev_close <= 0:
        return "NO_TRADE"

    atr_pct    = atr / close if atr > 0 else 0.0
    ema_spread = abs(ema20 - ema50) / close if (ema20 > 0 and ema50 > 0) else 0.0
    bar_move   = abs(close - prev_close) / prev_close

    aligned = (close > ema20 > ema50) or (close < ema20 < ema50)

    if atr_pct > ATR_VOLATILE_PCT:
        return "VOLATILE"

    if adx >= ADX_STRONG_TREND and ema_spread >= EMA_SPREAD_TREND and aligned:
        return "TREND"

    if adx >= 30 and ema_spread >= EMA_SPREAD_TREND:
        return "TREND"

    if ADX_EARLY_TREND_LOW <= adx < ADX_STRONG_TREND and ema_spread >= EMA_SPREAD_EARLY:
        return "EARLY_TREND"

    if bar_move >= BREAKOUT_PCT:
        return "BREAKOUT"

    if adx < ADX_RANGE_MAX:
        return "RANGE"

    return "EARLY_TREND"


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # Simulate a trending NIFTY-like DataFrame
    n = 60
    idx = pd.date_range("2026-03-17 09:15", periods=n, freq="5min")
    close_prices = 22000 + np.linspace(0, 200, n) + np.random.randn(n) * 5
    df_test = pd.DataFrame(
        {
            "Open":  close_prices - 2,
            "High":  close_prices + 5,
            "Low":   close_prices - 5,
            "Close": close_prices,
            "Volume": np.random.randint(1000, 5000, n),
            "adx":   np.linspace(15, 30, n),
            "ema20": close_prices - 10,
            "ema50": close_prices - 30,
            "atr":   np.full(n, 40.0),
        },
        index=idx,
    )

    regime = detect_market_regime(df_test, check_time=False)
    print(f"Detected regime: {regime}")

    # Test edge cases
    print(regime_from_indicators(22200, 27, 22190, 22150, 40, 22150))  # expect TREND
    print(regime_from_indicators(22200, 10, 22190, 22180, 20, 22190))  # expect RANGE
    print(regime_from_indicators(0, 20, 0, 0, 0, 0))                   # expect NO_TRADE
