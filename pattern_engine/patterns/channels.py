"""channels.py — ascending and descending channel breakout detectors."""
from __future__ import annotations

from typing import List

import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from ..channel_engine import detect_channels
from .common import confidence, last_results, make_result, vscore


class ChannelDetector(PatternDetector):
    name = "channel"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        cfg = self.config
        ch_cfg = cfg.get("channels", {}) or {}
        channels = detect_channels(
            df, context.swing_highs, context.swing_lows,
            min_touches=int(ch_cfg.get("min_touches", 2)),
            lookback=int(ch_cfg.get("lookback", 80)),
            flat_slope_pct=float(ch_cfg.get("flat_slope_pct", 0.0004)),
            parallel_tolerance_pct=float(ch_cfg.get("parallel_tolerance_pct", 0.0008)),
            touch_tolerance_pct=float(cfg.trendline["touch_tolerance_pct"]),
        )
        close = df["close"].to_numpy(float)
        brk = len(df) - 1
        out: List[PatternResult] = []
        for ch in channels:
            if ch.kind not in ("ASCENDING_CHANNEL", "DESCENDING_CHANNEL"):
                continue
            upper = float(ch.upper.y_at(brk))
            lower = float(ch.lower.y_at(brk))
            if close[brk] > upper:
                direction = Direction.LONG
                pattern = "ascending_channel" if ch.kind == "ASCENDING_CHANNEL" else "descending_channel"
                entry, stop, target, level = close[brk], lower, close[brk] + ch.width, upper
            elif close[brk] < lower:
                direction = Direction.SHORT
                pattern = "ascending_channel" if ch.kind == "ASCENDING_CHANNEL" else "descending_channel"
                entry, stop, target, level = close[brk], upper, close[brk] - ch.width, lower
            else:
                continue
            vol, vconf = vscore(cfg, df, ch.start_index, brk)
            conf, subs = confidence(cfg, context, direction, ch.strength, volume=vol)
            r = make_result(
                cfg, df, context, symbol=symbol, pattern=pattern,
                direction=direction, confidence_score=conf, entry=entry,
                stop=stop, target=target, level=level,
                start=ch.start_index, end=brk, subs=subs,
                volume_confirmed=vconf,
                meta={"channel_kind": ch.kind, "width": ch.width},
            )
            if r:
                out.append(r)
        return last_results(out)


class AscendingChannelDetector(ChannelDetector):
    name = "ascending_channel"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        return [r for r in super().detect(df, context, symbol)
                if r.pattern == self.name]


class DescendingChannelDetector(ChannelDetector):
    name = "descending_channel"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        return [r for r in super().detect(df, context, symbol)
                if r.pattern == self.name]
