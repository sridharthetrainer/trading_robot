"""
price_structure.py — Previous Day/Week/Month High-Low-Close + Chart Patterns

INSTITUTIONAL S/R LEVELS (watched by EVERY serious trader):
  PDH = Previous Day High     → first resistance on breakout
  PDL = Previous Day Low      → first support on breakdown  
  PDC = Previous Day Close    → psychological pivot
  PWH = Previous Week High    → major resistance / breakout target
  PWL = Previous Week Low     → major support
  PMH = Previous Month High   → institutional resistance
  PML = Previous Month Low    → institutional support

CHART PATTERNS (from Murphy, Minervini, O'Neil, Wyckoff):
  Breakout patterns (direction continuation):
    Ascending Triangle  → flat top + rising lows → breakout UP
    Descending Triangle → flat bottom + falling highs → breakout DOWN
    Symmetrical Triangle → converging → direction uncertain → wait for break
    Bull Flag / Bear Flag → sharp move + tight consolidation → continuation
    VCP (Volatility Contraction) → Minervini: tightest before breakout
    Cup & Handle → O'Neil: base + handle → breakout from handle
    Rectangle → trading range breakout

  Reversal patterns:
    Double Top / Double Bottom → failed 2nd test = reversal
    Head & Shoulders → L shoulder + Head + R shoulder → neckline break
    Wyckoff Spring → false breakdown below support = institutional buy
    UTAD (Upthrust After Distribution) → false breakout = institutional sell
    
  Continuation with caution:
    Pennant → triangle after sharp move
    Rising/Falling Wedge → wedge against trend = reversal likely
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


# ── Previous Day/Week/Month Levels ────────────────────────────────────────────

def get_pdh_pdl_pdc(df: pd.DataFrame) -> Dict[str, float]:
    """
    Extract Previous Day High, Low, Close from intraday 5-min data.
    Also computes: PDC as first S/R, PDH-PDL range (prior day volatility).
    """
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if "close" not in df_c.columns:
            return {}

        # Get today's date from index
        if hasattr(df_c.index, 'date'):
            today    = df_c.index[-1].date()
            prev_day = [d for d in set(df_c.index.date) if d < today]
            if not prev_day:
                return {}
            prev_day    = max(prev_day)
            prev_bars   = df_c[df_c.index.date == prev_day]
        else:
            # Fallback: treat first 75 bars as prev day, rest as today
            split = max(1, len(df_c) - 75)
            prev_bars = df_c.iloc[:split]

        if len(prev_bars) < 5:
            return {}

        pdh = float(prev_bars["high"].max()  if "high"  in prev_bars.columns else prev_bars["close"].max())
        pdl = float(prev_bars["low"].min()   if "low"   in prev_bars.columns else prev_bars["close"].min())
        pdc = float(prev_bars["close"].iloc[-1])
        pdr = pdh - pdl   # previous day range

        return {
            "PDH": round(pdh, 2),
            "PDL": round(pdl, 2),
            "PDC": round(pdc, 2),
            "PDR": round(pdr, 2),  # range
            "PDH_PDC": round(pdh - pdc, 2),
            "PDL_PDC": round(pdc - pdl, 2),
        }
    except Exception as e:
        logger.debug("get_pdh_pdl_pdc: %s", e)
        return {}


def get_weekly_monthly_levels(df_daily: pd.DataFrame) -> Dict[str, float]:
    """
    Extract Previous Week High/Low and Previous Month High/Low from daily data.
    df_daily: DataFrame with daily OHLCV.
    """
    result = {}
    try:
        df_c = df_daily.copy()
        df_c.columns = [c.lower() for c in df_c.columns]

        if "close" not in df_c.columns or len(df_c) < 10:
            return result

        # Weekly levels (last full 5-day week)
        df_c.index = pd.to_datetime(df_c.index)
        df_c["week"] = df_c.index.isocalendar().week.astype(int)
        current_week = int(df_c["week"].iloc[-1])
        prev_week_bars = df_c[df_c["week"] == current_week - 1]

        if len(prev_week_bars) >= 3:
            result["PWH"] = round(float(prev_week_bars["high"].max()  if "high" in df_c.columns else prev_week_bars["close"].max()), 2)
            result["PWL"] = round(float(prev_week_bars["low"].min()   if "low"  in df_c.columns else prev_week_bars["close"].min()), 2)
            result["PWC"] = round(float(prev_week_bars["close"].iloc[-1]), 2)
            result["PWR"] = round(result["PWH"] - result["PWL"], 2)

        # Monthly levels (last full month)
        df_c["month"] = df_c.index.month
        current_month = int(df_c["month"].iloc[-1])
        prev_month = current_month - 1 if current_month > 1 else 12
        prev_month_bars = df_c[df_c["month"] == prev_month]

        if len(prev_month_bars) >= 10:
            result["PMH"] = round(float(prev_month_bars["high"].max()  if "high" in df_c.columns else prev_month_bars["close"].max()), 2)
            result["PML"] = round(float(prev_month_bars["low"].min()   if "low"  in df_c.columns else prev_month_bars["close"].min()), 2)
            result["PMC"] = round(float(prev_month_bars["close"].iloc[-1]), 2)
            result["PMR"] = round(result["PMH"] - result["PML"], 2)

    except Exception as e:
        logger.debug("get_weekly_monthly_levels: %s", e)
    return result


def score_vs_key_levels(
    price:     float,
    direction: str,
    pdh:  float = 0, pdl:  float = 0, pdc:  float = 0,
    pwh:  float = 0, pwl:  float = 0,
    pmh:  float = 0, pml:  float = 0,
) -> Tuple[float, List[str]]:
    """
    Score a signal based on proximity to key S/R levels.
    Returns (score_modifier, list_of_context_strings).

    Logic (from Pivot Boss + Murphy):
      Breaking above PDH with BUY  = +2.0 (strongest intraday signal)
      Breaking above PWH with BUY  = +2.5 (weekly breakout)
      Breaking above PMH with BUY  = +3.0 (monthly breakout = institutional)
      Near PDH as resistance for BUY = -1.0
      Bouncing off PDL with BUY    = +1.5 (support held)
      Bouncing off PWL with BUY    = +2.0 (weekly support)
    """
    mod     = 0.0
    context = []
    tol     = 0.002  # 0.2% proximity

    def near(lvl):   return lvl > 0 and abs(price - lvl) / lvl < tol
    def above(lvl):  return lvl > 0 and price > lvl
    def below(lvl):  return lvl > 0 and price < lvl

    is_buy  = direction.upper() == "BUY"
    is_sell = not is_buy

    # ── Daily levels ──────────────────────────────────────────────────────────
    if pdh > 0:
        if above(pdh) and is_buy:
            mod += 2.0; context.append(f"↑PDH({pdh:.0f})_breakout")
        elif near(pdh) and is_buy:
            mod -= 1.0; context.append(f"≈PDH({pdh:.0f})_resistance")
        elif near(pdh) and is_sell:
            mod += 1.0; context.append(f"≈PDH({pdh:.0f})_short")

    if pdl > 0:
        if below(pdl) and is_sell:
            mod += 2.0; context.append(f"↓PDL({pdl:.0f})_breakdown")
        elif near(pdl) and is_buy:
            mod += 1.5; context.append(f"≈PDL({pdl:.0f})_support")
        elif near(pdl) and is_sell:
            mod -= 1.0; context.append(f"≈PDL({pdl:.0f})_support")

    if pdc > 0:
        if above(pdc) and is_buy:
            mod += 0.5; context.append(f"above_PDC({pdc:.0f})")
        elif below(pdc) and is_sell:
            mod += 0.5; context.append(f"below_PDC({pdc:.0f})")

    # ── Weekly levels ─────────────────────────────────────────────────────────
    if pwh > 0:
        if above(pwh) and is_buy:
            mod += 2.5; context.append(f"↑PWH({pwh:.0f})_weekly_breakout")
        elif near(pwh) and is_buy:
            mod -= 1.2; context.append(f"≈PWH({pwh:.0f})_weekly_resistance")
        elif near(pwh) and is_sell:
            mod += 1.5; context.append(f"≈PWH({pwh:.0f})_weekly_short")

    if pwl > 0:
        if below(pwl) and is_sell:
            mod += 2.5; context.append(f"↓PWL({pwl:.0f})_weekly_breakdown")
        elif near(pwl) and is_buy:
            mod += 2.0; context.append(f"≈PWL({pwl:.0f})_weekly_support")

    # ── Monthly levels ────────────────────────────────────────────────────────
    if pmh > 0:
        if above(pmh) and is_buy:
            mod += 3.0; context.append(f"↑PMH({pmh:.0f})_MONTHLY_BREAKOUT")
        elif near(pmh) and is_buy:
            mod -= 1.5; context.append(f"≈PMH({pmh:.0f})_monthly_resistance")

    if pml > 0:
        if below(pml) and is_sell:
            mod += 3.0; context.append(f"↓PML({pml:.0f})_MONTHLY_BREAKDOWN")
        elif near(pml) and is_buy:
            mod += 2.5; context.append(f"≈PML({pml:.0f})_monthly_support")

    # Confluence bonus: multiple timeframe levels agree
    n_agree_levels = sum(1 for s in context if "breakout" in s or "breakdown" in s or "support" in s)
    if n_agree_levels >= 2:
        mod += 1.0; context.append(f"multi_tf_level_confluence({n_agree_levels})")

    return round(max(-3.0, min(4.0, mod)), 2), context


# ── Chart Pattern Detection ───────────────────────────────────────────────────

def detect_patterns(df: pd.DataFrame, lookback: int = 60) -> Dict[str, dict]:
    """
    Detect chart patterns in the last `lookback` bars.

    Returns dict of detected patterns:
      {pattern_name: {detected, direction, score, target, stop, maturity}}

    Patterns implemented:
      triangle_asc, triangle_desc, triangle_sym
      double_top, double_bottom
      bull_flag, bear_flag
      rectangle, pennant
      vcp (Volatility Contraction Pattern — Minervini)
      wyckoff_spring, wyckoff_utad
      head_shoulders (simplified)
    """
    results = {}
    try:
        df_c  = df.tail(lookback).copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if "close" not in df_c.columns or len(df_c) < 20:
            return results

        highs  = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        lows   = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        closes = df_c["close"].values
        vols   = df_c["volume"].values if "volume" in df_c.columns else np.ones(len(closes))
        n      = len(closes)

        # ── TRIANGLE PATTERNS ─────────────────────────────────────────────────
        results["triangle"] = _detect_triangle(highs, lows, closes, vols)

        # ── DOUBLE TOP / DOUBLE BOTTOM ────────────────────────────────────────
        results["double_top"]    = _detect_double_top(highs, closes)
        results["double_bottom"] = _detect_double_bottom(lows, closes)

        # ── FLAG / PENNANT ────────────────────────────────────────────────────
        results["bull_flag"]  = _detect_flag(highs, lows, closes, vols, "bull")
        results["bear_flag"]  = _detect_flag(highs, lows, closes, vols, "bear")

        # ── RECTANGLE (Trading Range) ─────────────────────────────────────────
        results["rectangle"] = _detect_rectangle(highs, lows, closes, vols)

        # ── VCP — Minervini Volatility Contraction Pattern ────────────────────
        results["vcp"] = _detect_vcp(highs, lows, closes, vols)

        # ── WYCKOFF SPRING (false breakdown → reversal buy) ───────────────────
        results["wyckoff_spring"] = _detect_wyckoff_spring(highs, lows, closes, vols)

        # ── WYCKOFF UTAD (false breakout → reversal sell) ─────────────────────
        results["wyckoff_utad"] = _detect_wyckoff_utad(highs, lows, closes, vols)

        # ── HEAD & SHOULDERS (simplified) ─────────────────────────────────────
        results["head_shoulders"] = _detect_head_shoulders(highs, closes)

        # ── FAIR VALUE GAP (ICT concept) ──────────────────────────────────────
        results["fvg"] = _detect_fvg(highs, lows, closes)

    except Exception as e:
        logger.debug("detect_patterns: %s", e)
    return results


# ── Pattern Detection Helpers ─────────────────────────────────────────────────

def _detect_triangle(highs, lows, closes, vols) -> dict:
    n    = len(closes)
    if n < 15:
        return {"detected": False}
    # Find swing highs and lows
    sh = [i for i in range(2, n-2) if highs[i] > highs[i-1] and highs[i] > highs[i+1]]
    sl = [i for i in range(2, n-2) if lows[i]  < lows[i-1]  and lows[i]  < lows[i+1]]
    if len(sh) < 2 or len(sl) < 2:
        return {"detected": False}

    # Slope of recent swing highs and lows
    h_slope = (highs[sh[-1]] - highs[sh[-2]]) / max(sh[-1] - sh[-2], 1)
    l_slope = (lows[sl[-1]]  - lows[sl[-2]])  / max(sl[-1] - sl[-2], 1)

    # Descending triangle: flat bottom + falling highs
    if h_slope < -0.05 and abs(l_slope) < 0.02:
        target = float(lows[sl[-1]] - (highs[sh[-2]] - lows[sl[-2]]))
        return {"detected": True, "type": "descending", "direction": "SELL",
                "score": 4.5, "target": round(target,2), "maturity": "high"}

    # Ascending triangle: flat top + rising lows
    if l_slope > 0.05 and abs(h_slope) < 0.02:
        target = float(highs[sh[-1]] + (highs[sh[-2]] - lows[sl[-2]]))
        return {"detected": True, "type": "ascending", "direction": "BUY",
                "score": 4.5, "target": round(target,2), "maturity": "high"}

    # Symmetrical triangle: converging
    if h_slope < -0.02 and l_slope > 0.02:
        return {"detected": True, "type": "symmetrical", "direction": None,
                "score": 3.0, "target": 0, "maturity": "medium",
                "note": "wait_for_breakout_direction"}

    return {"detected": False}


def _detect_double_top(highs, closes) -> dict:
    n = len(closes)
    if n < 20:
        return {"detected": False}
    # Find two peaks of similar height
    window = min(n, 40)
    h = highs[-window:]
    peaks = [i for i in range(1, len(h)-1) if h[i] > h[i-1] and h[i] > h[i+1]]
    if len(peaks) < 2:
        return {"detected": False}
    p1, p2 = peaks[-2], peaks[-1]
    if abs(h[p1] - h[p2]) / h[p1] < 0.02:  # within 2%
        neckline = min(closes[(-window + p1):(-window + p2 + 1)])
        if closes[-1] < neckline:  # neckline broken
            target = float(neckline - (h[p2] - neckline))
            return {"detected": True, "direction": "SELL", "score": 5.0,
                    "target": round(target,2), "neckline": round(float(neckline),2),
                    "peak": round(float(h[p2]),2)}
    return {"detected": False}


def _detect_double_bottom(lows, closes) -> dict:
    n = len(closes)
    if n < 20:
        return {"detected": False}
    window = min(n, 40)
    l = lows[-window:]
    troughs = [i for i in range(1, len(l)-1) if l[i] < l[i-1] and l[i] < l[i+1]]
    if len(troughs) < 2:
        return {"detected": False}
    t1, t2 = troughs[-2], troughs[-1]
    if abs(l[t1] - l[t2]) / l[t1] < 0.02:
        neckline = max(closes[(-window + t1):(-window + t2 + 1)])
        if closes[-1] > neckline:
            target = float(neckline + (neckline - l[t2]))
            return {"detected": True, "direction": "BUY", "score": 5.0,
                    "target": round(target,2), "neckline": round(float(neckline),2),
                    "trough": round(float(l[t2]),2)}
    return {"detected": False}


def _detect_flag(highs, lows, closes, vols, flag_type: str) -> dict:
    n = len(closes)
    if n < 15:
        return {"detected": False}
    # Pole: sharp move in last 5-8 bars
    pole_bars = 8
    pole_move = closes[-1] - closes[-pole_bars]
    pole_pct  = abs(pole_move) / closes[-pole_bars] * 100

    if pole_pct < 1.5:  # need at least 1.5% move for the pole
        return {"detected": False}

    # Flag: tight consolidation (low ATR in recent bars vs pole)
    recent = closes[-5:]
    flag_range = (max(recent) - min(recent)) / min(recent) * 100

    if flag_range < 0.5:  # tight consolidation
        if flag_type == "bull" and pole_move > 0:
            return {"detected": True, "direction": "BUY", "score": 4.0,
                    "pole_pct": round(pole_pct,1), "flag_range": round(flag_range,2)}
        if flag_type == "bear" and pole_move < 0:
            return {"detected": True, "direction": "SELL", "score": 4.0,
                    "pole_pct": round(pole_pct,1), "flag_range": round(flag_range,2)}

    return {"detected": False}


def _detect_rectangle(highs, lows, closes, vols) -> dict:
    n = len(closes)
    if n < 20:
        return {"detected": False}
    window = min(n, 30)
    h = highs[-window:]
    l = lows[-window:]
    top  = float(np.percentile(h, 90))
    bot  = float(np.percentile(l, 10))
    rng  = (top - bot) / bot * 100

    if rng < 3.0:  # tight range
        # Price at top: sell setup; at bottom: buy setup
        close = float(closes[-1])
        if close > top * 0.998:
            return {"detected": True, "direction": "SELL", "score": 3.5,
                    "resistance": round(top,2), "support": round(bot,2), "note": "at_resistance"}
        if close < bot * 1.002:
            return {"detected": True, "direction": "BUY", "score": 3.5,
                    "resistance": round(top,2), "support": round(bot,2), "note": "at_support"}
        # Breakout from rectangle
        if close > top * 1.003:
            return {"detected": True, "direction": "BUY", "score": 4.5,
                    "resistance": round(top,2), "note": "rectangle_breakout"}
        if close < bot * 0.997:
            return {"detected": True, "direction": "SELL", "score": 4.5,
                    "support": round(bot,2), "note": "rectangle_breakdown"}

    return {"detected": False}


def _detect_vcp(highs, lows, closes, vols) -> dict:
    """Minervini VCP: 3 contractions, each tighter than last, on declining volume."""
    n = len(closes)
    if n < 30:
        return {"detected": False}
    # Calculate volatility in 3 segments
    seg = n // 3
    vol1 = np.std(closes[:seg])
    vol2 = np.std(closes[seg:2*seg])
    vol3 = np.std(closes[2*seg:])

    # VCP: each contraction tighter
    if vol1 > vol2 > vol3 and vol3 < vol1 * 0.5:
        # Volume should also decline
        avg_vol1 = np.mean(vols[:seg])
        avg_vol3 = np.mean(vols[2*seg:])
        if avg_vol3 < avg_vol1 * 0.8:  # declining volume
            return {"detected": True, "direction": "BUY",
                    "score": 5.5, "note": "minervini_vcp",
                    "contractions": 3, "vol_decline": round(avg_vol3/avg_vol1,2)}

    return {"detected": False}


def _detect_wyckoff_spring(highs, lows, closes, vols) -> dict:
    """
    Wyckoff Spring: false break below support + immediate recovery.
    Best BUY signal in institutional playbook.
    """
    n = len(closes)
    if n < 15:
        return {"detected": False}
    # Support = recent 20-bar low
    support = float(np.min(lows[-20:-3]))
    last_low = float(lows[-3])
    close    = float(closes[-1])

    # Spring: wick below support but closed above
    if last_low < support * 0.998 and close > support:
        # Volume spike on the spring bar (institutions absorbing)
        spring_vol = float(vols[-3])
        avg_vol    = float(np.mean(vols[-20:-3]))
        if spring_vol > avg_vol * 1.3:
            return {"detected": True, "direction": "BUY", "score": 6.0,
                    "support": round(support,2), "spring_low": round(last_low,2),
                    "note": "wyckoff_spring_institutional_buy",
                    "vol_ratio": round(spring_vol/avg_vol,2)}

    return {"detected": False}


def _detect_wyckoff_utad(highs, lows, closes, vols) -> dict:
    """UTAD: false breakout above resistance + immediate reversal → SELL."""
    n = len(closes)
    if n < 15:
        return {"detected": False}
    resistance = float(np.max(highs[-20:-3]))
    last_high  = float(highs[-3])
    close      = float(closes[-1])

    if last_high > resistance * 1.002 and close < resistance:
        utad_vol = float(vols[-3])
        avg_vol  = float(np.mean(vols[-20:-3]))
        if utad_vol > avg_vol * 1.3:
            return {"detected": True, "direction": "SELL", "score": 6.0,
                    "resistance": round(resistance,2), "utad_high": round(last_high,2),
                    "note": "wyckoff_utad_institutional_sell",
                    "vol_ratio": round(utad_vol/avg_vol,2)}

    return {"detected": False}


def _detect_head_shoulders(highs, closes) -> dict:
    n = len(closes)
    if n < 25:
        return {"detected": False}
    # Find 3 peaks: left shoulder, head, right shoulder
    window = min(n, 60)
    h = highs[-window:]
    peaks = [i for i in range(2, len(h)-2)
             if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]]
    if len(peaks) < 3:
        return {"detected": False}
    ls, head, rs = peaks[-3], peaks[-2], peaks[-1]
    # Head must be highest
    if h[head] > h[ls] and h[head] > h[rs]:
        # Shoulders roughly equal
        if abs(h[ls] - h[rs]) / h[head] < 0.05:
            neckline = min(closes[-window + ls:-window + rs + 1]) if ls < rs else 0
            if neckline > 0 and closes[-1] < neckline * 0.998:
                target = float(neckline - (h[head] - neckline))
                return {"detected": True, "direction": "SELL", "score": 5.5,
                        "neckline": round(float(neckline),2),
                        "head": round(float(h[head]),2),
                        "target": round(target,2)}
    return {"detected": False}


def _detect_fvg(highs, lows, closes) -> dict:
    """ICT Fair Value Gap: gap between candle N-2 high and candle N low."""
    n = len(closes)
    if n < 4:
        return {"detected": False}
    # Bullish FVG: low[i] > high[i-2] (gap up)
    if lows[-1] > highs[-3]:
        gap_size = (lows[-1] - highs[-3]) / highs[-3] * 100
        return {"detected": True, "direction": "BUY", "score": 3.5,
                "fvg_top": round(float(lows[-1]),2),
                "fvg_bot": round(float(highs[-3]),2),
                "gap_pct": round(gap_size,2),
                "note": "ict_bullish_fvg_magnet"}
    # Bearish FVG: high[i] < low[i-2] (gap down)
    if highs[-1] < lows[-3]:
        gap_size = (lows[-3] - highs[-1]) / lows[-3] * 100
        return {"detected": True, "direction": "SELL", "score": 3.5,
                "fvg_top": round(float(lows[-3]),2),
                "fvg_bot": round(float(highs[-1]),2),
                "gap_pct": round(gap_size,2),
                "note": "ict_bearish_fvg_magnet"}
    return {"detected": False}


def run_price_structure_strategy(df, df_htf=None, option_data=None) -> dict:
    """
    Drop-in strategy for signal_engine STRATEGIES list.
    Combines PDH/PDL/PWH/PWL levels + chart patterns.
    """
    empty = {"strategy": "price_structure", "score": 0.0, "direction": None, "side": None}
    try:
        if df is None or len(df) < 20:
            return empty

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        close = float(df_c["close"].iloc[-1])

        # Get levels
        levels  = get_pdh_pdl_pdc(df)
        wm_lvls = get_weekly_monthly_levels(df_htf) if df_htf is not None else {}

        pdh = levels.get("PDH", 0)
        pdl = levels.get("PDL", 0)
        pdc = levels.get("PDC", 0)
        pwh = wm_lvls.get("PWH", 0)
        pwl = wm_lvls.get("PWL", 0)
        pmh = wm_lvls.get("PMH", 0)
        pml = wm_lvls.get("PML", 0)

        # Score vs levels for both directions
        buy_mod, buy_ctx  = score_vs_key_levels(close, "BUY",  pdh, pdl, pdc, pwh, pwl, pmh, pml)
        sell_mod, sell_ctx = score_vs_key_levels(close, "SELL", pdh, pdl, pdc, pwh, pwl, pmh, pml)

        # Detect chart patterns
        patterns = detect_patterns(df_c, lookback=60)

        # Aggregate pattern signals
        pat_buy_score  = 0.0
        pat_sell_score = 0.0
        pat_notes      = []

        for pat_name, pat in patterns.items():
            if not pat.get("detected"):
                continue
            score = float(pat.get("score", 3.0))
            dirn  = pat.get("direction")
            if dirn == "BUY":
                pat_buy_score  = max(pat_buy_score,  score)
                pat_notes.append(f"{pat_name}↑")
            elif dirn == "SELL":
                pat_sell_score = max(pat_sell_score, score)
                pat_notes.append(f"{pat_name}↓")

        # Combine: levels + patterns
        total_buy  = buy_mod  + pat_buy_score
        total_sell = sell_mod + pat_sell_score

        if total_buy > total_sell and (buy_mod > 0 or pat_buy_score > 0):
            return {
                "strategy":  "price_structure",
                "score":     round(max(total_buy, 0), 2),
                "direction": "BUY",
                "side":      "BUY",
                "levels_ctx": " | ".join(buy_ctx[:3]),
                "patterns":   " | ".join(pat_notes[:3]),
                "pdh": pdh, "pdl": pdl, "pwh": pwh, "pwl": pwl,
            }
        elif total_sell > total_buy and (sell_mod > 0 or pat_sell_score > 0):
            return {
                "strategy":  "price_structure",
                "score":     round(max(total_sell, 0), 2),
                "direction": "SELL",
                "side":      "SELL",
                "levels_ctx": " | ".join(sell_ctx[:3]),
                "patterns":   " | ".join(pat_notes[:3]),
                "pdh": pdh, "pdl": pdl, "pwh": pwh, "pwl": pwl,
            }
    except Exception as e:
        logger.debug("price_structure strategy: %s", e)
    return empty
