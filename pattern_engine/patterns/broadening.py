"""broadening.py — expanding wedge / broadening formation breakouts."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from ..trendline_engine import fit_trendline
from .common import confidence, last_results, make_result, vscore


class BroadeningDetector(PatternDetector):
    name = "broadening_wedge"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        bc = cfg.get("broadening", {}) or {}
        lookback = int(bc.get("lookback", 80))
        min_ratio = float(bc.get("min_diverge_ratio", 1.15))
        start = max(0, len(df) - lookback)
        highs = [i for i in context.swing_highs if start <= i < len(df)]
        lows = [i for i in context.swing_lows if start <= i < len(df)]
        if len(highs) < 2 or len(lows) < 2:
            return []
        H = df["high"].to_numpy(float)
        L = df["low"].to_numpy(float)
        C = df["close"].to_numpy(float)
        up = fit_trendline([(i, H[i]) for i in highs], "least_squares",
                           float(cfg.trendline["touch_tolerance_pct"]))
        lo = fit_trendline([(i, L[i]) for i in lows], "least_squares",
                           float(cfg.trendline["touch_tolerance_pct"]))
        w0 = up.y_at(start) - lo.y_at(start)
        w1 = up.y_at(len(df) - 1) - lo.y_at(len(df) - 1)
        if w0 <= 0 or w1 <= w0 * min_ratio:
            return []
        end = len(df) - 1
        upper = float(up.y_at(end))
        lower = float(lo.y_at(end))
        if C[end] > upper:
            direction, entry, stop, target, level = Direction.LONG, C[end], lower, C[end] + w1, upper
        elif C[end] < lower:
            direction, entry, stop, target, level = Direction.SHORT, C[end], upper, C[end] - w1, lower
        else:
            return []
        vol, vconf = vscore(cfg, df, start, end)
        quality = min(100.0, 55.0 + min((w1 / max(w0, 1e-9) - 1.0), 1.0) * 45.0)
        conf, subs = confidence(cfg, context, direction, quality, volume=vol)
        r = make_result(
            cfg, df, context, symbol=symbol, pattern=self.name,
            direction=direction, confidence_score=conf, entry=entry,
            stop=stop, target=target, level=level, start=start, end=end,
            subs=subs, volume_confirmed=vconf,
            meta={"width0": round(float(w0), 2), "width1": round(float(w1), 2)},
        )
        return last_results([r] if r else [])
