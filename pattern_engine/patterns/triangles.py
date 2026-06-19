"""
triangles.py — ascending / descending / symmetrical / expanding triangles.

One detector classifies all four from the slopes of the upper (swing-high) and
lower (swing-low) trendlines plus whether the lines converge or diverge:

  ascending  : flat top  + rising bottom (converging)   → bullish
  descending : falling top + flat bottom (converging)   → bearish
  symmetrical: falling top + rising bottom (converging)  → breakout direction
  expanding  : rising top + falling bottom (diverging)   → breakout direction

Confirmed by a close beyond the relevant line; measured-move target = the
triangle's widest height. Slopes are normalised by price (scale-invariant).
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


def _ts(df: pd.DataFrame, i: int) -> str:
    return df.index[i].isoformat() if isinstance(df.index, pd.DatetimeIndex) else str(i)


def _line_valid(line, slope_norm: float, prices, flat: float, min_r2: float,
                max_flat_spread: float) -> bool:
    """A FLAT line (slope≈0) has r²≈0 by nature, so validate it by tight price
    clustering instead; a SLOPED line is validated by r²."""
    if abs(slope_norm) < flat:
        m = float(np.mean(prices))
        return m > 0 and (max(prices) - min(prices)) / m <= max_flat_spread
    return line.r_squared >= min_r2


class TriangleDetector(PatternDetector):
    name = "triangle"

    def detect(self, df, context: DetectionContext, symbol: str) -> List[PatternResult]:
        cfg = self.config
        tri = cfg.get("triangles", {}) or {}
        flat = float(tri.get("flat_slope_pct", 0.0004))
        min_touch = int(tri.get("min_touches_per_line", 2))
        conv_r = float(tri.get("converge_ratio", 0.85))
        div_r = float(tri.get("diverge_ratio", 1.15))
        min_r2 = float(tri.get("min_line_r2", 0.5))
        minb = int(cfg.patterns["min_pattern_bars"]); maxb = int(cfg.patterns["max_pattern_bars"])
        tl_tol = float(cfg.trendline["touch_tolerance_pct"])
        sc = cfg.scoring

        SH, SL = context.swing_highs, context.swing_lows
        close = df["close"].to_numpy(float)
        high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
        n = len(df)
        step = max(2, int(cfg.pivots["right_bars"]) + 1)
        out: List[PatternResult] = []

        for e in range(minb, n, step):
            s = max(0, e - maxb)
            sh = [i for i in SH if s <= i <= e]
            sl = [i for i in SL if s <= i <= e]
            if len(sh) < min_touch or len(sl) < min_touch:
                continue
            if (sh[-1] - sh[0]) < minb and (sl[-1] - sl[0]) < minb:
                continue
            up = fit_trendline([(i, high[i]) for i in sh], "least_squares", tl_tol)
            lo = fit_trendline([(i, low[i]) for i in sl], "least_squares", tl_tol)
            price = float(np.mean(close[s:e + 1])) or 1.0
            us, ls = up.slope / price, lo.slope / price
            max_spread = float(tri.get("flat_max_spread_pct", 0.02))
            if not _line_valid(up, us, [high[i] for i in sh], flat, min_r2, max_spread):
                continue
            if not _line_valid(lo, ls, [low[i] for i in sl], flat, min_r2, max_spread):
                continue
            w0 = up.y_at(s) - lo.y_at(s)
            w1 = up.y_at(e) - lo.y_at(e)
            if w0 <= 0 or w1 <= 0:
                continue
            converging = w1 < w0 * conv_r
            diverging = w1 > w0 * div_r

            ptype: Optional[str] = None
            want: Optional[Direction] = None
            if abs(us) < flat and ls > flat and converging:
                ptype, want = "ascending_triangle", Direction.LONG
            elif abs(ls) < flat and us < -flat and converging:
                ptype, want = "descending_triangle", Direction.SHORT
            elif us < -flat and ls > flat and converging:
                ptype, want = "symmetrical_triangle", None
            elif us > flat and ls < -flat and diverging:
                ptype, want = "expanding_triangle", None
            if ptype is None:
                continue

            brk = None; bdir = None
            for j in range(e + 1, min(n, e + maxb)):
                if close[j] > up.y_at(j):
                    brk, bdir = j, Direction.LONG; break
                if close[j] < lo.y_at(j):
                    brk, bdir = j, Direction.SHORT; break
            if brk is None:
                continue
            if want is not None and bdir != want:
                continue

            entry = float(close[brk])
            height = float(w0)
            if bdir == Direction.LONG:
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
                    "trend": trend_score(regime, "BULL" if bdir == Direction.LONG else "BEAR"),
                    "market_structure": structure_score(regime), "breakout": 75.0}
            conf = blend(subs, sc["weights"])
            if conf < float(sc["min_confidence_to_emit"]):
                continue
            out.append(PatternResult(
                symbol=symbol, timestamp=_ts(df, brk), pattern=ptype,
                direction=bdir, confidence=conf, entry=entry, stop_loss=stop_px,
                target=target, breakout_level=entry, volume_confirmation=vconf,
                market_structure=regime, breakout_confirmed=True,
                start_index=s, end_index=brk, sub_scores=subs,
                meta={"upper_slope": round(us, 6), "lower_slope": round(ls, 6),
                      "width0": round(w0, 1), "width1": round(w1, 1)}))
        return _dedup(out)
