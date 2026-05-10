"""
signal_score.py

Composite signal scoring used by signal_engine.py and live_signal_engine.py.

Fixes applied
-------------
Column name mismatch with add_all_indicators() output.

Original accessed:
    latest.get("ema20")   → always None (column is "ema_fast")
    latest.get("ema200")  → always None (column is "ema_trend")

Effect of ema20=0 (the _safe default):
    "if direction == 'BUY' and close > ema20" → close > 0 → always True
        → every BUY candidate got +1 for free regardless of EMA structure
    "if ema20 and ema200" → "if 0 and 0" → False
        → the ema alignment check (ema_fast > ema_trend) was always skipped

Fix: try ema indicator column names in priority order matching what each
possible caller produces:
    ema20  → try: ema20, ema_fast
    ema200 → try: ema200, ema_trend, ema_slow

Also add "Close" Title-Case fallback alongside "close" for the HTF
DataFrame which always comes from DataFetcher._clean() (Title-Case).
"""

from __future__ import annotations

import numpy as np

from mtf import get_htf_bias


def _safe(x, default: float = 0.0) -> float:
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default


def _get(row, *keys, default: float = 0.0) -> float:
    """Try column names in order, return first non-None non-NaN value."""
    for key in keys:
        try:
            val = row.get(key) if hasattr(row, "get") else row[key]
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
        except (KeyError, TypeError, ValueError):
            continue
    return default


def calculate_signal_score(df, df_htf, option_data=None):

    if len(df) < 5:
        return 0, None

    score   = 0.0
    reasons = []

    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    # Prefer Title-Case (DataFetcher output) then lowercase (raw broker data)
    close  = _get(latest, "Close",  "close")
    open_  = _get(latest, "Open",   "open")
    high   = _get(latest, "High",   "high")
    low    = _get(latest, "Low",    "low")

    # ema20 → add_all_indicators writes "ema_fast" (default period=9)
    # ema200 → add_all_indicators writes "ema_trend" (default period=200)
    ema20  = _get(latest, "ema20",  "ema_fast")
    ema200 = _get(latest, "ema200", "ema_trend", "ema_slow")
    vwap   = _get(latest, "vwap")
    adx    = _get(latest, "adx",  default=15.0)
    rsi    = _get(latest, "rsi",  default=50.0)

    # =========================================================
    # 1. HTF BIAS
    # =========================================================
    htf_bias = get_htf_bias(df_htf)

    if htf_bias == "BULLISH":
        direction = "BUY"
        score += 2
        reasons.append("htf_bullish")
    elif htf_bias == "BEARISH":
        direction = "SELL"
        score += 2
        reasons.append("htf_bearish")
    else:
        direction = "BUY" if close > open_ else "SELL"
        score += 0.5
        reasons.append("htf_sideways_momentum")

    # =========================================================
    # 2. EMA STRUCTURE
    # Only score if we actually have EMA values (non-zero)
    # =========================================================
    if ema20 > 0:
        if direction == "BUY"  and close > ema20:
            score += 1
            reasons.append("close_above_ema_fast")
        if direction == "SELL" and close < ema20:
            score += 1
            reasons.append("close_below_ema_fast")

    if ema20 > 0 and ema200 > 0:
        if direction == "BUY"  and ema20 > ema200:
            score += 1
            reasons.append("ema_fast_above_trend")
        if direction == "SELL" and ema20 < ema200:
            score += 1
            reasons.append("ema_fast_below_trend")

    # =========================================================
    # 3. VWAP CONFIRMATION
    # =========================================================
    if vwap > 0:
        if direction == "BUY"  and close > vwap:
            score += 0.5
            reasons.append("close_above_vwap")
        if direction == "SELL" and close < vwap:
            score += 0.5
            reasons.append("close_below_vwap")

    # =========================================================
    # 4. ADX TREND STRENGTH
    # =========================================================
    if adx > 20:
        score += 0.5
        reasons.append("adx_trending")

    # =========================================================
    # 5. PULLBACK (near EMA)
    # =========================================================
    if ema20 > 0:
        if direction == "BUY"  and close <= ema20 * 1.005:
            score += 1
            reasons.append("pullback_buy")
        if direction == "SELL" and close >= ema20 * 0.995:
            score += 1
            reasons.append("pullback_sell")

    # =========================================================
    # 6. BREAKOUT ABOVE PREV BAR
    # =========================================================
    prev_high = _get(prev, "High", "high")
    prev_low  = _get(prev, "Low",  "low")

    if direction == "BUY"  and close > prev_high > 0:
        score += 1
        reasons.append("breakout_up")
    if direction == "SELL" and close < prev_low  > 0:
        score += 1
        reasons.append("breakout_down")

    # =========================================================
    # 7. WICK FILTER (adverse wick penalty)
    # =========================================================
    candle_body = abs(close - open_)
    upper_wick  = high - max(open_, close)
    lower_wick  = min(open_, close) - low

    if direction == "BUY"  and candle_body > 0 and upper_wick > candle_body * 1.5:
        score -= 0.5
        reasons.append("upper_wick_penalty")
    if direction == "SELL" and candle_body > 0 and lower_wick > candle_body * 1.5:
        score -= 0.5
        reasons.append("lower_wick_penalty")

    # =========================================================
    # 8. RSI MOMENTUM
    # =========================================================
    if direction == "BUY"  and rsi > 50:
        score += 0.5
        reasons.append("rsi_bullish")
    if direction == "SELL" and rsi < 50:
        score += 0.5
        reasons.append("rsi_bearish")

    # =========================================================
    # 9. OPTION DATA (optional boost)
    # =========================================================
    if option_data:
        oi_bias = option_data.get("oi_bias")
        if direction == "BUY"  and oi_bias == "PUT_WRITING":
            score += 1
            reasons.append("put_writing_support")
        if direction == "SELL" and oi_bias == "CALL_WRITING":
            score += 1
            reasons.append("call_writing_support")

    # =========================================================
    # 10. OVEREXTENSION PENALTY
    # =========================================================
    if vwap > 0:
        distance = abs(close - vwap) / max(close, 1e-9)
        if distance > 0.01:
            score -= 1
            reasons.append("overextended_from_vwap")

    # =========================================================
    # FINAL DECISION
    # =========================================================
    if score >= 4:
        return score, direction

    if score >= 2.5:
        return score, direction

    return score, None
