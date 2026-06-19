"""
mtf.py

Higher time-frame bias helper.

Fixes applied
-------------
1. KeyError on normalized DataFrames
   Original accessed latest["close"], latest["ema20"], latest["ema50"]
   directly — no fallback for Title-Case column names.

   DataFetcher._clean() produces "Close" (uppercase).
   indicators.add_all_indicators() writes "ema_fast" / "ema_slow",
   NOT "ema20" / "ema50".

   In practice this crashed on EVERY live signal evaluation because the
   HTF DataFrame from DataFetcher always uses "Close", not "close".

   Fix: try multiple column name variants in priority order.
   Fallback to "SIDEWAYS" on any error so the signal chain continues.

2. No error handling at all
   A single bad row (NaN, zero close) propagated unhandled and crashed
   the caller. Added try/except returning "SIDEWAYS" as the safe fallback.
"""

from __future__ import annotations

import pandas as pd


def _safe_get(row: pd.Series, *keys, default: float = 0.0) -> float:
    """Try each key in order; return first non-NaN float found."""
    for key in keys:
        try:
            val = row[key]
            if pd.notna(val):
                return float(val)
        except (KeyError, TypeError, ValueError):
            continue
    return default


def get_htf_bias(df_htf) -> str:
    """
    Return higher time-frame directional bias.

    Returns "BULLISH", "BEARISH", or "SIDEWAYS".

    Column name resolution (tried in order):
    - close / Close / CLOSE
    - ema20 / ema_fast / ema_slow (for the faster EMA)
    - ema50 / ema_slow / ema_trend (for the slower EMA)

    This covers both raw DataFetcher output (lowercase + add_all_indicators
    naming) and pre-normalized DataFrames (Title-Case).
    """
    try:
        if df_htf is None or len(df_htf) < 2:
            return "SIDEWAYS"

        latest = df_htf.iloc[-1]

        close = _safe_get(latest, "Close", "close", "CLOSE")
        ema20 = _safe_get(latest, "ema20", "ema_fast", "EMA20")
        ema50 = _safe_get(latest, "ema50", "ema_slow", "EMA50", "ema_trend")

        if close <= 0 or ema20 <= 0 or ema50 <= 0:
            return "SIDEWAYS"

        if close > ema20 > ema50:
            return "BULLISH"
        if close < ema20 < ema50:
            return "BEARISH"
        return "SIDEWAYS"

    except Exception:
        return "SIDEWAYS"
