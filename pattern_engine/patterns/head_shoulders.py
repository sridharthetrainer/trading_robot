"""
head_shoulders.py — Head & Shoulders (bearish) and Inverse H&S (bullish).

H&S: three peaks — left shoulder, higher head, right shoulder (shoulders ~equal
and below the head). Neckline through the two intervening troughs; a close below
it confirms. Measured-move target = neckline - (head - neckline). Inverse mirrors.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..base import PatternDetector, PatternResult, Direction, DetectionContext
from ..pattern_scoring import blend
from . import volume_score, trend_score, structure_score
from .double_top import _dedup


def _ts(df: pd.DataFrame, i: int) -> str:
    return df.index[i].isoformat() if isinstance(df.index, pd.DatetimeIndex) else str(i)


class HeadShouldersDetector(PatternDetector):
    name = "head_shoulders"

    def detect(self, df, context: DetectionContext, symbol: str) -> List[PatternResult]:
        pc = self.config.patterns; sc = self.config.scoring
        tol = float(pc["peak_tolerance_pct"]); minb = int(pc["min_pattern_bars"])
        maxb = int(pc["max_pattern_bars"])
        H = context.swing_highs; lows = df["low"].to_numpy(float); close = df["close"].to_numpy(float)
        highv = df["high"].to_numpy(float)
        out: List[PatternResult] = []
        for a in range(len(H) - 2):
            ls, hd, rs = H[a], H[a + 1], H[a + 2]
            if not (minb <= rs - ls <= maxb):
                continue
            pls, phd, prs = highv[ls], highv[hd], highv[rs]
            if not (phd > pls and phd > prs):                # head highest
                continue
            if abs(pls - prs) / max(pls, prs) > tol:         # shoulders ~equal
                continue
            t1 = lows[ls:hd + 1].min(); t2 = lows[hd:rs + 1].min()
            neck = float((t1 + t2) / 2.0)
            brk = next((j for j in range(rs + 1, min(len(df), rs + maxb))
                        if close[j] < neck), None)
            if brk is None:
                continue
            vscore, vconf = volume_score(df, ls, brk, brk,
                                         int(self.config.volume["ma_period"]),
                                         float(self.config.volume["expansion_ratio"]))
            sym_q = 1.0 - (abs(pls - prs) / max(pls, prs)) / tol
            quality = float(np.clip(50 + sym_q * 30 + min((phd - neck) / phd, 0.05) / 0.05 * 20, 0, 100))
            regime = str(context.structure.get("regime", "UNKNOWN"))
            subs = {"pattern_quality": quality, "volume": vscore,
                    "trend": trend_score(regime, "BEAR"),
                    "market_structure": structure_score(regime), "breakout": 80.0}
            conf = blend(subs, sc["weights"])
            if conf < float(sc["min_confidence_to_emit"]):
                continue
            height = phd - neck
            out.append(PatternResult(
                symbol=symbol, timestamp=_ts(df, brk), pattern=self.name,
                direction=Direction.SHORT, confidence=conf, entry=neck,
                stop_loss=float(phd), target=neck - height, breakout_level=neck,
                volume_confirmation=vconf, market_structure=regime,
                breakout_confirmed=True, start_index=ls, end_index=brk,
                sub_scores=subs, meta={"head": float(phd)}))
        return _dedup(out)


class InverseHeadShouldersDetector(PatternDetector):
    name = "inverse_head_shoulders"

    def detect(self, df, context: DetectionContext, symbol: str) -> List[PatternResult]:
        pc = self.config.patterns; sc = self.config.scoring
        tol = float(pc["peak_tolerance_pct"]); minb = int(pc["min_pattern_bars"])
        maxb = int(pc["max_pattern_bars"])
        L = context.swing_lows; highs = df["high"].to_numpy(float); close = df["close"].to_numpy(float)
        lowv = df["low"].to_numpy(float)
        out: List[PatternResult] = []
        for a in range(len(L) - 2):
            ls, hd, rs = L[a], L[a + 1], L[a + 2]
            if not (minb <= rs - ls <= maxb):
                continue
            pls, phd, prs = lowv[ls], lowv[hd], lowv[rs]
            if not (phd < pls and phd < prs):                # head lowest
                continue
            if abs(pls - prs) / min(pls, prs) > tol:
                continue
            t1 = highs[ls:hd + 1].max(); t2 = highs[hd:rs + 1].max()
            neck = float((t1 + t2) / 2.0)
            brk = next((j for j in range(rs + 1, min(len(df), rs + maxb))
                        if close[j] > neck), None)
            if brk is None:
                continue
            vscore, vconf = volume_score(df, ls, brk, brk,
                                         int(self.config.volume["ma_period"]),
                                         float(self.config.volume["expansion_ratio"]))
            sym_q = 1.0 - (abs(pls - prs) / min(pls, prs)) / tol
            quality = float(np.clip(50 + sym_q * 30 + min((neck - phd) / neck, 0.05) / 0.05 * 20, 0, 100))
            regime = str(context.structure.get("regime", "UNKNOWN"))
            subs = {"pattern_quality": quality, "volume": vscore,
                    "trend": trend_score(regime, "BULL"),
                    "market_structure": structure_score(regime), "breakout": 80.0}
            conf = blend(subs, sc["weights"])
            if conf < float(sc["min_confidence_to_emit"]):
                continue
            height = neck - phd
            out.append(PatternResult(
                symbol=symbol, timestamp=_ts(df, brk), pattern=self.name,
                direction=Direction.LONG, confidence=conf, entry=neck,
                stop_loss=float(phd), target=neck + height, breakout_level=neck,
                volume_confirmation=vconf, market_structure=regime,
                breakout_confirmed=True, start_index=ls, end_index=brk,
                sub_scores=subs, meta={"head": float(phd)}))
        return _dedup(out)
