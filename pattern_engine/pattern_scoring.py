"""
pattern_scoring.py — blend sub-scores into a 0-100 confidence.

Final = 0.35*quality + 0.20*volume + 0.20*trend + 0.15*structure + 0.10*breakout
(weights configurable). Each sub-score is 0-100; detectors supply what they can
and missing ones default to a neutral 50 so a pattern isn't punished for a
dimension it doesn't measure.
"""
from __future__ import annotations

from typing import Dict


def blend(sub_scores: Dict[str, float], weights: Dict[str, float]) -> float:
    total_w = 0.0
    acc = 0.0
    for key, w in weights.items():
        s = sub_scores.get(key, 50.0)
        acc += w * max(0.0, min(100.0, float(s)))
        total_w += w
    return round(acc / total_w, 1) if total_w > 0 else 0.0
