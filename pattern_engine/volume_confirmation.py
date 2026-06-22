"""
volume_confirmation.py — volume-based confirmation helpers.

Patterns are stronger when volume CONTRACTS during formation and EXPANDS on the
breakout. These stateless helpers return ratios + booleans against config
thresholds (volume.*). Used by detectors and usable standalone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class VolumeProfile:
    formation_ratio: float    # avg formation volume / prior baseline
    breakout_ratio: float     # breakout-bar volume / formation avg
    contracted: bool
    expanded: bool


def analyse_volume(df: pd.DataFrame, start: int, end: int, break_idx: int,
                   *, ma_period: int = 20, contraction_ratio: float = 0.8,
                   expansion_ratio: float = 1.5) -> VolumeProfile:
    vol = df["volume"].to_numpy(dtype=float)
    n = len(vol)
    start = max(0, start); end = min(n - 1, end); break_idx = min(n - 1, break_idx)
    base_lo = max(0, start - ma_period)
    baseline = vol[base_lo:start].mean() if start > base_lo else vol[start]
    formation = vol[start:end + 1].mean() if end >= start else vol[start]
    form_avg = formation if formation > 0 else 1.0
    f_ratio = formation / baseline if baseline > 0 else 1.0
    b_ratio = vol[break_idx] / form_avg
    return VolumeProfile(
        formation_ratio=round(float(f_ratio), 3),
        breakout_ratio=round(float(b_ratio), 3),
        contracted=bool(f_ratio <= contraction_ratio),
        expanded=bool(b_ratio >= expansion_ratio),
    )


def volume_quality_score(profile: VolumeProfile) -> float:
    """0-100: reward contraction-during + expansion-on-break."""
    score = 50.0
    if profile.contracted:
        score += 20.0
    if profile.expanded:
        score += 30.0 * min(profile.breakout_ratio / 2.0, 1.0)
    return float(np.clip(score, 0.0, 100.0))
