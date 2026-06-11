"""rounding.py — rounding top / bottom reversal patterns via quadratic curvature."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from .common import confidence, make_result, vscore


class _RoundingBase(PatternDetector):
    pattern_name = "rounding"
    want = Direction.NEUTRAL
    curvature_sign = 0

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        rc = cfg.get("rounding", {}) or {}
        lookback = int(rc.get("lookback", 50))
        min_curve = float(rc.get("min_curvature", 0.00008))
        if len(df) < lookback:
            return []
        start = len(df) - lookback
        end = len(df) - 1
        close = df["close"].to_numpy(float)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        y = close[start:end + 1]
        x = np.arange(len(y), dtype=float)
        a, b, _ = np.polyfit(x, y, 2)
        norm_curve = a / max(float(np.mean(y)), 1e-9)
        if self.curvature_sign < 0 and norm_curve > -min_curve:
            return []
        if self.curvature_sign > 0 and norm_curve < min_curve:
            return []

        neckline = float(np.mean([y[0], y[-1]]))
        if self.want == Direction.SHORT:
            if close[end] >= neckline:
                return []
            entry, stop, target = close[end], float(np.max(high[start:end + 1])), close[end] - abs(float(np.max(high[start:end + 1])) - neckline)
        else:
            if close[end] <= neckline:
                return []
            entry, stop, target = close[end], float(np.min(low[start:end + 1])), close[end] + abs(neckline - float(np.min(low[start:end + 1])))
        vol, vconf = vscore(cfg, df, start, end)
        quality = min(100.0, 55.0 + abs(norm_curve) / max(min_curve, 1e-9) * 15.0)
        conf, subs = confidence(cfg, context, self.want, quality, volume=vol)
        r = make_result(
            cfg, df, context, symbol=symbol, pattern=self.pattern_name,
            direction=self.want, confidence_score=conf, entry=entry, stop=stop,
            target=target, level=neckline, start=start, end=end, subs=subs,
            volume_confirmed=vconf, meta={"curvature": round(norm_curve, 8)},
        )
        return [r] if r else []


class RoundingTopDetector(_RoundingBase):
    name = "rounding_top"
    pattern_name = "rounding_top"
    want = Direction.SHORT
    curvature_sign = -1


class RoundingBottomDetector(_RoundingBase):
    name = "rounding_bottom"
    pattern_name = "rounding_bottom"
    want = Direction.LONG
    curvature_sign = 1
