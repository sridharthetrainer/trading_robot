"""smart_money.py — wick/sweep/retest/range-expansion intraday structures."""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternDetector, PatternResult
from .common import confidence, make_result, vscore


class SmartMoneyDetector(PatternDetector):
    name = "smart_money"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        if len(df) < 20:
            return []
        out: List[PatternResult] = []
        out.extend(self._liquidity_sweeps(df, context, symbol))
        out.extend(self._failed_breaks(df, context, symbol))
        out.extend(self._breakout_retest(df, context, symbol))
        out.extend(self._range_expansion(df, context, symbol))
        out.extend(self._volatility_compression(df, context, symbol))
        out.extend(self._opening_range_breakout(df, context, symbol))
        out.extend(self._trend_day(df, context, symbol))
        return sorted([r for r in out if r], key=lambda r: r.confidence, reverse=True)

    def _emit(self, df, context, symbol, pattern, direction, entry, stop, target,
              level, start, end, quality, meta=None):
        cfg = self.config
        vol, vconf = vscore(cfg, df, start, end)
        conf, subs = confidence(cfg, context, direction, quality, volume=vol)
        return make_result(
            cfg, df, context, symbol=symbol, pattern=pattern,
            direction=direction, confidence_score=conf, entry=entry,
            stop=stop, target=target, level=level, start=start, end=end,
            subs=subs, volume_confirmed=vconf, meta=meta or {},
        )

    def _liquidity_sweeps(self, df, context, symbol):
        cfg = self.config
        sm = cfg.get("smart_money", {}) or {}
        lookback = int(sm.get("sweep_lookback", 20))
        wick_ratio = float(sm.get("wick_body_ratio", 1.4))
        end = len(df) - 1
        start = max(0, end - lookback)
        H = df["high"].to_numpy(float); L = df["low"].to_numpy(float)
        O = df["open"].to_numpy(float); C = df["close"].to_numpy(float)
        prev_high = float(np.max(H[start:end])); prev_low = float(np.min(L[start:end]))
        body = abs(C[end] - O[end]) or 1e-9
        upper_wick = H[end] - max(O[end], C[end])
        lower_wick = min(O[end], C[end]) - L[end]
        out = []
        if H[end] > prev_high and C[end] < prev_high and upper_wick / body >= wick_ratio:
            out.append(self._emit(df, context, symbol, "liquidity_sweep_high",
                                  Direction.SHORT, C[end], H[end],
                                  C[end] - (H[end] - prev_high), prev_high,
                                  start, end, 82, {"swept_level": prev_high}))
            out.append(self._emit(df, context, symbol, "stop_hunt",
                                  Direction.SHORT, C[end], H[end],
                                  C[end] - (H[end] - prev_high), prev_high,
                                  start, end, 76, {"side": "above_high"}))
        if L[end] < prev_low and C[end] > prev_low and lower_wick / body >= wick_ratio:
            out.append(self._emit(df, context, symbol, "liquidity_sweep_low",
                                  Direction.LONG, C[end], L[end],
                                  C[end] + (prev_low - L[end]), prev_low,
                                  start, end, 82, {"swept_level": prev_low}))
            out.append(self._emit(df, context, symbol, "stop_hunt",
                                  Direction.LONG, C[end], L[end],
                                  C[end] + (prev_low - L[end]), prev_low,
                                  start, end, 76, {"side": "below_low"}))
        return out

    def _failed_breaks(self, df, context, symbol):
        end = len(df) - 1
        start = max(0, end - 25)
        H = df["high"].to_numpy(float); L = df["low"].to_numpy(float); C = df["close"].to_numpy(float)
        prev_high = float(np.max(H[start:end - 1])); prev_low = float(np.min(L[start:end - 1]))
        out = []
        if end >= 2 and H[end - 1] > prev_high and C[end] < prev_high:
            out.append(self._emit(df, context, symbol, "failed_breakout",
                                  Direction.SHORT, C[end], max(H[end - 1], H[end]),
                                  C[end] - abs(max(H[end - 1], H[end]) - prev_high),
                                  prev_high, start, end, 78))
        if end >= 2 and L[end - 1] < prev_low and C[end] > prev_low:
            out.append(self._emit(df, context, symbol, "failed_breakdown",
                                  Direction.LONG, C[end], min(L[end - 1], L[end]),
                                  C[end] + abs(prev_low - min(L[end - 1], L[end])),
                                  prev_low, start, end, 78))
        return out

    def _breakout_retest(self, df, context, symbol):
        end = len(df) - 1
        start = max(0, end - 30)
        H = df["high"].to_numpy(float); L = df["low"].to_numpy(float); C = df["close"].to_numpy(float)
        if end < 4:
            return []
        level_high = float(np.max(H[start:end - 3]))
        level_low = float(np.min(L[start:end - 3]))
        out = []
        if C[end - 3] > level_high and L[end] <= level_high <= C[end]:
            out.append(self._emit(df, context, symbol, "breakout_retest",
                                  Direction.LONG, C[end], min(L[end], level_high * 0.998),
                                  C[end] + abs(C[end] - level_high) * 2.0,
                                  level_high, start, end, 80))
        if C[end - 3] < level_low and H[end] >= level_low >= C[end]:
            out.append(self._emit(df, context, symbol, "breakout_retest",
                                  Direction.SHORT, C[end], max(H[end], level_low * 1.002),
                                  C[end] - abs(level_low - C[end]) * 2.0,
                                  level_low, start, end, 80))
        return out

    def _range_expansion(self, df, context, symbol):
        end = len(df) - 1
        start = max(0, end - 20)
        H = df["high"].to_numpy(float); L = df["low"].to_numpy(float); C = df["close"].to_numpy(float)
        ranges = H - L
        avg = float(np.mean(ranges[start:end])) if end > start else 0.0
        if avg <= 0 or ranges[end] < avg * 1.8:
            return []
        direction = Direction.LONG if C[end] >= C[end - 1] else Direction.SHORT
        stop = L[end] if direction == Direction.LONG else H[end]
        target = C[end] + ranges[end] if direction == Direction.LONG else C[end] - ranges[end]
        return [self._emit(df, context, symbol, "range_expansion",
                           direction, C[end], stop, target, C[end],
                           start, end, 74, {"range_ratio": round(float(ranges[end] / avg), 2)})]

    def _volatility_compression(self, df, context, symbol):
        end = len(df) - 1
        start = max(0, end - 30)
        H = df["high"].to_numpy(float); L = df["low"].to_numpy(float); C = df["close"].to_numpy(float)
        ranges = H - L
        if len(ranges[start:end + 1]) < 10:
            return []
        recent = float(np.mean(ranges[end - 5:end]))
        base = float(np.mean(ranges[start:end - 5]))
        if base <= 0 or recent > base * 0.55:
            return []
        high = float(np.max(H[end - 5:end]))
        low = float(np.min(L[end - 5:end]))
        if C[end] > high:
            return [self._emit(df, context, symbol, "volatility_compression",
                               Direction.LONG, C[end], low, C[end] + (high - low),
                               high, start, end, 76)]
        if C[end] < low:
            return [self._emit(df, context, symbol, "volatility_compression",
                               Direction.SHORT, C[end], high, C[end] - (high - low),
                               low, start, end, 76)]
        return []

    def _opening_range_breakout(self, df, context, symbol):
        if len(df) < 8:
            return []
        end = len(df) - 1
        opening_bars = min(6, len(df) - 2)
        H = df["high"].to_numpy(float); L = df["low"].to_numpy(float); C = df["close"].to_numpy(float)
        oh = float(np.max(H[:opening_bars])); ol = float(np.min(L[:opening_bars]))
        if C[end] > oh:
            return [self._emit(df, context, symbol, "opening_range_breakout",
                               Direction.LONG, C[end], ol, C[end] + (oh - ol),
                               oh, 0, end, 78)]
        if C[end] < ol:
            return [self._emit(df, context, symbol, "opening_range_breakout",
                               Direction.SHORT, C[end], oh, C[end] - (oh - ol),
                               ol, 0, end, 78)]
        return []

    def _trend_day(self, df, context, symbol):
        end = len(df) - 1
        start = max(0, end - 30)
        C = df["close"].to_numpy(float); H = df["high"].to_numpy(float); L = df["low"].to_numpy(float)
        if end - start < 12:
            return []
        slope = float(np.polyfit(np.arange(end - start + 1), C[start:end + 1], 1)[0])
        net = C[end] - C[start]
        ranges = np.maximum(H[start:end + 1] - L[start:end + 1], 1e-9)
        directional = abs(net) / float(np.sum(ranges))
        if directional < 0.35:
            return []
        if slope > 0:
            return [self._emit(df, context, symbol, "trend_day_structure",
                               Direction.LONG, C[end], float(np.min(L[start:end + 1])),
                               C[end] + abs(net), C[end], start, end, 73,
                               {"directional_efficiency": round(directional, 2)})]
        return [self._emit(df, context, symbol, "trend_day_structure",
                           Direction.SHORT, C[end], float(np.max(H[start:end + 1])),
                           C[end] - abs(net), C[end], start, end, 73,
                           {"directional_efficiency": round(directional, 2)})]
