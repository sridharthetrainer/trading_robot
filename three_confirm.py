"""
three_confirm.py

Institutional 3-Confirmation Filter.

Every professional trading desk requires at least 3 independent
confirmations before entering a position. This eliminates the
single biggest source of losses: acting on noisy one-condition signals.

The 3 Pillars:
──────────────
PILLAR 1 — STRUCTURE
    Where is price in the market's structure?
    Is it at a key level (Order Block, VWAP, Pivot, CPR)?
    Is it making higher-highs or lower-lows?
    → Source: institutional_indicators.detect_market_structure()

PILLAR 2 — MOMENTUM  
    What is the momentum behind the move?
    Is volume confirming price action?
    Is CVD (institutional order flow) aligned?
    → Source: CVD direction, volume ratio, OBV slope

PILLAR 3 — CONTEXT
    What does the broader environment say?
    Is the day type consistent with this trade?
    Is BANKNIFTY confirming (no divergence)?
    Is VIX in the right zone?
    → Source: DayClassifier, market_context, VIX

Scoring:
    Each pillar scores 0.0 to 1.0
    Combined score = (P1 + P2 + P3) / 3
    Only signals with combined >= 0.55 pass the filter
    Minimum: each pillar must score >= 0.30 (no zero pillars)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

MIN_COMBINED_SCORE  = 0.55    # minimum combined score to pass
MIN_PILLAR_SCORE    = 0.30    # no pillar can score below this


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None and str(v) != 'nan' else default
    except Exception:
        return default


def evaluate_three_confirmations(
    df:           pd.DataFrame,
    signal:       Dict[str, Any],
    day_type:     str         = "UNKNOWN",
    vix:          float       = 15.0,
    has_divergence: bool      = False,
    df_htf:       Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Score a signal against the 3-pillar institutional confirmation model.

    Parameters
    ----------
    df          : 5-minute OHLCV DataFrame
    signal      : signal dict from signal_engine (action, strategy, score, confidence...)
    day_type    : from DayClassifier ("TREND_DAY", "RANGE_DAY", etc.)
    vix         : current India VIX value
    has_divergence : True if NIFTY and BANKNIFTY are moving in opposite directions
    df_htf      : 15-minute OHLCV (for HTF structure check)

    Returns
    -------
    {
        "pass":           bool,
        "combined_score": float,
        "pillar1_structure": float,
        "pillar2_momentum":  float,
        "pillar3_context":   float,
        "blocking_reason":   str (why it failed, if it did),
        "boost":             float (score boost for high-quality confirmations),
    }
    """
    action   = signal.get("action", "HOLD")
    strategy = str(signal.get("strategy", "")).lower()
    score    = _safe_float(signal.get("score",      0))
    conf     = _safe_float(signal.get("confidence", 0))

    if action == "HOLD":
        return _fail("hold_signal", 0, 0, 0)

    # ─────────────────────────────────────────────────────────────────────────
    # PILLAR 1 — STRUCTURE
    # ─────────────────────────────────────────────────────────────────────────
    p1 = 0.0
    try:
        from institutional_indicators import (
            detect_market_structure, get_nearest_order_block, get_vpoc_bias,
        )
        ms      = detect_market_structure(df, lookback=40)
        ms_score = _safe_float(ms.get("structure_score", 0))

        # +0.4 if market structure aligns with trade direction
        if action == "BUY"  and ms_score >= 1.0: p1 += 0.40
        elif action == "SELL" and ms_score <= -1.0: p1 += 0.40
        elif action == "BUY"  and ms_score >= 0:  p1 += 0.20
        elif action == "SELL" and ms_score <= 0:  p1 += 0.20

        # +0.20 if price at an institutional Order Block
        ob = get_nearest_order_block(df, action, tolerance=0.004)
        if ob:
            p1 += 0.20

        # +0.15 if VPOC confirms direction
        vp = get_vpoc_bias(df)
        pos = vp.get("position", "")
        if action == "BUY"  and "ABOVE_VPOC" in pos: p1 += 0.15
        if action == "SELL" and "BELOW_VPOC" in pos: p1 += 0.15

        # +0.15 if recent BOS confirmed
        if action == "BUY"  and ms.get("last_bos") == "BULLISH": p1 += 0.15
        if action == "SELL" and ms.get("last_bos") == "BEARISH": p1 += 0.15

        # +0.10 from base signal confidence
        p1 += conf * 0.10

    except Exception as exc:
        logger.debug("Pillar 1 error: %s", exc)
        p1 = conf * 0.40    # fallback: use confidence

    p1 = min(1.0, max(0.0, p1))

    # ─────────────────────────────────────────────────────────────────────────
    # PILLAR 2 — MOMENTUM (order flow and volume)
    # ─────────────────────────────────────────────────────────────────────────
    p2 = 0.0
    try:
        from institutional_indicators import get_cvd_signal
        from indicators import calculate_volume_ratio, calculate_obv

        cvd_result = get_cvd_signal(df, lookback=5)
        cvd_dir    = cvd_result.get("direction", "NEUTRAL")
        cvd_div    = cvd_result.get("divergence", False)
        absorption = cvd_result.get("absorption", False)

        # +0.35 if CVD direction matches trade
        if action == "BUY"  and cvd_dir == "BULLISH": p2 += 0.35
        elif action == "SELL" and cvd_dir == "BEARISH": p2 += 0.35
        elif cvd_dir == "NEUTRAL": p2 += 0.10

        # -0.20 if CVD diverging from price (distribution / absorption)
        if cvd_div: p2 -= 0.20

        # +0.25 if absorption detected (high conviction bar)
        if absorption: p2 += 0.25

        # +0.20 volume ratio
        vr = _safe_float(calculate_volume_ratio(df, 20).iloc[-1], 1.0)
        if vr >= 1.8: p2 += 0.20
        elif vr >= 1.2: p2 += 0.10
        elif vr < 0.7: p2 -= 0.15

        # +0.15 OBV slope confirms
        try:
            obv   = calculate_obv(df)
            slope = float(obv.diff(5).iloc[-1]) if len(obv) >= 6 else 0.0
            if action == "BUY"  and slope > 0: p2 += 0.15
            if action == "SELL" and slope < 0: p2 += 0.15
        except Exception:
            pass

    except Exception as exc:
        logger.debug("Pillar 2 error: %s", exc)
        p2 = 0.35    # neutral fallback

    p2 = min(1.0, max(0.0, p2))

    # ─────────────────────────────────────────────────────────────────────────
    # PILLAR 3 — CONTEXT (day type, VIX, cross-index)
    # ─────────────────────────────────────────────────────────────────────────
    p3 = 0.50   # neutral base

    try:
        # Day type alignment
        trend_strategies = {"trend","breakout","hour_orb","orb","market_structure",
                             "supertrend_mtf","liquidity_sweep","order_block"}
        range_strategies = {"mean_reversion","vwap_reversion","vpoc_magnet",
                             "iron_condor","bull_put_spread"}

        if day_type == "TREND_DAY":
            if strategy in trend_strategies: p3 += 0.25
            if strategy in range_strategies: p3 -= 0.30
        elif day_type == "RANGE_DAY":
            if strategy in range_strategies: p3 += 0.25
            if strategy in trend_strategies: p3 -= 0.30
        elif day_type == "VOLATILE_DAY":
            p3 = 0.0    # nothing passes on volatile day
        elif day_type == "REVERSAL_DAY":
            if strategy in range_strategies: p3 += 0.15

        # VIX zone
        if vix < 13:   p3 += 0.10   # complacent = steady trends
        elif vix < 18: p3 += 0.05   # normal
        elif vix < 22: p3 += 0.00   # elevated — neutral
        elif vix < 26: p3 -= 0.10   # high — reduce
        else:          p3 -= 0.30   # extreme — strong reduction

        # Cross-index divergence penalty
        if has_divergence:
            p3 -= 0.20   # NIFTY and BANKNIFTY disagree — weaker signal

        # HTF alignment bonus
        if df_htf is not None and len(df_htf) >= 6:
            try:
                htf_col = "Close" if "Close" in df_htf.columns else "close"
                htf_c   = pd.to_numeric(df_htf[htf_col], errors="coerce")
                htf_ret = float(htf_c.iloc[-1] - htf_c.iloc[-4]) / float(htf_c.iloc[-4])
                if action == "BUY"  and htf_ret > 0.001: p3 += 0.10
                if action == "SELL" and htf_ret < -0.001: p3 += 0.10
            except Exception:
                pass

    except Exception as exc:
        logger.debug("Pillar 3 error: %s", exc)

    p3 = min(1.0, max(0.0, p3))

    # ─────────────────────────────────────────────────────────────────────────
    # COMBINE
    # ─────────────────────────────────────────────────────────────────────────
    combined = round((p1 + p2 + p3) / 3.0, 4)

    # Each pillar must meet minimum
    if p1 < MIN_PILLAR_SCORE:
        return _fail(f"pillar1_structure_low_{p1:.2f}", p1, p2, p3)
    if p2 < MIN_PILLAR_SCORE:
        return _fail(f"pillar2_momentum_low_{p2:.2f}", p1, p2, p3)
    if p3 < MIN_PILLAR_SCORE:
        return _fail(f"pillar3_context_low_{p3:.2f}", p1, p2, p3)
    if combined < MIN_COMBINED_SCORE:
        return _fail(f"combined_too_low_{combined:.2f}", p1, p2, p3)

    # Score boost for high-quality confluence (all 3 pillars strong)
    boost = 0.0
    if combined >= 0.80: boost = 2.0
    elif combined >= 0.70: boost = 1.0
    elif combined >= 0.65: boost = 0.5

    return {
        "pass":              True,
        "combined_score":    combined,
        "pillar1_structure": round(p1, 3),
        "pillar2_momentum":  round(p2, 3),
        "pillar3_context":   round(p3, 3),
        "blocking_reason":   "",
        "boost":             boost,
    }


def _fail(reason: str, p1: float, p2: float, p3: float) -> Dict[str, Any]:
    return {
        "pass":              False,
        "combined_score":    round((p1 + p2 + p3) / 3.0, 4),
        "pillar1_structure": round(p1, 3),
        "pillar2_momentum":  round(p2, 3),
        "pillar3_context":   round(p3, 3),
        "blocking_reason":   reason,
        "boost":             0.0,
    }
