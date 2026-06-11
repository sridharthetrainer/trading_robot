"""
vwap_reversion_strategy.py

VWAP Deviation Reversion strategy.

Large funds must execute near VWAP to minimise market impact. This
creates a predictable gravity effect — price tends to revert to VWAP
after significant deviations. Best in the 11:00-13:00 consolidation
window but valid all session.

Logic
-----
BUY  when: price > 0.3% below VWAP AND RSI < 38 AND volume_ratio >= 0.8
SELL when: price > 0.3% above VWAP AND RSI > 62 AND volume_ratio >= 0.8
Target: VWAP itself
Stop: 2× ATR from entry
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

from indicators import (
    calculate_atr,
    calculate_choppiness_index,
    calculate_rsi,
    calculate_volume_ratio,
    calculate_vwap_bands,
)

logger = logging.getLogger(__name__)

VWAP_DEV_MIN   = 0.003   # minimum 0.3% deviation from VWAP to trigger
RSI_OVERSOLD   = 38
RSI_OVERBOUGHT = 62
VOL_MIN        = 0.80    # at least 80% of avg volume (avoid dead market)
CONF_BASE      = 0.52
CONF_MAX       = 0.88


def _safe(s: pd.Series, default: float = 0.0) -> float:
    try:
        v = s.iloc[-1]
        return float(v) if pd.notna(v) else default
    except Exception:
        return default


def vwap_reversion_signal(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute VWAP reversion signal.

    Returns dict with action, strategy, confidence, reason, indicators.
    """
    _hold = {"action": "HOLD", "strategy": "vwap_reversion",
             "confidence": 0.0, "reason": "no_signal", "indicators": {}}

    try:
        if df is None or len(df) < 30:
            return {**_hold, "reason": "insufficient_data"}

        close_col = "Close" if "Close" in df.columns else "close"
        last_close = float(df[close_col].iloc[-1])
        if last_close <= 0:
            return {**_hold, "reason": "invalid_close"}

        vwap_lower_s, vwap_s, vwap_upper_s = calculate_vwap_bands(df, period=20, std_mult=1.5)
        rsi_s     = calculate_rsi(df, 14)
        atr_s     = calculate_atr(df, 14)
        vol_ratio = _safe(calculate_volume_ratio(df, 20))
        chop_s    = calculate_choppiness_index(df, 14)

        current_vwap = _safe(vwap_s)
        vwap_lower   = _safe(vwap_lower_s, current_vwap * (1.0 - VWAP_DEV_MIN))
        vwap_upper   = _safe(vwap_upper_s, current_vwap * (1.0 + VWAP_DEV_MIN))
        current_rsi  = _safe(rsi_s)
        current_atr  = _safe(atr_s)
        current_chop = _safe(chop_s, 50.0)

        if current_vwap <= 0:
            return {**_hold, "reason": "vwap_unavailable"}

        # Deviation from VWAP
        dev_pct = (last_close - current_vwap) / current_vwap
        band_half_width = max(current_vwap - vwap_lower, vwap_upper - current_vwap, current_vwap * VWAP_DEV_MIN)
        dev_z = (last_close - current_vwap) / band_half_width

        action = "HOLD"
        below_band = last_close <= vwap_lower or dev_pct <= -VWAP_DEV_MIN
        above_band = last_close >= vwap_upper or dev_pct >= VWAP_DEV_MIN

        if below_band and current_rsi <= RSI_OVERSOLD and vol_ratio >= VOL_MIN:
            action = "BUY"
        elif above_band and current_rsi >= RSI_OVERBOUGHT and vol_ratio >= VOL_MIN:
            action = "SELL"

        if action == "HOLD":
            return {**_hold, "reason": f"no_deviation_rsi={current_rsi:.1f}_dev={dev_pct:.3f}"}

        # Confidence
        conf = CONF_BASE
        abs_dev = abs(dev_pct)
        conf += 0.15 * min(1.0, max(0.0, abs(dev_z)) / 2.0)

        if action == "BUY":
            rsi_extreme = max(0, RSI_OVERSOLD - current_rsi)
            conf += 0.10 * min(1.0, rsi_extreme / 15.0)
        else:
            rsi_extreme = max(0, current_rsi - RSI_OVERBOUGHT)
            conf += 0.10 * min(1.0, rsi_extreme / 15.0)

        if current_chop >= 50:
            conf += 0.06
        elif current_chop < 38:
            conf -= 0.08

        if vol_ratio >= 1.5:
            conf += 0.08

        conf = round(min(conf, CONF_MAX), 4)

        stop   = last_close - 2 * current_atr if action == "BUY" else last_close + 2 * current_atr
        target = current_vwap   # natural target = VWAP

        return {
            "action":     action,
            "strategy":   "vwap_reversion",
            "confidence": conf,
            "reason":     f"vwap_dev={dev_pct:.3f}_rsi={current_rsi:.1f}",
            "indicators": {
                "vwap":      round(current_vwap, 2),
                "vwap_lower": round(vwap_lower, 2),
                "vwap_upper": round(vwap_upper, 2),
                "dev_pct":   round(dev_pct,   4),
                "dev_z":     round(dev_z,     3),
                "rsi":       round(current_rsi, 2),
                "atr":       round(current_atr, 2),
                "vol_ratio": round(vol_ratio,   2),
                "chop":      round(current_chop, 2),
                "stop":      round(stop,    2),
                "target":    round(target,  2),
            },
        }

    except Exception as exc:
        logger.exception("vwap_reversion_signal failed: %s", exc)
        return {**_hold, "reason": f"error:{exc}"}
