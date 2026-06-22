"""
double_bottom.py — bullish reversal: two troughs at ~equal price with a peak
between, confirmed by a close above the neckline (the peak). Mirror of double_top.
"""
from __future__ import annotations

from typing import List

import pandas as pd

from ..base import PatternDetector, PatternResult, Direction, DetectionContext
from ..pattern_scoring import blend
from . import volume_score, trend_score, structure_score
from .double_top import _dedup


class DoubleBottomDetector(PatternDetector):
    name = "double_bottom"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        pc = self.config.patterns
        sc = self.config.scoring
        tol = float(pc["peak_tolerance_pct"])
        min_depth = float(pc["min_trough_depth_pct"])
        min_bars = int(pc["min_pattern_bars"])
        max_bars = int(pc["max_pattern_bars"])
        results: List[PatternResult] = []
        lows = context.swing_lows
        high_arr = df["high"].to_numpy(dtype=float)
        low_arr = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)

        for a in range(len(lows)):
            for b in range(a + 1, len(lows)):
                i1, i2 = lows[a], lows[b]
                if not (min_bars <= (i2 - i1) <= max_bars):
                    continue
                t1, t2 = low_arr[i1], low_arr[i2]
                if abs(t1 - t2) / min(t1, t2) > tol:
                    continue
                peak = high_arr[i1:i2 + 1].max()
                trough = min(t1, t2)
                if (peak - trough) / peak < min_depth:
                    continue
                neckline = float(peak)
                brk = None
                for j in range(i2 + 1, min(len(df), i2 + max_bars)):
                    if close[j] > neckline:
                        brk = j
                        break
                if brk is None:
                    continue

                vscore, vconf = volume_score(df, i1, brk, brk,
                                             int(self.config.volume["ma_period"]),
                                             float(self.config.volume["expansion_ratio"]))
                eq = 1.0 - (abs(t1 - t2) / min(t1, t2)) / tol
                depth = (peak - trough) / peak
                quality = float(max(0.0, min(100.0, 50.0 + eq * 30.0 +
                                             min(depth, 0.05) / 0.05 * 20.0)))
                regime = str(context.structure.get("regime", "UNKNOWN"))
                subs = {
                    "pattern_quality": quality, "volume": vscore,
                    "trend": trend_score(regime, "BULL"),
                    "market_structure": structure_score(regime),
                    "breakout": 80.0,
                }
                conf = blend(subs, sc["weights"])
                if conf < float(sc["min_confidence_to_emit"]):
                    continue

                height = neckline - trough
                results.append(PatternResult(
                    symbol=symbol,
                    timestamp=df.index[brk].isoformat() if isinstance(df.index, pd.DatetimeIndex)
                    else str(brk),
                    pattern=self.name, direction=Direction.LONG, confidence=conf,
                    entry=neckline, stop_loss=float(trough), target=neckline + height,
                    breakout_level=neckline, volume_confirmation=vconf,
                    market_structure=regime, breakout_confirmed=True,
                    start_index=i1, end_index=brk, sub_scores=subs,
                    meta={"trough1": float(t1), "trough2": float(t2)},
                ))
        return _dedup(results)
