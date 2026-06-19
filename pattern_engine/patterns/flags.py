"""
flags.py — bull/bear flags and pennants (continuation patterns).

Unlike the reversal patterns, a flag/pennant resumes the prior move:

  1. FLAGPOLE: a sharp, near-vertical impulse (>= pole_min_move_pct over a short
     window). Its direction is the trade direction.
  2. CONSOLIDATION: a brief, tight drift that retraces only part of the pole
     (range <= flag_max_range_ratio * pole height; retrace <= flag_max_retrace).
  3. BREAKOUT: the first close beyond the consolidation extreme in the pole's
     direction confirms; measured-move target = pole height from the breakout.

Flag vs pennant is classified by whether the consolidation's range CONTRACTS in
its second half (pennant) or stays roughly parallel (flag). Pole moves are
percentage-based, so detection is scale-invariant.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from ..base import PatternDetector, PatternResult, Direction, DetectionContext
from ..pattern_scoring import blend
from . import volume_score, trend_score, structure_score
from .double_top import _dedup
from .triangles import _ts


class FlagDetector(PatternDetector):
    name = "flag"

    def detect(self, df, context: DetectionContext, symbol: str) -> List[PatternResult]:
        cfg = self.config
        fl = cfg.get("flags", {}) or {}
        pole_min_move = float(fl.get("pole_min_move_pct", 0.05))
        pole_min_bars = int(fl.get("pole_min_bars", 3))
        pole_max_bars = int(fl.get("pole_max_bars", 15))
        flag_min_bars = int(fl.get("flag_min_bars", 3))
        flag_max_bars = int(fl.get("flag_max_bars", 25))
        flag_max_range = float(fl.get("flag_max_range_ratio", 0.5))
        flag_max_retrace = float(fl.get("flag_max_retrace", 0.5))
        pennant_converge = float(fl.get("pennant_converge_ratio", 0.7))
        sc = cfg.scoring

        close = df["close"].to_numpy(float)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        n = len(df)
        step = max(1, int(cfg.pivots["right_bars"]))
        out: List[PatternResult] = []

        for p in range(pole_min_bars, n - flag_min_bars, step):
            # strongest impulse ending at bar p, base in [p-max, p-min]
            best_ps: Optional[int] = None
            best_move = 0.0
            for ps in range(max(0, p - pole_max_bars), max(0, p - pole_min_bars) + 1):
                base = close[ps] or 1.0
                m = (close[p] - close[ps]) / base
                if abs(m) > abs(best_move):
                    best_move, best_ps = m, ps
            if best_ps is None or abs(best_move) < pole_min_move:
                continue
            want = Direction.LONG if best_move > 0 else Direction.SHORT
            pole_height = abs(close[p] - close[best_ps])
            if pole_height <= 0:
                continue

            # grow the consolidation forward until the first valid breakout
            fhi, flo = -np.inf, np.inf
            brk: Optional[int] = None
            for j in range(p + 1, min(n, p + 1 + flag_max_bars)):
                clen = j - p - 1                    # bars already consolidated
                if clen >= flag_min_bars:
                    if want == Direction.LONG and close[j] > fhi:
                        brk = j; break
                    if want == Direction.SHORT and close[j] < flo:
                        brk = j; break
                fhi = max(fhi, high[j]); flo = min(flo, low[j])
            if brk is None:
                continue

            chi = float(high[p + 1:brk].max()); clo = float(low[p + 1:brk].min())
            if (chi - clo) > flag_max_range * pole_height:      # must stay tight
                continue
            retrace = (close[p] - clo) if want == Direction.LONG else (chi - close[p])
            if retrace > flag_max_retrace * pole_height:        # not a deep reversal
                continue

            entry = float(close[brk])
            if want == Direction.LONG:
                stop_px, target = clo, entry + pole_height
            else:
                stop_px, target = chi, entry - pole_height
            if abs(entry - stop_px) <= 0:
                continue

            ptype = self._classify(high, low, p + 1, brk, pennant_converge, want)

            vscore, vconf = volume_score(df, best_ps, brk, brk,
                                         int(cfg.volume["ma_period"]),
                                         float(cfg.volume["expansion_ratio"]))
            pole_strength = min(abs(best_move) / (2.0 * pole_min_move), 1.0)
            tightness = 1.0 - min((chi - clo) / (flag_max_range * pole_height), 1.0)
            quality = float(np.clip(pole_strength * 60.0 + tightness * 40.0, 0, 100))
            regime = str(context.structure.get("regime", "UNKNOWN"))
            subs = {"pattern_quality": quality, "volume": vscore,
                    "trend": trend_score(regime, "BULL" if want == Direction.LONG else "BEAR"),
                    "market_structure": structure_score(regime), "breakout": 75.0}
            conf = blend(subs, sc["weights"])
            if conf < float(sc["min_confidence_to_emit"]):
                continue
            out.append(PatternResult(
                symbol=symbol, timestamp=_ts(df, brk), pattern=ptype, direction=want,
                confidence=conf, entry=entry, stop_loss=stop_px, target=target,
                breakout_level=chi if want == Direction.LONG else clo,
                volume_confirmation=vconf, market_structure=regime,
                breakout_confirmed=True, start_index=best_ps, end_index=brk,
                sub_scores=subs,
                meta={"pole_move_pct": round(best_move, 4),
                      "pole_height": round(pole_height, 2),
                      "flag_bars": int(brk - p - 1)}))
        return _dedup(out)

    @staticmethod
    def _classify(high, low, s: int, e: int, converge_ratio: float,
                  want: Direction) -> str:
        """flag (parallel drift) vs pennant (range contracts in 2nd half)."""
        side = "bull" if want == Direction.LONG else "bear"
        mid = (s + e) // 2
        if mid - s >= 1 and e - mid >= 1:
            sp1 = float(high[s:mid].max() - low[s:mid].min())
            sp2 = float(high[mid:e].max() - low[mid:e].min())
            if sp1 > 0 and sp2 < sp1 * converge_ratio:
                return f"{side}_pennant"
        return f"{side}_flag"
