"""
wedges.py — rising wedge (bearish) and falling wedge (bullish).

Unlike triangles, BOTH trendlines slope the same way and converge:
  rising wedge : both lines rising, lower steeper (converging) → breakdown → SHORT
  falling wedge: both lines falling, upper steeper (converging) → breakout  → LONG
Confirmed by a close beyond the far line; measured-move target = widest height.
Both lines are sloped, so r² validates both (no flat-line special case).
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from ..base import PatternDetector, PatternResult, Direction, DetectionContext
from ..pattern_scoring import blend
from ..trendline_engine import fit_trendline
from . import volume_score, trend_score, structure_score
from .double_top import _dedup
from .triangles import _ts


class WedgeDetector(PatternDetector):
    name = "wedge"

    def detect(self, df, context: DetectionContext, symbol: str) -> List[PatternResult]:
        cfg = self.config
        tri = cfg.get("triangles", {}) or {}
        flat = float(tri.get("flat_slope_pct", 0.0004))
        conv_r = float(tri.get("converge_ratio", 0.85))
        min_r2 = float(tri.get("min_line_r2", 0.5))
        min_touch = int(tri.get("min_touches_per_line", 2))
        minb = int(cfg.patterns["min_pattern_bars"]); maxb = int(cfg.patterns["max_pattern_bars"])
        tl_tol = float(cfg.trendline["touch_tolerance_pct"]); sc = cfg.scoring

        SH, SL = context.swing_highs, context.swing_lows
        close = df["close"].to_numpy(float); high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        n = len(df); step = max(2, int(cfg.pivots["right_bars"]) + 1)
        out: List[PatternResult] = []

        for e in range(minb, n, step):
            s = max(0, e - maxb)
            sh = [i for i in SH if s <= i <= e]
            sl = [i for i in SL if s <= i <= e]
            if len(sh) < min_touch or len(sl) < min_touch:
                continue
            up = fit_trendline([(i, high[i]) for i in sh], "least_squares", tl_tol)
            lo = fit_trendline([(i, low[i]) for i in sl], "least_squares", tl_tol)
            if up.r_squared < min_r2 or lo.r_squared < min_r2:
                continue
            price = float(np.mean(close[s:e + 1])) or 1.0
            us, ls = up.slope / price, lo.slope / price
            w0 = up.y_at(s) - lo.y_at(s); w1 = up.y_at(e) - lo.y_at(e)
            if w0 <= 0 or w1 <= 0 or w1 >= w0 * conv_r:        # must converge
                continue

            ptype: Optional[str] = None; want: Optional[Direction] = None
            if us > flat and ls > flat and lo.slope > up.slope:        # rising wedge
                ptype, want = "rising_wedge", Direction.SHORT
            elif us < -flat and ls < -flat and up.slope < lo.slope:    # falling wedge
                ptype, want = "falling_wedge", Direction.LONG
            if ptype is None:
                continue

            brk = None
            for j in range(e + 1, min(n, e + maxb)):
                if want == Direction.SHORT and close[j] < lo.y_at(j):
                    brk = j; break
                if want == Direction.LONG and close[j] > up.y_at(j):
                    brk = j; break
            if brk is None:
                continue

            entry = float(close[brk]); height = float(w0)
            if want == Direction.LONG:
                stop_px = float(lo.y_at(brk)); target = entry + height
            else:
                stop_px = float(up.y_at(brk)); target = entry - height
            if abs(entry - stop_px) <= 0:
                continue

            vscore, vconf = volume_score(df, s, brk, brk,
                                         int(cfg.volume["ma_period"]),
                                         float(cfg.volume["expansion_ratio"]))
            quality = float(np.clip((up.r_squared + lo.r_squared) / 2 * 70 +
                                    min(len(sh) + len(sl), 8) / 8 * 30, 0, 100))
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
                breakout_level=entry, volume_confirmation=vconf, market_structure=regime,
                breakout_confirmed=True, start_index=s, end_index=brk, sub_scores=subs,
                meta={"upper_slope": round(us, 6), "lower_slope": round(ls, 6)}))
        return _dedup(out)
