"""diamond.py — broadening then contracting structure with directional breakout."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from .common import confidence, make_result, vscore


class DiamondDetector(PatternDetector):
    name = "diamond"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        dc = cfg.get("diamond", {}) or {}
        lookback = int(dc.get("lookback", 80))
        min_ratio = float(dc.get("min_expand_contract_ratio", 1.2))
        if len(df) < max(30, lookback // 2):
            return []
        end = len(df) - 1
        start = max(0, end - lookback + 1)
        mid = start + (end - start) // 2
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        close = df["close"].to_numpy(float)
        w1 = float(np.max(high[start:mid + 1]) - np.min(low[start:mid + 1]))
        w2 = float(np.max(high[mid:end]) - np.min(low[mid:end]))
        if w1 <= 0 or w2 <= 0 or max(w1, w2) / max(min(w1, w2), 1e-9) < min_ratio:
            return []
        upper = float(np.max(high[mid:end]))
        lower = float(np.min(low[mid:end]))
        if close[end] > upper:
            direction, pattern = Direction.LONG, "diamond_bottom"
            entry, stop, target, level = close[end], lower, close[end] + max(w1, w2), upper
        elif close[end] < lower:
            direction, pattern = Direction.SHORT, "diamond_top"
            entry, stop, target, level = close[end], upper, close[end] - max(w1, w2), lower
        else:
            return []
        vol, vconf = vscore(cfg, df, start, end)
        quality = min(100.0, 55.0 + (max(w1, w2) / max(min(w1, w2), 1e-9) - 1.0) * 35.0)
        conf, subs = confidence(cfg, context, direction, quality, volume=vol)
        r = make_result(
            cfg, df, context, symbol=symbol, pattern=pattern,
            direction=direction, confidence_score=conf, entry=entry,
            stop=stop, target=target, level=level, start=start, end=end,
            subs=subs, volume_confirmed=vconf,
            meta={"width_first": round(w1, 2), "width_second": round(w2, 2)},
        )
        return [r] if r else []


class DiamondTopDetector(DiamondDetector):
    name = "diamond_top"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        return [r for r in super().detect(df, context, symbol) if r.pattern == self.name]


class DiamondBottomDetector(DiamondDetector):
    name = "diamond_bottom"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        return [r for r in super().detect(df, context, symbol) if r.pattern == self.name]
