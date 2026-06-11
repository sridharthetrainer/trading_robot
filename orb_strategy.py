"""
orb_strategy.py

Opening Range Breakout (ORB) strategy for NSE index options.

The ORB is the most reliable intraday pattern on NIFTY. Institutional
players who could not execute at open establish their directional bias
in the first 15 minutes (9:15-9:30). When price breaks that range with
volume confirmation, they add to positions — creating strong momentum.

Logic
-----
1. Record the Opening Range: High and Low of bars from 9:15 to 9:30
2. On the first 5-min bar that closes ABOVE range_high with volume surge → BUY signal
3. On the first 5-min bar that closes BELOW range_low with volume surge → SELL signal
4. Stop: opposite side of the opening range
5. Target: 1.5–2× range width projected from the breakout level

Filters applied
--------------
- ADX > 18 (some directional bias must exist)
- Volume on breakout bar >= 1.3× 20-bar average
- Bar must close beyond the range (not just touch)
- Not valid after 10:30 (stale ORB)
- India VIX < 20 preferred (not enforced — handled by live engine)

Confidence scoring
------------------
- Break magnitude vs ATR: larger break = higher confidence
- Volume surge ratio: more volume = stronger institutional conviction
- ADX level: higher ADX = trend confirming the break
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from indicators import calculate_adx, calculate_atr, calculate_volume_ratio

logger = logging.getLogger(__name__)

ORB_WINDOW_START  = dtime(9, 15)
ORB_WINDOW_END    = dtime(9, 30)
ORB_VALID_UNTIL   = dtime(10, 30)   # ORB signal stale after this
ORB_ADX_MIN       = 18.0
ORB_VOLUME_MIN    = 1.3             # breakout bar volume >= 1.3× avg
ORB_CONF_BASE     = 0.55
ORB_CONF_MAX      = 0.90


def _safe(s: pd.Series, default: float = 0.0) -> float:
    try:
        v = s.iloc[-1]
        return float(v) if pd.notna(v) else default
    except Exception:
        return default


def _get_opening_range(
    df: pd.DataFrame,
    now: Optional[datetime] = None,
) -> Optional[Tuple[float, float]]:
    """
    Extract the opening range high and low from the first 15 minutes
    of the current session.

    Returns (range_high, range_low) or None if insufficient data.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        return None

    _now  = now if now is not None else datetime.now()
    today = _now.date()
    session_start = pd.Timestamp(today).replace(
        hour=ORB_WINDOW_START.hour, minute=ORB_WINDOW_START.minute
    )
    session_end = pd.Timestamp(today).replace(
        hour=ORB_WINDOW_END.hour, minute=ORB_WINDOW_END.minute
    )

    # Angel candles carry a tz-aware index (UTC+5:30); match it so the
    # comparison is valid (was failing: tz-aware vs tz-naive Timestamp).
    if getattr(df.index, "tz", None) is not None:
        try:
            session_start = session_start.tz_localize(df.index.tz)
            session_end   = session_end.tz_localize(df.index.tz)
        except (TypeError, ValueError):
            session_start = session_start.tz_convert(df.index.tz)
            session_end   = session_end.tz_convert(df.index.tz)

    mask = (df.index >= session_start) & (df.index <= session_end)
    orb_bars = df[mask]

    if len(orb_bars) < 2:
        return None

    high_col = "High" if "High" in df.columns else "high"
    low_col  = "Low"  if "Low"  in df.columns else "low"

    range_high = float(orb_bars[high_col].max())
    range_low  = float(orb_bars[low_col].min())

    if range_high <= range_low:
        return None

    return range_high, range_low


def orb_signal(
    df: pd.DataFrame,
    adx_min:    float = ORB_ADX_MIN,
    volume_min: float = ORB_VOLUME_MIN,
    now:        Optional[datetime] = None,   # GA-12: injectable for backtesting
) -> Dict[str, Any]:
    """
    Compute the ORB signal for a given OHLCV DataFrame.

    Returns a signal dict compatible with signal_engine.generate_signal output:
    {
        "action":     "BUY" | "SELL" | "HOLD",
        "strategy":   "orb",
        "confidence": float,
        "reason":     str,
        "indicators": {...},
    }
    """
    _hold = {"action": "HOLD", "strategy": "orb", "confidence": 0.0,
             "reason": "no_signal", "indicators": {}}

    try:
        if df is None or len(df) < 20:
            return {**_hold, "reason": "insufficient_data"}

        # Only valid during ORB trading window
        # GA-12: use injected now for backtesting, datetime.now() for live
        effective_now = now if now is not None else datetime.now()
        now_t = effective_now.time()
        if not (ORB_WINDOW_END <= now_t <= ORB_VALID_UNTIL):
            return {**_hold, "reason": "outside_orb_window"}

        orb = _get_opening_range(df, now=effective_now)
        if orb is None:
            return {**_hold, "reason": "orb_range_unavailable"}

        range_high, range_low = orb
        range_width = range_high - range_low

        close_col  = "Close" if "Close" in df.columns else "close"
        high_col   = "High"  if "High"  in df.columns else "high"
        low_col    = "Low"   if "Low"   in df.columns else "low"

        last_close = float(df[close_col].iloc[-1])
        last_high  = float(df[high_col].iloc[-1])
        last_low   = float(df[low_col].iloc[-1])

        # ADX filter
        adx_series = calculate_adx(df, 14)
        current_adx = _safe(adx_series)
        if current_adx < adx_min:
            return {**_hold, "reason": f"adx_too_low_{current_adx:.1f}"}

        # ATR for magnitude scoring
        atr_series  = calculate_atr(df, 14)
        current_atr = _safe(atr_series, range_width)

        # Volume ratio
        vol_ratio = _safe(calculate_volume_ratio(df, 20))

        # --- Breakout detection ---
        action = "HOLD"
        break_pct = 0.0

        if last_close > range_high and vol_ratio >= volume_min:
            action    = "BUY"
            break_pct = (last_close - range_high) / max(current_atr, 1.0)
        elif last_close < range_low and vol_ratio >= volume_min:
            action    = "SELL"
            break_pct = (range_low - last_close) / max(current_atr, 1.0)
        else:
            return {**_hold, "reason": "no_orb_breakout"}

        # --- Confidence scoring ---
        conf = ORB_CONF_BASE

        # Break magnitude vs ATR (larger break = more confident)
        conf += 0.15 * min(1.0, break_pct / 1.5)

        # Volume surge above threshold
        if vol_ratio >= 2.0:
            conf += 0.10
        elif vol_ratio >= 1.5:
            conf += 0.05

        # ADX contribution
        conf += 0.10 * min(1.0, (current_adx - adx_min) / 20.0)

        conf = round(min(conf, ORB_CONF_MAX), 4)

        # Compute natural stop and target
        stop  = range_low  if action == "BUY" else range_high
        tgt1  = last_close + range_width * 1.5 if action == "BUY" else last_close - range_width * 1.5
        tgt2  = last_close + range_width * 2.0 if action == "BUY" else last_close - range_width * 2.0

        return {
            "action":      action,
            "strategy":    "orb",
            "confidence":  conf,
            "reason":      f"orb_breakout_vol={vol_ratio:.2f}",
            "indicators": {
                "range_high":  round(range_high, 2),
                "range_low":   round(range_low,  2),
                "range_width": round(range_width, 2),
                "break_pct":   round(break_pct,  4),
                "vol_ratio":   round(vol_ratio,  2),
                "adx":         round(current_adx, 2),
                "atr":         round(current_atr, 2),
                "stop":        round(stop,  2),
                "target1":     round(tgt1,  2),
                "target2":     round(tgt2,  2),
            },
        }

    except Exception as exc:
        logger.exception("orb_signal failed: %s", exc)
        return {**_hold, "reason": f"error:{exc}"}
