"""triple_top.py — bearish reversal: three similar peaks, neckline breakdown."""
from __future__ import annotations

from itertools import combinations
from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from .common import confidence, last_results, make_result, vscore


class TripleTopDetector(PatternDetector):
    name = "triple_top"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        tol = float(cfg.patterns["peak_tolerance_pct"])
        min_depth = float(cfg.patterns["min_trough_depth_pct"])
        minb = int(cfg.patterns["min_pattern_bars"])
        maxb = int(cfg.patterns["max_pattern_bars"])
        highs = context.swing_highs
        H = df["high"].to_numpy(float)
        L = df["low"].to_numpy(float)
        C = df["close"].to_numpy(float)
        out: List[PatternResult] = []
        for i1, i2, i3 in combinations(highs, 3):
            if not (minb <= i3 - i1 <= maxb):
                continue
            peaks = [H[i1], H[i2], H[i3]]
            if (max(peaks) - min(peaks)) / max(peaks) > tol:
                continue
            neckline = float(np.min(L[i1:i3 + 1]))
            peak = float(max(peaks))
            if (peak - neckline) / peak < min_depth:
                continue
            brk = next((j for j in range(i3 + 1, min(len(df), i3 + maxb))
                        if C[j] < neckline), None)
            if brk is None:
                continue
            vol, vconf = vscore(cfg, df, i1, brk)
            equality = 1.0 - ((max(peaks) - min(peaks)) / max(peaks)) / max(tol, 1e-9)
            quality = 55.0 + max(0.0, equality) * 25.0 + min((peak - neckline) / peak, 0.06) / 0.06 * 20.0
            conf, subs = confidence(cfg, context, Direction.SHORT, quality, volume=vol)
            r = make_result(
                cfg, df, context, symbol=symbol, pattern=self.name,
                direction=Direction.SHORT, confidence_score=conf,
                entry=neckline, stop=peak, target=neckline - (peak - neckline),
                level=neckline, start=i1, end=brk, subs=subs,
                volume_confirmed=vconf, meta={"peaks": [float(x) for x in peaks]},
            )
            if r:
                out.append(r)
        return last_results(out)
