"""
patterns — individual detectors. Each module exposes a PatternDetector subclass.

Shared scoring helpers live here so detectors stay small and consistent.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def volume_score(df: pd.DataFrame, start: int, end: int, break_idx: int,
                 ma_period: int, expansion_ratio: float) -> tuple[float, bool]:
    """0-100 volume score + bool confirmation: breakout bar volume vs its MA."""
    if break_idx <= 0 or break_idx >= len(df):
        return 50.0, False
    vol = df["volume"].to_numpy(dtype=float)
    lo = max(0, break_idx - ma_period)
    ma = vol[lo:break_idx].mean() if break_idx > lo else vol[break_idx]
    if ma <= 0:
        return 50.0, False
    ratio = vol[break_idx] / ma
    confirmed = ratio >= expansion_ratio
    score = float(np.clip(50.0 + (ratio - 1.0) * 50.0, 0.0, 100.0))
    return score, bool(confirmed)


def trend_score(structure_regime: str, want: str) -> float:
    """Reward patterns aligned with structure. `want` in {BULL,BEAR}."""
    if want == "BEAR":
        return {"BEAR_TREND": 90, "TRANSITION": 60, "RANGE": 50,
                "BULL_TREND": 25}.get(structure_regime, 50)
    return {"BULL_TREND": 90, "TRANSITION": 60, "RANGE": 50,
            "BEAR_TREND": 25}.get(structure_regime, 50)


def structure_score(structure_regime: str) -> float:
    return {"BULL_TREND": 80, "BEAR_TREND": 80, "TRANSITION": 65,
            "RANGE": 55, "UNKNOWN": 40}.get(structure_regime, 50)
