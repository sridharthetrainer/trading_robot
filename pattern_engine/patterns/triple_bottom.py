"""triple_bottom.py — bullish reversal: three similar lows, neckline breakout."""
from __future__ import annotations

from itertools import combinations
from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from .common import confidence, last_results, make_result, vscore


class TripleBottomDetector(PatternDetector):
    name = "triple_bottom"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        tol = float(cfg.patterns["peak_tolerance_pct"])
        min_depth = float(cfg.patterns["min_trough_depth_pct"])
        minb = int(cfg.patterns["min_pattern_bars"])
        maxb = int(cfg.patterns["max_pattern_bars"])
        lows = context.swing_lows
        H = df["high"].to_numpy(float)
        L = df["low"].to_numpy(float)
        C = df["close"].to_numpy(float)
        out: List[PatternResult] = []
        for i1, i2, i3 in combinations(lows, 3):
            if not (minb <= i3 - i1 <= maxb):
                continue
            troughs = [L[i1], L[i2], L[i3]]
            if (max(troughs) - min(troughs)) / max(max(troughs), 1e-9) > tol:
                continue
            neckline = float(np.max(H[i1:i3 + 1]))
            trough = float(min(troughs))
            if (neckline - trough) / neckline < min_depth:
                continue
            brk = next((j for j in range(i3 + 1, min(len(df), i3 + maxb))
                        if C[j] > neckline), None)
            if brk is None:
                continue
            vol, vconf = vscore(cfg, df, i1, brk)
            equality = 1.0 - ((max(troughs) - min(troughs)) /
                              max(max(troughs), 1e-9)) / max(tol, 1e-9)
            quality = 55.0 + max(0.0, equality) * 25.0 + min((neckline - trough) / neckline, 0.06) / 0.06 * 20.0
            conf, subs = confidence(cfg, context, Direction.LONG, quality, volume=vol)
            r = make_result(
                cfg, df, context, symbol=symbol, pattern=self.name,
                direction=Direction.LONG, confidence_score=conf,
                entry=neckline, stop=trough, target=neckline + (neckline - trough),
                level=neckline, start=i1, end=brk, subs=subs,
                volume_confirmed=vconf, meta={"troughs": [float(x) for x in troughs]},
            )
            if r:
                out.append(r)
        return last_results(out)
