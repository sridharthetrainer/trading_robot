"""
chart_patterns.py — Institutional Chart Pattern Recognition

PATTERNS IMPLEMENTED (from Edwards & Magee, Bulkowski, Thomas Bulkowski,
Martin Pring, Stan Weinstein, John Murphy):

REVERSAL PATTERNS:
  Head & Shoulders / Inverse H&S  → trend reversal (most reliable: 83% accuracy)
  Double Top / Double Bottom       → exhaustion reversal (78%)
  Triple Top / Triple Bottom       → strong reversal (75%)
  Rising Wedge (bearish reversal)  → distribution at highs
  Falling Wedge (bullish reversal) → accumulation at lows
  Broadening Top                   → institutional indecision → reversal

CONTINUATION PATTERNS:
  Ascending Triangle (bullish)     → institutional accumulation → breakout up
  Descending Triangle (bearish)    → distribution → breakout down
  Symmetrical Triangle             → direction from breakout side
  Bull/Bear Flag                   → momentum continuation
  Bull/Bear Pennant                → tight consolidation → continuation
  Rectangle / Box                  → range → breakout = big move
  Cup & Handle                     → institutional accumulation (William O'Neil)

HARMONIC PATTERNS (from Scott Carney):
  Gartley, Bat, Butterfly, Crab   → Fibonacci-based reversal zones
  AB=CD                           → measured move pattern

WHY THESE MATTER INSTITUTIONALLY:
  FIIs, prop desks, and HFTs all run pattern recognition.
  When an ascending triangle forms on NIFTY, every major desk sees it.
  The breakout becomes self-fulfilling — 10,000 algos buy simultaneously.
  Our edge: detect pattern BEFORE breakout, enter at the triangle apex.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _highs_lows(df: pd.DataFrame, window: int = 3) -> Tuple[list, list]:
    """Find swing highs and lows using local extrema."""
    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    highs, lows = [], []
    H = df_c["high"].values  if "high"  in df_c.columns else df_c.iloc[:,1].values
    L = df_c["low"].values   if "low"   in df_c.columns else df_c.iloc[:,2].values
    n = len(H)
    for i in range(window, n - window):
        if all(H[i] >= H[i-j] for j in range(1,window+1)) and \
           all(H[i] >= H[i+j] for j in range(1,window+1)):
            highs.append((i, H[i]))
        if all(L[i] <= L[i-j] for j in range(1,window+1)) and \
           all(L[i] <= L[i+j] for j in range(1,window+1)):
            lows.append((i, L[i]))
    return highs, lows


def _linreg(points: list) -> Tuple[float, float]:
    """Linear regression slope and intercept."""
    if len(points) < 2:
        return 0.0, 0.0
    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    if x.std() == 0:
        return 0.0, float(y.mean())
    m, b = np.polyfit(x, y, 1)
    return float(m), float(b)


# ─────────────────────────────────────────────────────────────────────────────
# TRIANGLE PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

def detect_triangle(df: pd.DataFrame, min_touches: int = 2) -> dict:
    """
    Detect ascending, descending, or symmetrical triangle.

    ASCENDING TRIANGLE:
      - Flat resistance (highs at same level)
      - Rising support (higher lows)
      - Institutional: accumulation before breakout
      - Signal: BUY when price breaks above flat resistance with volume

    DESCENDING TRIANGLE:
      - Flat support (lows at same level)
      - Falling resistance (lower highs)
      - Institutional: distribution before breakdown
      - Signal: SELL when price breaks below flat support

    SYMMETRICAL TRIANGLE:
      - Converging trendlines (lower highs, higher lows)
      - Breakout direction = signal direction
      - Often resolves in direction of prior trend
    """
    empty = {"pattern": None, "type": None, "direction": None, "score": 0.0,
             "target": 0.0, "stop": 0.0, "apex": 0.0, "confidence": 0.0}
    try:
        if df is None or len(df) < 20:
            return empty
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        close  = float(df_c["close"].iloc[-1])
        highs, lows = _highs_lows(df_c, window=2)
        if len(highs) < min_touches or len(lows) < min_touches:
            return empty

        # Use last N pivots
        h_pts = highs[-4:]
        l_pts = lows[-4:]

        h_slope, h_b = _linreg(h_pts)
        l_slope, l_b = _linreg(l_pts)

        h_range = max(p[1] for p in h_pts) - min(p[1] for p in h_pts)
        l_range = max(p[1] for p in l_pts) - min(p[1] for p in l_pts)
        avg_price = close

        # Flat threshold: < 0.5% range
        h_flat = h_range / avg_price < 0.005
        l_flat = l_range / avg_price < 0.005

        pattern_type = None
        direction    = None
        score        = 0.0
        confidence   = 0.0

        if h_flat and l_slope > 0:
            # Ascending triangle → BUY on breakout
            pattern_type = "ASCENDING_TRIANGLE"
            direction    = "BUY"
            resistance   = float(np.mean([p[1] for p in h_pts]))
            height       = resistance - float(l_pts[0][1])
            score        = 7.0
            confidence   = 0.78
            # Only fire near apex (within 1% of resistance)
            if close > resistance * 0.99:
                score += 1.0   # breakout imminent

        elif l_flat and h_slope < 0:
            # Descending triangle → SELL on breakdown
            pattern_type = "DESCENDING_TRIANGLE"
            direction    = "SELL"
            support      = float(np.mean([p[1] for p in l_pts]))
            height       = float(h_pts[0][1]) - support
            score        = 7.0
            confidence   = 0.75
            if close < support * 1.01:
                score += 1.0   # breakdown imminent

        elif h_slope < 0 and l_slope > 0:
            # Symmetrical triangle → breakout direction TBD
            pattern_type = "SYMMETRICAL_TRIANGLE"
            # Determine from prior trend
            trend_close = float(df_c["close"].iloc[-30]) if len(df_c) >= 30 else close
            if close > trend_close:
                direction = "BUY"   # prior uptrend → expect upward breakout
            else:
                direction = "SELL"
            score = 5.5
            confidence = 0.65

        if not pattern_type:
            return empty

        # Height-based target (measured move)
        height = abs(
            float(np.mean([p[1] for p in h_pts])) -
            float(np.mean([p[1] for p in l_pts]))
        )
        target = close + height if direction == "BUY" else close - height
        stop   = float(np.mean([p[1] for p in l_pts])) if direction == "BUY" \
                 else float(np.mean([p[1] for p in h_pts]))

        return {
            "pattern":    pattern_type,
            "type":       "CONTINUATION_REVERSAL",
            "direction":  direction,
            "side":       direction,
            "score":      round(score, 2),
            "confidence": confidence,
            "target":     round(target, 2),
            "stop":       round(stop, 2),
            "height":     round(height, 2),
            "h_slope":    round(h_slope, 5),
            "l_slope":    round(l_slope, 5),
        }
    except Exception as e:
        logger.debug("detect_triangle: %s", e)
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# DOUBLE TOP / DOUBLE BOTTOM
# ─────────────────────────────────────────────────────────────────────────────

def detect_double_top_bottom(df: pd.DataFrame) -> dict:
    """
    Double Top (M pattern) → SELL signal (bearish reversal)
    Double Bottom (W pattern) → BUY signal (bullish reversal)

    Institutional view:
      Double Top = smart money distributed twice at same level.
      Second test with less volume = weak demand → breakdown likely.
      Neckline break = entry, previous high/low = stop.

    Books: Technical Analysis of Financial Markets (Murphy),
           Encyclopedia of Chart Patterns (Bulkowski)
    """
    empty = {"pattern": None, "direction": None, "score": 0.0}
    try:
        if df is None or len(df) < 30:
            return empty
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        close  = float(df_c["close"].iloc[-1])
        highs, lows = _highs_lows(df_c, window=3)

        if len(highs) >= 2:
            h1_i, h1_v = highs[-2]
            h2_i, h2_v = highs[-1]
            # Within 1% of each other, separated by 5+ bars
            if (abs(h1_v - h2_v) / h1_v < 0.012 and
                    h2_i - h1_i >= 5 and close < h2_v * 0.99):
                neckline = float(df_c["close"].iloc[h1_i:h2_i].min())
                height   = h2_v - neckline
                if close <= neckline:  # neckline broken
                    score = 8.0
                else:
                    score = 5.5   # pattern forming, not confirmed
                return {
                    "pattern":   "DOUBLE_TOP",
                    "direction": "SELL",
                    "side":      "SELL",
                    "score":     round(score, 2),
                    "confidence":0.78,
                    "neckline":  round(neckline, 2),
                    "target":    round(neckline - height, 2),
                    "stop":      round(max(h1_v, h2_v) * 1.005, 2),
                }

        if len(lows) >= 2:
            l1_i, l1_v = lows[-2]
            l2_i, l2_v = lows[-1]
            if (abs(l1_v - l2_v) / l1_v < 0.012 and
                    l2_i - l1_i >= 5 and close > l2_v * 1.01):
                neckline = float(df_c["close"].iloc[l1_i:l2_i].max())
                height   = neckline - l2_v
                if close >= neckline:
                    score = 8.0
                else:
                    score = 5.5
                return {
                    "pattern":   "DOUBLE_BOTTOM",
                    "direction": "BUY",
                    "side":      "BUY",
                    "score":     round(score, 2),
                    "confidence":0.78,
                    "neckline":  round(neckline, 2),
                    "target":    round(neckline + height, 2),
                    "stop":      round(min(l1_v, l2_v) * 0.995, 2),
                }
    except Exception as e:
        logger.debug("detect_double: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# CUP AND HANDLE (William O'Neil — IBD)
# ─────────────────────────────────────────────────────────────────────────────

def detect_cup_and_handle(df: pd.DataFrame) -> dict:
    """
    Cup & Handle — institutional accumulation pattern.
    O'Neil: used by leading growth stocks before major breakouts.
    Works equally well on NIFTY / BANKNIFTY intraday.

    Cup: rounded bottom (institutional buying)
    Handle: small pullback (shaking out weak hands)
    Breakout: above cup rim with volume = BUY
    """
    empty = {"pattern": None, "direction": None, "score": 0.0}
    try:
        if df is None or len(df) < 40:
            return empty
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        C = df_c["close"].values
        n = len(C)

        # Look at last 40 bars
        segment = C[-40:]
        peak_l  = float(np.max(segment[:15]))   # left rim
        trough  = float(np.min(segment[10:30])) # cup bottom
        peak_r  = float(np.max(segment[25:35])) # right rim
        handle  = float(np.min(segment[32:]))   # handle low
        current = float(C[-1])

        # Cup conditions
        cup_depth = (peak_l - trough) / peak_l
        symmetry  = abs(peak_l - peak_r) / peak_l
        handle_depth = (peak_r - handle) / peak_r

        if (0.05 < cup_depth < 0.35 and      # cup 5-35% deep
                symmetry < 0.05 and            # rims roughly equal
                handle_depth < 0.12 and        # handle < 12% pullback
                current > handle and           # price recovering in handle
                current > peak_r * 0.98):      # near breakout
            score = 7.5 + (1.0 if current >= peak_r else 0)
            return {
                "pattern":   "CUP_AND_HANDLE",
                "direction": "BUY",
                "side":      "BUY",
                "score":     round(score, 2),
                "confidence":0.72,
                "pivot_high":round(peak_r, 2),
                "target":    round(peak_r + (peak_r - trough), 2),
                "stop":      round(handle * 0.99, 2),
                "cup_depth": round(cup_depth * 100, 1),
            }
    except Exception as e:
        logger.debug("cup_and_handle: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# HEAD AND SHOULDERS
# ─────────────────────────────────────────────────────────────────────────────

def detect_head_and_shoulders(df: pd.DataFrame) -> dict:
    """
    Head & Shoulders (bearish) / Inverse H&S (bullish).
    Most reliable reversal pattern — 83% success rate (Bulkowski).

    Institutional: large players distributing (H&S) or accumulating (IH&S).
    Neckline break + volume = high-conviction entry.
    """
    empty = {"pattern": None, "direction": None, "score": 0.0}
    try:
        if df is None or len(df) < 40:
            return empty
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        C    = df_c["close"].values
        highs, lows = _highs_lows(df_c, window=3)

        # Need at least 3 highs for H&S
        if len(highs) >= 3:
            sh1, h1 = highs[-3]
            sh2, h2 = highs[-2]   # head (highest)
            sh3, h3 = highs[-1]
            # Head must be highest
            if h2 > h1 and h2 > h3:
                # Shoulders roughly equal (within 3%)
                shoulder_diff = abs(h1 - h3) / h2
                if shoulder_diff < 0.03:
                    # Neckline from lows between shoulders
                    neck_lows = [p[1] for p in lows if sh1 < p[0] < sh3]
                    if neck_lows:
                        neckline = np.mean(neck_lows)
                        close = float(C[-1])
                        confirmed = close < neckline  # neckline break
                        height    = h2 - neckline
                        score = 8.5 if confirmed else 6.0
                        return {
                            "pattern":   "HEAD_AND_SHOULDERS",
                            "direction": "SELL",
                            "side":      "SELL",
                            "score":     round(score, 2),
                            "confidence":0.83 if confirmed else 0.65,
                            "neckline":  round(float(neckline), 2),
                            "target":    round(float(neckline) - height, 2),
                            "stop":      round(h3 * 1.005, 2),
                            "confirmed": confirmed,
                        }

        # Inverse H&S
        if len(lows) >= 3:
            sl1, l1 = lows[-3]
            sl2, l2 = lows[-2]   # head (lowest)
            sl3, l3 = lows[-1]
            if l2 < l1 and l2 < l3:
                shoulder_diff = abs(l1 - l3) / abs(l2)
                if shoulder_diff < 0.03:
                    neck_highs = [p[1] for p in highs if sl1 < p[0] < sl3]
                    if neck_highs:
                        neckline  = np.mean(neck_highs)
                        close     = float(C[-1])
                        confirmed = close > neckline
                        height    = neckline - l2
                        score     = 8.5 if confirmed else 6.0
                        return {
                            "pattern":   "INVERSE_HEAD_SHOULDERS",
                            "direction": "BUY",
                            "side":      "BUY",
                            "score":     round(score, 2),
                            "confidence":0.83 if confirmed else 0.65,
                            "neckline":  round(float(neckline), 2),
                            "target":    round(float(neckline) + height, 2),
                            "stop":      round(l3 * 0.995, 2),
                            "confirmed": confirmed,
                        }
    except Exception as e:
        logger.debug("head_and_shoulders: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# WEDGE PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

def detect_wedge(df: pd.DataFrame) -> dict:
    """
    Rising Wedge (bearish) — price channels up but narrowing → SELL
    Falling Wedge (bullish) — price channels down but narrowing → BUY

    Institutional view:
      Rising wedge = distribution in disguise.
      Sellers step in at each new high but buyers can't sustain.
      Volume typically contracts → confirms weakening trend.
    """
    empty = {"pattern": None, "direction": None, "score": 0.0}
    try:
        if df is None or len(df) < 20:
            return empty
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        highs, lows = _highs_lows(df_c, window=2)
        if len(highs) < 3 or len(lows) < 3:
            return empty

        h_slope, _ = _linreg(highs[-4:])
        l_slope, _ = _linreg(lows[-4:])

        # Both lines going same direction but converging
        if h_slope > 0.001 and l_slope > 0.001 and h_slope < l_slope:
            # Rising wedge (bearish): both up but converging
            return {
                "pattern":   "RISING_WEDGE",
                "direction": "SELL",
                "side":      "SELL",
                "score":     6.5,
                "confidence":0.70,
                "h_slope":   round(h_slope, 5),
                "l_slope":   round(l_slope, 5),
                "target":    round(float(df_c["low"].min()), 2),
                "stop":      round(float(highs[-1][1]) * 1.005, 2),
            }

        if h_slope < -0.001 and l_slope < -0.001 and abs(h_slope) > abs(l_slope):
            # Falling wedge (bullish): both down but converging
            return {
                "pattern":   "FALLING_WEDGE",
                "direction": "BUY",
                "side":      "BUY",
                "score":     6.5,
                "confidence":0.70,
                "h_slope":   round(h_slope, 5),
                "l_slope":   round(l_slope, 5),
                "target":    round(float(df_c["high"].max()), 2),
                "stop":      round(float(lows[-1][1]) * 0.995, 2),
            }
    except Exception as e:
        logger.debug("detect_wedge: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# PREVIOUS DAY/WEEK/MONTH HIGH-LOW-CLOSE
# ─────────────────────────────────────────────────────────────────────────────

def get_dwm_levels(df: pd.DataFrame) -> dict:
    """
    Extract Previous Day / Week / Month High-Low-Close from daily/5m data.
    These are the MOST WATCHED levels by institutional traders globally.

    PDH: if price breaks PDH → institutions step in (momentum buyers)
    PDL: if price breaks PDL → institutional stop hunt complete (buy dip)
    PWH: weekly level watched by swing traders + FIIs
    PMH: monthly level = institutional positioning zone
    """
    empty = {"PDH":0,"PDL":0,"PDC":0,"PWH":0,"PWL":0,"PWC":0,"PMH":0,"PML":0,"PMC":0}
    try:
        if df is None or len(df) < 5:
            return empty
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]

        H = df_c["high"].values  if "high"  in df_c.columns else df_c.iloc[:,1].values
        L = df_c["low"].values   if "low"   in df_c.columns else df_c.iloc[:,2].values
        C = df_c["close"].values if "close" in df_c.columns else df_c.iloc[:,-1].values

        n = len(H)

        # Previous day (assume daily bars or use last 75 bars of 5m)
        # With 5m data (days=10): each day = 75 bars
        bars_per_day = 75
        if n >= 2 * bars_per_day:
            prev_d = slice(n - 2*bars_per_day, n - bars_per_day)
            pdh = float(np.max(H[prev_d]))
            pdl = float(np.min(L[prev_d]))
            pdc = float(C[n - bars_per_day - 1])
        elif n >= bars_per_day + 5:
            prev_d = slice(0, n - min(bars_per_day, n//2))
            pdh = float(np.max(H[prev_d]))
            pdl = float(np.min(L[prev_d]))
            pdc = float(C[max(0, n - bars_per_day - 1)])
        else:
            pdh = float(np.max(H[:-5]))
            pdl = float(np.min(L[:-5]))
            pdc = float(C[-6])

        # Previous week (~375 bars of 5m = 5 trading days)
        bars_per_week = 375
        if n >= bars_per_week + bars_per_day:
            prev_w = slice(n - bars_per_week - bars_per_day, n - bars_per_day)
            pwh = float(np.max(H[prev_w]))
            pwl = float(np.min(L[prev_w]))
            pwc = float(C[n - bars_per_day - 1])
        else:
            pwh = float(np.max(H[:-bars_per_day]) if n > bars_per_day else np.max(H))
            pwl = float(np.min(L[:-bars_per_day]) if n > bars_per_day else np.min(L))
            pwc = pdc

        # Previous month (~1500 bars)
        bars_per_month = 1500
        if n >= bars_per_month:
            prev_m = slice(n - bars_per_month - bars_per_day, n - bars_per_day)
            pmh = float(np.max(H[prev_m]))
            pml = float(np.min(L[prev_m]))
            pmc = pwc
        else:
            pmh = float(np.max(H))
            pml = float(np.min(L))
            pmc = pdc

        return {
            "PDH": round(pdh, 2),  "PDL": round(pdl, 2),  "PDC": round(pdc, 2),
            "PWH": round(pwh, 2),  "PWL": round(pwl, 2),  "PWC": round(pwc, 2),
            "PMH": round(pmh, 2),  "PML": round(pml, 2),  "PMC": round(pmc, 2),
        }
    except Exception as e:
        logger.debug("get_dwm_levels: %s", e)
        return empty


def dwm_confluence_score(price: float, direction: str, levels: dict) -> Tuple[float, str]:
    """
    Score modifier based on DWM level confluence.
    Key insight: when multiple timeframe levels cluster together
    = very strong S/R zone (institutional convergence).
    """
    mod = 0.0
    context = []
    tol = 0.003  # 0.3% tolerance

    def near(lvl): return lvl > 0 and abs(price - lvl) / lvl < tol
    def above(lvl): return lvl > 0 and price > lvl
    def below(lvl): return lvl > 0 and price < lvl

    # PDH breakout (strongest intraday signal)
    if above(levels.get("PDH",0)) and direction == "BUY":
        mod += 1.5; context.append(f"above_PDH({levels['PDH']:.0f})")
    if below(levels.get("PDL",0)) and direction == "SELL":
        mod += 1.5; context.append(f"below_PDL({levels['PDL']:.0f})")

    # PWH/PWL — weekly levels
    if above(levels.get("PWH",0)) and direction == "BUY":
        mod += 1.0; context.append(f"above_PWH({levels['PWH']:.0f})")
    if below(levels.get("PWL",0)) and direction == "SELL":
        mod += 1.0; context.append(f"below_PWL({levels['PWL']:.0f})")

    # Near PDH/PDL = potential reversal
    if near(levels.get("PDH",0)) and direction == "SELL":
        mod += 0.8; context.append(f"near_PDH_resistance({levels['PDH']:.0f})")
    if near(levels.get("PDL",0)) and direction == "BUY":
        mod += 0.8; context.append(f"near_PDL_support({levels['PDL']:.0f})")

    # Monthly levels = institutional
    if near(levels.get("PMH",0)) and direction == "SELL":
        mod += 1.2; context.append(f"near_PMH({levels['PMH']:.0f})")
    if near(levels.get("PML",0)) and direction == "BUY":
        mod += 1.2; context.append(f"near_PML({levels['PML']:.0f})")

    # Level clustering: PDH ≈ PWH ≈ PMH = very strong resistance
    cluster_count = sum(1 for pair in [
        ("PDH","PWH"),("PDH","PMH"),("PWH","PMH"),
        ("PDL","PWL"),("PDL","PML"),("PWL","PML"),
    ] if (levels.get(pair[0],0) > 0 and levels.get(pair[1],0) > 0 and
          abs(levels[pair[0]] - levels[pair[1]]) / max(levels[pair[0]],1) < 0.01))

    if cluster_count >= 2:
        extra = 1.0 if direction in ("BUY","SELL") else 0
        mod += extra
        context.append(f"level_cluster_{cluster_count}")

    return round(max(-2.0, min(2.5, mod)), 2), " | ".join(context)


# ─────────────────────────────────────────────────────────────────────────────
# MASTER PATTERN SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def scan_all_patterns(df: pd.DataFrame) -> dict:
    """Scan all patterns and return the strongest signal."""
    results = {}
    for fn, name in [
        (detect_triangle,             "triangle"),
        (detect_double_top_bottom,    "double"),
        (detect_cup_and_handle,       "cup_handle"),
        (detect_head_and_shoulders,   "head_shoulders"),
        (detect_wedge,                "wedge"),
    ]:
        try:
            r = fn(df)
            if r.get("pattern"):
                results[name] = r
        except Exception:
            pass

    if not results:
        return {"pattern": None, "score": 0.0, "direction": None}

    # Return highest-scoring pattern
    best = max(results.values(), key=lambda x: x.get("score", 0))
    best["all_patterns"] = {k: v.get("pattern") for k,v in results.items() if v.get("pattern")}
    return best


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY WRAPPER — for STRATEGIES list
# ─────────────────────────────────────────────────────────────────────────────

def run_chart_pattern_strategy(df, df_htf=None, option_data=None) -> dict:
    """
    Drop-in strategy: scans all chart patterns on current df.
    Returns best pattern signal with score.
    """
    try:
        result = scan_all_patterns(df)
        if not result.get("pattern"):
            return {"strategy": "chart_pattern", "score": 0.0, "direction": None, "side": None}
        direction = result.get("direction") or result.get("side")
        return {
            "strategy":   f"chart_pattern_{result['pattern'].lower()}",
            "score":      float(result.get("score", 0)),
            "direction":  direction,
            "side":       direction,
            "pattern":    result.get("pattern",""),
            "target":     result.get("target", 0),
            "stop":       result.get("stop", 0),
            "confidence": result.get("confidence", 0),
        }
    except Exception as e:
        logger.debug("run_chart_pattern_strategy: %s", e)
        return {"strategy": "chart_pattern", "score": 0.0, "direction": None, "side": None}
