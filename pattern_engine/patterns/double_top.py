"""
double_top.py — bearish reversal: two peaks at ~equal price with a trough between,
confirmed by a close below the neckline (the trough). Measured-move target.
"""
from __future__ import annotations

from typing import List

import pandas as pd

from ..base import PatternDetector, PatternResult, Direction, DetectionContext
from ..pattern_scoring import blend
from . import volume_score, trend_score, structure_score


class DoubleTopDetector(PatternDetector):
    name = "double_top"

    def detect(self, df: pd.DataFrame, context: DetectionContext,
               symbol: str) -> List[PatternResult]:
        pc = self.config.patterns
        sc = self.config.scoring
        tol = float(pc["peak_tolerance_pct"])
        min_depth = float(pc["min_trough_depth_pct"])
        min_bars = int(pc["min_pattern_bars"])
        max_bars = int(pc["max_pattern_bars"])
        results: List[PatternResult] = []
        highs = context.swing_highs
        lows_arr = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        high_arr = df["high"].to_numpy(dtype=float)

        for a in range(len(highs)):
            for b in range(a + 1, len(highs)):
                i1, i2 = highs[a], highs[b]
                if not (min_bars <= (i2 - i1) <= max_bars):
                    continue
                p1, p2 = high_arr[i1], high_arr[i2]
                if abs(p1 - p2) / max(p1, p2) > tol:           # peaks ~equal
                    continue
                trough = lows_arr[i1:i2 + 1].min()
                peak = max(p1, p2)
                if (peak - trough) / peak < min_depth:          # meaningful dip
                    continue
                neckline = float(trough)
                # confirmation: a close below neckline AFTER the 2nd peak
                brk = None
                for j in range(i2 + 1, min(len(df), i2 + max_bars)):
                    if close[j] < neckline:
                        brk = j
                        break
                if brk is None:
                    continue

                vscore, vconf = volume_score(df, i1, brk, brk,
                                             int(self.config.volume["ma_period"]),
                                             float(self.config.volume["expansion_ratio"]))
                quality = self._quality(p1, p2, peak, trough, tol)
                regime = str(context.structure.get("regime", "UNKNOWN"))
                subs = {
                    "pattern_quality": quality,
                    "volume": vscore,
                    "trend": trend_score(regime, "BEAR"),
                    "market_structure": structure_score(regime),
                    "breakout": 80.0,   # confirmed close-through by construction
                }
                conf = blend(subs, sc["weights"])
                if conf < float(sc["min_confidence_to_emit"]):
                    continue

                height = peak - neckline
                entry = neckline
                stop = float(peak)
                target = neckline - height          # measured move
                results.append(PatternResult(
                    symbol=symbol, timestamp=self._ts(df, brk), pattern=self.name,
                    direction=Direction.SHORT, confidence=conf, entry=entry,
                    stop_loss=stop, target=target, breakout_level=neckline,
                    volume_confirmation=vconf, market_structure=regime,
                    breakout_confirmed=True, start_index=i1, end_index=brk,
                    sub_scores=subs, meta={"peak1": float(p1), "peak2": float(p2)},
                ))
        return _dedup(results)

    @staticmethod
    def _quality(p1: float, p2: float, peak: float, trough: float, tol: float) -> float:
        # tighter peak equality + deeper trough → higher quality
        eq = 1.0 - (abs(p1 - p2) / max(p1, p2)) / tol      # 1 = identical peaks
        depth = (peak - trough) / peak
        return float(max(0.0, min(100.0, 50.0 + eq * 30.0 + min(depth, 0.05) / 0.05 * 20.0)))

    @staticmethod
    def _ts(df: pd.DataFrame, i: int) -> str:
        if isinstance(df.index, pd.DatetimeIndex):
            return df.index[i].isoformat()
        return str(df.iloc[i].get("timestamp", i))


def _dedup(results: List[PatternResult]) -> List[PatternResult]:
    """Keep the highest-confidence pattern per overlapping confirmation bar."""
    best: dict = {}
    for r in results:
        key = r.end_index
        if key not in best or r.confidence > best[key].confidence:
            best[key] = r
    return sorted(best.values(), key=lambda r: r.end_index)
