"""cup_handle.py — bullish cup-and-handle accumulation breakout."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from .common import confidence, make_result, vscore


class CupHandleDetector(PatternDetector):
    name = "cup_handle"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        cp = cfg.get("cup_handle", {}) or {}
        minb = int(cp.get("min_bars", 35))
        maxb = int(cp.get("max_bars", 120))
        max_depth = float(cp.get("max_depth_pct", 0.35))
        min_depth = float(cp.get("min_depth_pct", 0.05))
        handle_max = float(cp.get("handle_max_retrace", 0.4))
        if len(df) < minb:
            return []
        close = df["close"].to_numpy(float)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        end = len(df) - 1
        out: List[PatternResult] = []
        for start in range(max(0, end - maxb), max(1, end - minb + 1)):
            seg = close[start:end + 1]
            if len(seg) < minb:
                continue
            mid = start + int(np.argmin(seg))
            if mid <= start + 3 or mid >= end - 5:
                continue
            left_rim = float(np.max(high[start:mid + 1]))
            right_rim = float(np.max(high[mid:end + 1]))
            rim = min(left_rim, right_rim)
            bottom = float(low[mid])
            if rim <= 0:
                continue
            depth = (rim - bottom) / rim
            if not (min_depth <= depth <= max_depth):
                continue
            handle_start = max(mid + 1, end - max(5, (end - start) // 4))
            handle_low = float(np.min(low[handle_start:end + 1]))
            if (rim - handle_low) / max(rim - bottom, 1e-9) > handle_max:
                continue
            if close[end] <= rim:
                continue
            vol, vconf = vscore(cfg, df, start, end)
            quality = 60 + min(depth / max_depth, 1) * 20 + min((end - start) / maxb, 1) * 20
            conf, subs = confidence(cfg, context, Direction.LONG, quality, volume=vol)
            r = make_result(
                cfg, df, context, symbol=symbol, pattern=self.name,
                direction=Direction.LONG, confidence_score=conf,
                entry=close[end], stop=handle_low,
                target=close[end] + (rim - bottom), level=rim,
                start=start, end=end, subs=subs, volume_confirmed=vconf,
                meta={"rim": rim, "bottom": bottom, "depth_pct": round(depth * 100, 2)},
            )
            if r:
                out.append(r)
        return sorted(out, key=lambda r: r.confidence, reverse=True)[:1]
