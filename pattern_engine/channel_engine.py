"""
channel_engine.py — detect parallel / near-parallel price channels.

The helpers are deliberately pure and small: they consume a validated OHLCV
frame plus pivot indices and return geometric channel candidates. Pattern
detectors decide whether the channel is actionable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

from .trendline_engine import TrendLine, fit_trendline


@dataclass
class Channel:
    kind: str
    upper: TrendLine
    lower: TrendLine
    start_index: int
    end_index: int
    width: float
    strength: float
    upper_touches: int
    lower_touches: int


def _classify_channel(upper: TrendLine, lower: TrendLine, price_ref: float,
                      flat_slope_pct: float, parallel_tolerance_pct: float) -> str:
    if price_ref <= 0:
        return "UNKNOWN"
    us = upper.slope / price_ref
    ls = lower.slope / price_ref
    if abs(us) < flat_slope_pct and abs(ls) < flat_slope_pct:
        return "HORIZONTAL_CHANNEL"
    if abs(us - ls) <= parallel_tolerance_pct:
        if us > flat_slope_pct and ls > flat_slope_pct:
            return "ASCENDING_CHANNEL"
        if us < -flat_slope_pct and ls < -flat_slope_pct:
            return "DESCENDING_CHANNEL"
    return "UNKNOWN"


def detect_channels(
    df: pd.DataFrame,
    swing_highs: List[int],
    swing_lows: List[int],
    *,
    min_touches: int = 2,
    lookback: int = 80,
    flat_slope_pct: float = 0.0004,
    parallel_tolerance_pct: float = 0.0008,
    touch_tolerance_pct: float = 0.0015,
) -> List[Channel]:
    """Return recent horizontal / ascending / descending channel candidates."""
    if df is None or len(df) < 10:
        return []
    n = len(df)
    start = max(0, n - lookback)
    highs = [i for i in swing_highs if start <= i < n]
    lows = [i for i in swing_lows if start <= i < n]
    if len(highs) < min_touches or len(lows) < min_touches:
        return []

    high_arr = df["high"].to_numpy(float)
    low_arr = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    price_ref = float(np.nanmean(close[start:])) or 1.0

    upper = fit_trendline([(i, high_arr[i]) for i in highs], "least_squares",
                          touch_tolerance_pct)
    lower = fit_trendline([(i, low_arr[i]) for i in lows], "least_squares",
                          touch_tolerance_pct)
    kind = _classify_channel(upper, lower, price_ref, flat_slope_pct,
                             parallel_tolerance_pct)
    if kind == "UNKNOWN":
        return []

    s = min(highs[0], lows[0])
    e = n - 1
    width = float(abs(upper.y_at(e) - lower.y_at(e)))
    if width <= 0:
        return []
    touch_score = min(1.0, (upper.touch_count + lower.touch_count) /
                      max(1.0, float(min_touches * 2)))
    fit_score = max(0.0, min(1.0, (upper.r_squared + lower.r_squared) / 2.0))
    strength = round((touch_score * 0.55 + fit_score * 0.45) * 100.0, 2)
    return [Channel(
        kind=kind,
        upper=upper,
        lower=lower,
        start_index=s,
        end_index=e,
        width=width,
        strength=strength,
        upper_touches=upper.touch_count,
        lower_touches=lower.touch_count,
    )]
