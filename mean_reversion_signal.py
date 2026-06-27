"""
mean_reversion_signal.py

Lightweight mean-reversion signal helper.

Fixes applied
-------------
Column name mismatch with DataFetcher / add_all_indicators output.

Original accessed:
    latest["close"]    — DataFetcher._clean() produces "Close" (Title-Case)
    latest["bb_lower"] — add_all_indicators() writes "bb_lower" (correct)
    latest["bb_upper"] — add_all_indicators() writes "bb_upper" (correct)
    latest["rsi"]      — add_all_indicators() writes "rsi"      (correct)

"close" → KeyError on any DataFrame that went through DataFetcher._clean()
or DataFetcher._fetch_from_yfinance() since both normalize to Title-Case.

Fix: try both capitalisations for each field.  The helper is intentionally
minimal — it's a thin gate used where a full signal_engine pass is not
warranted (e.g. quick checks inside backtest loops or diagnostic scripts).
"""

from __future__ import annotations

from typing import Optional


def _get(row, *keys, default: float = 0.0) -> float:
    """Try column names in order, return first non-None value."""
    for key in keys:
        try:
            val = row.get(key) if hasattr(row, "get") else row[key]
            if val is not None:
                return float(val)
        except (KeyError, TypeError, ValueError):
            continue
    return default


def generate_mr_signal(df) -> Optional[dict]:
    """
    Quick mean-reversion signal from a pre-indicator DataFrame.

    Expects columns produced by indicators.add_all_indicators():
        rsi, bb_lower, bb_upper

    Plus the price close in either casing:
        Close  (DataFetcher / yfinance)
        close  (raw broker data)

    Returns {"action": "BUY" | "SELL", "type": "MR"} or None.
    """
    if df is None or len(df) < 2:
        return None

    try:
        latest = df.iloc[-1]

        rsi      = _get(latest, "rsi",      "RSI")
        close    = _get(latest, "Close",    "close",    "CLOSE")
        lower_bb = _get(latest, "bb_lower", "lower_bb", "BB_LOWER")
        upper_bb = _get(latest, "bb_upper", "upper_bb", "BB_UPPER")

        if close <= 0 or lower_bb <= 0 or upper_bb <= 0:
            return None

        if rsi < 30 and close < lower_bb:
            return {"action": "BUY",  "type": "MR"}

        if rsi > 70 and close > upper_bb:
            return {"action": "SELL", "type": "MR"}

    except Exception:
        return None

    return None
