"""
pivot_boss.py

Complete implementation of Frank Ochoa's "Secrets of a Pivot Boss" strategies.

STRATEGIES IMPLEMENTED
───────────────────────
1.  CPR (Central Pivot Range) — base levels
2.  Floor Pivots R1/R2/R3/S1/S2/S3 — standard pivot levels
3.  Camarilla Pivots H1-H4/L1-L4 — intraday reversal levels
4.  CPR Width Classification — Narrow/Medium/Wide → day type prediction
5.  Virgin CPR — untouched CPR = magnetic level
6.  CPR Trend Analysis — multi-day CPR relationship
7.  Opening Price vs CPR — first 15-min bias
8.  Target Pivots — use R1/S1 as profit targets
9.  PDH/PDL Breakout — previous day high/low as key levels
10. CPR + Pivot confluence signals

PIVOT BOSS CORE CONCEPTS
──────────────────────────
1. CPR width predicts day type:
   Narrow CPR (<0.25%)  → Trending day → ride the breakout
   Medium CPR (0.25-0.5%) → Normal day → watch for range or trend
   Wide CPR (>0.5%)    → Sideways day → fade the extremes

2. Virgin CPR:
   If price has NEVER touched the CPR since it was formed
   → CPR acts as a MAGNET (price will be attracted to it)
   → Strong support/resistance when price approaches

3. CPR Trend:
   3 consecutive days of higher CPRs → Uptrend
   3 consecutive days of lower CPRs  → Downtrend
   Overlapping CPRs                   → No trend / choppy

4. Opening Price Relationship:
   Open > TC → Bullish day bias (buy dips to TC)
   Open < BC → Bearish day bias (sell rallies to BC)
   Open between BC-TC → Sideways day (fade extremes)

5. Floor Pivots as targets:
   In uptrend: enter at pivot, target R1 → R2 → R3
   In downtrend: enter at pivot, target S1 → S2 → S3
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ── Core Level Calculations ───────────────────────────────────────────────────

def calc_cpr(high: float, low: float, close: float) -> Dict[str, float]:
    """
    Central Pivot Range from previous day's H/L/C.
    Pivot, TC (Top Central), BC (Bottom Central).
    """
    pivot = (high + low + close) / 3
    tc    = (pivot + high) / 2
    bc    = (pivot + low)  / 2
    return {
        "pivot": round(pivot, 2),
        "tc":    round(tc, 2),
        "bc":    round(bc, 2),
    }


def calc_floor_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    """
    Classic floor pivot levels: P, R1, R2, R3, S1, S2, S3.
    Used by floor traders for decades.
    These are the levels that EVERYONE watches → self-fulfilling.
    """
    p  = (high + low + close) / 3
    r1 = (2 * p) - low
    s1 = (2 * p) - high
    r2 = p + (high - low)
    s2 = p - (high - low)
    r3 = high + 2 * (p - low)
    s3 = low  - 2 * (high - p)
    return {
        "P":  round(p,  2),
        "R1": round(r1, 2),
        "R2": round(r2, 2),
        "R3": round(r3, 2),
        "S1": round(s1, 2),
        "S2": round(s2, 2),
        "S3": round(s3, 2),
    }


def calc_camarilla_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    """
    Camarilla pivots — developed by Nick Scott in 1989.
    H4/L4 are the most important — breakout levels.
    H3/L3 are reversal levels.
    Key for intraday options trading.

    If price is between H3 and L3 → range day, fade the extremes
    If price breaks H4 → strong uptrend, buy CE
    If price breaks L4 → strong downtrend, buy PE
    """
    rng = high - low
    h1  = close + rng * 1.1 / 12
    h2  = close + rng * 1.1 / 6
    h3  = close + rng * 1.1 / 4    # KEY reversal resistance
    h4  = close + rng * 1.1 / 2    # KEY breakout level
    h5  = (high / low) * close      # extreme level
    l1  = close - rng * 1.1 / 12
    l2  = close - rng * 1.1 / 6
    l3  = close - rng * 1.1 / 4    # KEY reversal support
    l4  = close - rng * 1.1 / 2    # KEY breakout level
    l5  = close - (h5 - close)      # extreme level
    return {
        "H5": round(h5, 2), "H4": round(h4, 2), "H3": round(h3, 2),
        "H2": round(h2, 2), "H1": round(h1, 2),
        "L1": round(l1, 2), "L2": round(l2, 2), "L3": round(l3, 2),
        "L4": round(l4, 2), "L5": round(l5, 2),
    }


def calc_weekly_pivots(
    weekly_high: float, weekly_low: float, weekly_close: float
) -> Dict[str, float]:
    """
    Weekly pivot levels — higher timeframe bias.
    If price > weekly pivot → weekly trend is bullish
    If price < weekly pivot → weekly trend is bearish
    """
    fp = calc_floor_pivots(weekly_high, weekly_low, weekly_close)
    return {f"W_{k}": v for k, v in fp.items()}


# ── CPR Width Classification ──────────────────────────────────────────────────


def calc_monthly_pivots(
    monthly_high: float, monthly_low: float, monthly_close: float
) -> Dict[str, float]:
    """
    Monthly pivot levels — macro bias and institutional zones.
    Monthly R2/S2 = strong institutional profit-booking / accumulation zones.
    Near monthly R2 → reduce long targets. Near monthly S2 → strong buy zone.
    """
    fp = calc_floor_pivots(monthly_high, monthly_low, monthly_close)
    return {f"M_{k}": v for k, v in fp.items()}

def classify_cpr_width(cpr: Dict[str, float]) -> Dict[str, str]:
    """
    Pivot Boss CPR Width Classification.
    Width of CPR predicts what kind of day tomorrow will be.

    Returns:
        {
          "classification": "NARROW" | "MEDIUM" | "WIDE",
          "day_type":       "TRENDING" | "NORMAL" | "SIDEWAYS",
          "bias":           "BREAKOUT" | "WATCH" | "FADE",
          "width_pct":      "0.25%",
        }
    """
    pivot = cpr.get("pivot", 1)
    tc    = cpr.get("tc", pivot)
    bc    = cpr.get("bc", pivot)
    width_pct = abs(tc - bc) / pivot if pivot > 0 else 0

    if width_pct < 0.0025:      # < 0.25%
        return {
            "classification": "NARROW",
            "day_type":       "TRENDING",
            "bias":           "BREAKOUT",
            "width_pct":      f"{width_pct:.3%}",
            "note":           "Narrow CPR → expect trend. Trade breakout direction.",
        }
    elif width_pct < 0.005:     # 0.25% – 0.5%
        return {
            "classification": "MEDIUM",
            "day_type":       "NORMAL",
            "bias":           "WATCH",
            "width_pct":      f"{width_pct:.3%}",
            "note":           "Medium CPR → wait for open to determine direction.",
        }
    else:                        # > 0.5%
        return {
            "classification": "WIDE",
            "day_type":       "SIDEWAYS",
            "bias":           "FADE",
            "width_pct":      f"{width_pct:.3%}",
            "note":           "Wide CPR → range day expected. Fade extremes.",
        }


# ── Virgin CPR Detection ──────────────────────────────────────────────────────

def is_virgin_cpr(
    df_today: pd.DataFrame,
    cpr:      Dict[str, float],
) -> Dict[str, bool]:
    """
    Detect if today's CPR is Virgin (price has not touched it yet).
    Virgin CPR = magnetic level — price will be pulled toward it.

    A CPR is touched if any bar's High >= BC and Low <= TC
    (i.e., price entered the CPR range).
    """
    tc    = cpr.get("tc", 0)
    bc    = cpr.get("bc", 0)
    pivot = cpr.get("pivot", 0)

    if df_today is None or len(df_today) == 0:
        return {"virgin": True, "touched": False, "bars_since_touch": -1}

    df = df_today.copy()
    df.columns = [c.lower() for c in df.columns]

    if "high" not in df.columns or "low" not in df.columns:
        return {"virgin": True, "touched": False, "bars_since_touch": -1}

    # CPR is touched if price entered the TC-BC zone
    touched_mask = (df["high"] >= bc) & (df["low"] <= tc)
    touched      = bool(touched_mask.any())

    first_touch = int(touched_mask.idxmax()) if touched else -1
    bars_since  = len(df) - first_touch if touched else len(df)

    pivot_touched = bool((df["high"] >= pivot) & (df["low"] <= pivot)).any() \
                    if pivot > 0 else False

    return {
        "virgin":           not touched,
        "touched":          touched,
        "pivot_touched":    pivot_touched,
        "bars_since_touch": bars_since,
        "note": (
            "Virgin CPR — price will be attracted to this zone"
            if not touched
            else f"CPR touched {bars_since} bars ago"
        ),
    }


# ── CPR Trend Analysis (multi-day) ────────────────────────────────────────────

def analyse_cpr_trend(
    daily_data: List[Dict],   # list of {high, low, close} dicts, oldest first
    lookback:   int = 5,
) -> Dict[str, str]:
    """
    Pivot Boss CPR Trend Analysis.
    Compare pivot positions over multiple days.

    Uptrend:   Each day's pivot > previous day's pivot
    Downtrend: Each day's pivot < previous day's pivot
    Sideways:  Pivots overlapping / no clear direction

    Returns:
        {
          "trend":     "UP" | "DOWN" | "SIDEWAYS",
          "strength":  "STRONG" | "MODERATE" | "WEAK",
          "days":      5,
          "note":      "...",
        }
    """
    if not daily_data or len(daily_data) < 2:
        return {"trend": "UNKNOWN", "strength": "WEAK", "days": 0, "note": "insufficient data"}

    recent = daily_data[-lookback:] if len(daily_data) >= lookback else daily_data
    cprs   = []
    for d in recent:
        c = calc_cpr(d["high"], d["low"], d["close"])
        cprs.append(c)

    pivots = [c["pivot"] for c in cprs]
    tcs    = [c["tc"]    for c in cprs]
    bcs    = [c["bc"]    for c in cprs]

    # Count consecutive higher/lower pivots
    up_count   = sum(1 for i in range(1, len(pivots)) if pivots[i] > pivots[i-1])
    down_count = sum(1 for i in range(1, len(pivots)) if pivots[i] < pivots[i-1])
    overlap    = sum(
        1 for i in range(1, len(cprs))
        if tcs[i] >= bcs[i-1] and bcs[i] <= tcs[i-1]
    )

    n = len(pivots) - 1   # number of comparisons

    if up_count >= n * 0.75:
        trend    = "UP"
        strength = "STRONG" if up_count == n else "MODERATE"
    elif down_count >= n * 0.75:
        trend    = "DOWN"
        strength = "STRONG" if down_count == n else "MODERATE"
    elif overlap >= n * 0.5:
        trend    = "SIDEWAYS"
        strength = "STRONG"
    else:
        trend    = "SIDEWAYS"
        strength = "WEAK"

    return {
        "trend":    trend,
        "strength": strength,
        "days":     len(recent),
        "up_days":  up_count,
        "dn_days":  down_count,
        "note":     (
            f"CPR trend {trend} ({strength}) over {len(recent)} days. "
            f"Up: {up_count}, Down: {down_count}, Overlap: {overlap}"
        ),
    }


# ── Opening Price Analysis ────────────────────────────────────────────────────

def analyse_open_vs_cpr(
    open_price: float,
    cpr:        Dict[str, float],
    floor_pivots: Dict[str, float],
) -> Dict[str, str]:
    """
    Pivot Boss Opening Price Analysis.
    First 15 minutes determines the bias for the whole day.

    Open above TC → bullish gap up → expect test of R1/R2
    Open below BC → bearish gap down → expect test of S1/S2
    Open in CPR   → sideways open → wait for breakout of TC or BC

    Returns bias and likely day targets.
    """
    tc = cpr.get("tc", 0)
    bc = cpr.get("bc", 0)
    p  = floor_pivots.get("P", cpr.get("pivot", 0))
    r1 = floor_pivots.get("R1", 0)
    s1 = floor_pivots.get("S1", 0)
    r2 = floor_pivots.get("R2", 0)
    s2 = floor_pivots.get("S2", 0)

    if open_price > tc:
        gap_pct  = (open_price - tc) / tc
        strength = "STRONG" if gap_pct > 0.005 else "MODERATE"
        return {
            "bias":       "BULLISH",
            "strength":   strength,
            "action":     "BUY dips to TC level",
            "target_1":   r1,
            "target_2":   r2,
            "stop_zone":  bc,
            "note":       f"Open {gap_pct:.2%} above TC → bullish day bias → target R1={r1} R2={r2}",
        }
    elif open_price < bc:
        gap_pct  = (bc - open_price) / bc
        strength = "STRONG" if gap_pct > 0.005 else "MODERATE"
        return {
            "bias":       "BEARISH",
            "strength":   strength,
            "action":     "SELL rallies to BC level",
            "target_1":   s1,
            "target_2":   s2,
            "stop_zone":  tc,
            "note":       f"Open {gap_pct:.2%} below BC → bearish day bias → target S1={s1} S2={s2}",
        }
    else:
        dist_tc = abs(open_price - tc) / tc
        dist_bc = abs(open_price - bc) / bc
        return {
            "bias":       "NEUTRAL",
            "strength":   "WAIT",
            "action":     "Wait for TC or BC breakout before trading",
            "target_1":   r1 if open_price > p else s1,
            "target_2":   r2 if open_price > p else s2,
            "stop_zone":  bc if open_price > p else tc,
            "note":       "Open inside CPR → wait for breakout direction",
        }


# ── PDH/PDL Analysis ─────────────────────────────────────────────────────────


def get_mtf_pivot_score_mod(
    price:         float,
    direction:     str,
    daily_levels:  Dict[str, float],
    weekly_levels: Dict[str, float] = None,
    monthly_levels:Dict[str, float] = None,
) -> tuple:
    """
    Multi-timeframe pivot score modifier for ANY strategy.

    Returns (modifier: float, context: str)

    Rules:
      WEEKLY BREAKOUT  → +1.5 (breaking major weekly level = strong trend)
      WEEKLY REJECTION → -1.0 (near weekly R2/S2 = strong resistance)
      MONTHLY ZONE     → +0.8 (near monthly S1/S2 = institutional support)
      MONTHLY BARRIER  → -0.8 (near monthly R1/R2 = institutional resistance)
      LEVEL CONFLUENCE → +1.0 extra when daily + weekly align
    """
    mod     = 0.0
    context = []
    tol     = 0.002   # 0.2% tolerance for "near" a level

    def near(lvl):
        return lvl > 0 and abs(price - lvl) / lvl < tol

    def above(lvl):
        return lvl > 0 and price > lvl

    def below(lvl):
        return lvl > 0 and price < lvl

    # ── Weekly levels ──────────────────────────────────────────────────────
    if weekly_levels:
        wp  = weekly_levels.get("W_P",  0)
        wr1 = weekly_levels.get("W_R1", 0)
        wr2 = weekly_levels.get("W_R2", 0)
        ws1 = weekly_levels.get("W_S1", 0)
        ws2 = weekly_levels.get("W_S2", 0)

        # Breaking above weekly R1 = strong breakout
        if above(wr1) and direction == "BUY" and price > wr1 * 0.999:
            mod += 1.5
            context.append(f"above_W_R1({wr1:.0f})")

        # Breaking below weekly S1 = strong breakdown
        if below(ws1) and direction == "SELL" and price < ws1 * 1.001:
            mod += 1.5
            context.append(f"below_W_S1({ws1:.0f})")

        # Near weekly R2 = strong resistance, penalise BUY
        if near(wr2) and direction == "BUY":
            mod -= 1.0
            context.append(f"near_W_R2({wr2:.0f})_resistance")

        # Near weekly S2 = strong support, penalise SELL
        if near(ws2) and direction == "SELL":
            mod -= 1.0
            context.append(f"near_W_S2({ws2:.0f})_support")

        # Price above weekly pivot = weekly bullish bias
        if above(wp) and direction == "BUY":
            mod += 0.5
            context.append("above_W_P_bullish")
        elif below(wp) and direction == "SELL":
            mod += 0.5
            context.append("below_W_P_bearish")

        # Weekly pivot as magnet: near weekly pivot = mean reversion zone
        if near(wp):
            mod -= 0.3   # reduce trend/breakout signals near pivot (chop zone)
            context.append(f"near_W_P({wp:.0f})_chop")

    # ── Monthly levels ─────────────────────────────────────────────────────
    if monthly_levels:
        mp  = monthly_levels.get("M_P",  0)
        mr1 = monthly_levels.get("M_R1", 0)
        mr2 = monthly_levels.get("M_R2", 0)
        ms1 = monthly_levels.get("M_S1", 0)
        ms2 = monthly_levels.get("M_S2", 0)

        # Monthly R2 = institutional sell zone
        if near(mr2) and direction == "BUY":
            mod -= 0.8
            context.append(f"near_M_R2({mr2:.0f})_inst_sell")

        # Monthly S2 = institutional buy zone
        if near(ms2) and direction == "SELL":
            mod -= 0.8
            context.append(f"near_M_S2({ms2:.0f})_inst_buy")

        # Monthly S1 = support, boost BUY
        if near(ms1) and direction == "BUY":
            mod += 0.8
            context.append(f"near_M_S1({ms1:.0f})_support")

        # Monthly R1 = resistance, boost SELL
        if near(mr1) and direction == "SELL":
            mod += 0.8
            context.append(f"near_M_R1({mr1:.0f})_resistance")

        # Monthly macro bias
        if above(mp) and direction == "BUY":
            mod += 0.3
            context.append("above_M_P_macro_bull")
        elif below(mp) and direction == "SELL":
            mod += 0.3
            context.append("below_M_P_macro_bear")

    # ── Level confluence bonus ─────────────────────────────────────────────
    # When daily R1 ≈ weekly R1 → double confirmation
    if weekly_levels and daily_levels:
        dr1 = daily_levels.get("R1", 0)
        wr1 = weekly_levels.get("W_R1", 0)
        if dr1 > 0 and wr1 > 0 and abs(dr1 - wr1) / wr1 < 0.005:
            # Daily and weekly R1 confluent
            if direction == "BUY" and above(dr1):
                mod += 1.0
                context.append(f"D_W_R1_confluence({dr1:.0f})")
            elif direction == "SELL" and below(dr1):
                mod += 0.8
                context.append(f"D_W_S1_confluence({dr1:.0f})")

    return round(max(-3.0, min(3.0, mod)), 2), " | ".join(context)

def get_pdh_pdl(df: pd.DataFrame) -> Dict[str, float]:
    """
    Previous Day High/Low — critical Pivot Boss levels.

    PDH = Previous Day High → major resistance
    PDL = Previous Day Low  → major support

    PDH breakout = strong BUY signal
    PDL breakdown = strong SELL signal

    These are the levels institutions watch most closely.
    """
    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    if "high" not in df_c.columns:
        return {}

    prev_day_bars = df_c.iloc[:78]   # first 78 5-min bars = previous full session
    if len(prev_day_bars) < 10:
        return {}

    pdh = float(prev_day_bars["high"].max())
    pdl = float(prev_day_bars["low"].min())
    pdc = float(prev_day_bars["close"].iloc[-1])

    return {
        "PDH": round(pdh, 2),
        "PDL": round(pdl, 2),
        "PDC": round(pdc, 2),
        "range": round(pdh - pdl, 2),
    }


# ── Main Pivot Boss Signal ────────────────────────────────────────────────────

def pivot_boss_signal(
    df:           pd.DataFrame,
    df_daily:     Optional[pd.DataFrame] = None,
    option_data:  Optional[dict]         = None,
) -> Dict:
    """
    Complete Pivot Boss signal generator.
    Combines CPR, floor pivots, Camarilla, PDH/PDL, and opening analysis.

    Returns:
        {
          "direction":   "BUY" | "SELL" | None,
          "score":       float,
          "strategy":    "pivot_boss_...",
          "levels":      {...all pivot levels...},
          "day_type":    "TRENDING" | "SIDEWAYS" | "NORMAL",
          "bias":        "BULLISH" | "BEARISH" | "NEUTRAL",
          "targets":     [R1/S1, R2/S2],
          "stop":        float,
          "reason":      str,
        }
    """
    empty = {"direction": None, "score": 0.0, "strategy": "pivot_boss", "reason": "insufficient_data"}

    if df is None or len(df) < 80:
        return empty

    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    if "close" not in df_c.columns:
        return empty

    # ── Get previous day data ───────────────────────────────────────────
    prev_bars  = df_c.iloc[:78]
    if len(prev_bars) < 20:
        return empty

    prev_H = float(prev_bars["high"].max())  if "high"  in prev_bars.columns else 0
    prev_L = float(prev_bars["low"].min())   if "low"   in prev_bars.columns else 0
    prev_C = float(prev_bars["close"].iloc[-1])
    if prev_H <= 0 or prev_L <= 0:
        return empty

    # ── Calculate all levels ───────────────────────────────────────────
    cpr    = calc_cpr(prev_H, prev_L, prev_C)
    floor  = calc_floor_pivots(prev_H, prev_L, prev_C)
    cam    = calc_camarilla_pivots(prev_H, prev_L, prev_C)
    pdh_pdl = get_pdh_pdl(df_c)

    # ── Weekly levels from df_daily (if available) ─────────────────────
    weekly_levels  = {}
    monthly_levels = {}
    if df_daily is not None and len(df_daily) >= 5:
        try:
            dfc = df_daily.copy()
            dfc.columns = [c.lower() for c in dfc.columns]
            # Weekly: last 5 daily bars
            w_bars = dfc.tail(6).iloc[:-1]  # last full week
            if len(w_bars) >= 3:
                wH = float(w_bars["high"].max())
                wL = float(w_bars["low"].min())
                wC = float(w_bars["close"].iloc[-1])
                weekly_levels = calc_weekly_pivots(wH, wL, wC)
            # Monthly: last 20 daily bars
            m_bars = dfc.tail(22).iloc[:-2]  # approx last month
            if len(m_bars) >= 10:
                mH = float(m_bars["high"].max())
                mL = float(m_bars["low"].min())
                mC = float(m_bars["close"].iloc[-1])
                monthly_levels = calc_monthly_pivots(mH, mL, mC)
        except Exception as _we:
            logger.debug("Weekly/monthly pivot calc: %s", _we)

    # ── Today's data ───────────────────────────────────────────────────
    today_bars = df_c.iloc[78:]
    if len(today_bars) < 3:
        today_bars = df_c.iloc[-15:]

    open_price = float(today_bars["open"].iloc[0]  if "open" in today_bars.columns else today_bars["close"].iloc[0])
    close      = float(df_c["close"].iloc[-1])
    prev_close = float(df_c["close"].iloc[-2])

    # ── CPR Width Classification ───────────────────────────────────────
    width_info  = classify_cpr_width(cpr)
    day_type    = width_info["day_type"]     # TRENDING / NORMAL / SIDEWAYS

    # ── Virgin CPR ─────────────────────────────────────────────────────
    virgin_info = is_virgin_cpr(today_bars, cpr)
    is_virgin   = virgin_info.get("virgin", False)

    # ── Opening bias ───────────────────────────────────────────────────
    open_bias   = analyse_open_vs_cpr(open_price, cpr, floor)
    day_bias    = open_bias.get("bias", "NEUTRAL")   # BULLISH / BEARISH / NEUTRAL

    # ── Extract key levels ─────────────────────────────────────────────
    pivot  = cpr["pivot"]
    tc     = cpr["tc"]
    bc     = cpr["bc"]
    r1     = floor["R1"]; r2 = floor["R2"]
    s1     = floor["S1"]; s2 = floor["S2"]
    h3     = cam["H3"];   h4 = cam["H4"]
    l3     = cam["L3"];   l4 = cam["L4"]
    pdh    = pdh_pdl.get("PDH", 0)
    pdl    = pdh_pdl.get("PDL", 0)

    direction = None
    score     = 0.0
    reason    = ""
    targets   = []
    stop      = 0.0

    # ═══════════════════════════════════════════════════════════════════
    # PIVOT BOSS SIGNAL LOGIC
    # ═══════════════════════════════════════════════════════════════════

    # ── SIGNAL 1: PDH Breakout (highest conviction BUY) ────────────────
    # Previous Day High breakout = institutions stepping in
    if pdh > 0 and prev_close <= pdh and close > pdh:
        direction = "BUY"
        score     = 8.5
        targets   = [r1, r2]
        stop      = pdh * 0.998   # stop just below PDH (now support)
        reason    = f"PDH_breakout_{pdh:.0f}_strong_BUY"

    # ── SIGNAL 2: PDL Breakdown (highest conviction SELL) ──────────────
    elif pdl > 0 and prev_close >= pdl and close < pdl:
        direction = "SELL"
        score     = 8.5
        targets   = [s1, s2]
        stop      = pdl * 1.002
        reason    = f"PDL_breakdown_{pdl:.0f}_strong_SELL"

    # ── SIGNAL 3: Camarilla H4 Breakout ────────────────────────────────
    # H4 break = very strong trend day signal (rare but very reliable)
    elif h4 > 0 and prev_close <= h4 and close > h4:
        direction = "BUY"
        score     = 9.0   # Pivot Boss calls this the strongest signal
        targets   = [cam.get("H5", r2), r2]
        stop      = h3
        reason    = f"camarilla_H4_breakout_{h4:.0f}_extremely_strong"

    # ── SIGNAL 4: Camarilla L4 Breakdown ───────────────────────────────
    elif l4 > 0 and prev_close >= l4 and close < l4:
        direction = "SELL"
        score     = 9.0
        targets   = [cam.get("L5", s2), s2]
        stop      = l3
        reason    = f"camarilla_L4_breakdown_{l4:.0f}_extremely_strong"

    # ── SIGNAL 5: Camarilla H3 Rejection (fade) ────────────────────────
    # Price touches H3 and reverses → SELL (range day fade)
    elif h3 > 0 and day_type == "SIDEWAYS":
        near_h3 = abs(close - h3) / h3 < 0.001
        if near_h3 and close < prev_close:
            direction = "SELL"
            score     = 6.5
            targets   = [l3, cam.get("L2", s1)]
            stop      = h4
            reason    = f"camarilla_H3_rejection_{h3:.0f}_sideways_fade"

    # ── SIGNAL 6: Camarilla L3 Bounce (fade) ───────────────────────────
    elif l3 > 0 and day_type == "SIDEWAYS":
        near_l3 = abs(close - l3) / l3 < 0.001
        if near_l3 and close > prev_close:
            direction = "BUY"
            score     = 6.5
            targets   = [h3, cam.get("H2", r1)]
            stop      = l4
            reason    = f"camarilla_L3_bounce_{l3:.0f}_sideways_fade"

    # ── SIGNAL 7: TC Breakout (Trending day) ───────────────────────────
    elif prev_close <= tc and close > tc and day_type == "TRENDING":
        direction = "BUY"
        score     = 7.5 + (1.0 if is_virgin else 0)
        targets   = [r1, r2]
        stop      = pivot   # pivot as stop on trending day
        reason    = f"TC_breakout_{tc:.0f}_trending_day{'_virgin_cpr' if is_virgin else ''}"

    # ── SIGNAL 8: BC Breakdown (Trending day) ──────────────────────────
    elif prev_close >= bc and close < bc and day_type == "TRENDING":
        direction = "SELL"
        score     = 7.5 + (1.0 if is_virgin else 0)
        targets   = [s1, s2]
        stop      = pivot
        reason    = f"BC_breakdown_{bc:.0f}_trending_day{'_virgin_cpr' if is_virgin else ''}"

    # ── SIGNAL 9: Virgin CPR approach ──────────────────────────────────
    # Price approaching virgin CPR from above or below = magnetic pull
    elif is_virgin:
        approaching_from_above = close > tc and abs(close - tc) / tc < 0.003
        approaching_from_below = close < bc and abs(close - bc) / bc < 0.003
        if approaching_from_above and day_bias == "BEARISH":
            direction = "SELL"
            score     = 6.0
            targets   = [bc, s1]
            stop      = close * 1.003
            reason    = f"virgin_CPR_magnet_approach_from_above"
        elif approaching_from_below and day_bias == "BULLISH":
            direction = "BUY"
            score     = 6.0
            targets   = [tc, r1]
            stop      = close * 0.997
            reason    = f"virgin_CPR_magnet_approach_from_below"

    # ── SIGNAL 10: Floor Pivot Bounce ──────────────────────────────────
    # Price bouncing off S1/R1 with correct daily bias
    elif s1 > 0 and day_bias == "BULLISH":
        at_s1 = abs(close - s1) / s1 < 0.002
        if at_s1 and close > prev_close:
            direction = "BUY"
            score     = 6.0
            targets   = [pivot, r1]
            stop      = s2
            reason    = f"floor_S1_bounce_{s1:.0f}_bullish_day"

    elif r1 > 0 and day_bias == "BEARISH":
        at_r1 = abs(close - r1) / r1 < 0.002
        if at_r1 and close < prev_close:
            direction = "SELL"
            score     = 6.0
            targets   = [pivot, s1]
            stop      = r2
            reason    = f"floor_R1_rejection_{r1:.0f}_bearish_day"

    if not direction:
        return {**empty, "reason": "no_pivot_boss_setup", "levels": {
            "pivot": pivot, "tc": tc, "bc": bc, "R1": r1, "S1": s1,
            "H3": h3, "H4": h4, "L3": l3, "L4": l4,
            "PDH": pdh, "PDL": pdl,
        }, "day_type": day_type, "bias": day_bias}

    # Apply day type score adjustment
    if day_type == "TRENDING" and direction in ("BUY","SELL"):
        score += 0.5
    if day_type == "SIDEWAYS" and "breakout" in reason.lower():
        score -= 1.0   # breakout signals less reliable on sideways days

    # Apply opening bias alignment bonus
    if (direction == "BUY"  and day_bias == "BULLISH") or \
       (direction == "SELL" and day_bias == "BEARISH"):
        score += 1.0

    return {
        "direction":  direction,
        "score":      round(min(score, 10.0), 2),
        "strategy":   f"pivot_boss_{reason.split('_')[0].lower()}",
        "reason":     reason,
        "day_type":   day_type,
        "bias":       day_bias,
        "targets":    [round(t, 2) for t in targets if t],
        "stop":       round(stop, 2),
        "is_virgin":  is_virgin,
        "cpr_width":  width_info["classification"],
        "levels": {
            "pivot": pivot, "tc": tc, "bc": bc,
            "R1": r1, "R2": r2, "R3": floor.get("R3",0),
            "S1": s1, "S2": s2, "S3": floor.get("S3",0),
            "H3": h3, "H4": h4, "L3": l3, "L4": l4,
            "PDH": pdh, "PDL": pdl,
        },
    }


def run_pivot_boss_strategy(df, df_htf=None, option_data=None) -> Dict:
    """
    Drop-in strategy function compatible with signal_engine.py STRATEGIES list.
    """
    try:
        result = pivot_boss_signal(df, df_htf, option_data)
        return {
            "strategy":  result.get("strategy", "pivot_boss"),
            "score":     float(result.get("score", 0.0)),
            "direction": result.get("direction"),
            "reason":    result.get("reason", ""),
            "day_type":  result.get("day_type", ""),
            "targets":   result.get("targets", []),
            "stop":      result.get("stop", 0.0),
            "is_virgin": result.get("is_virgin", False),
            "levels":    result.get("levels", {}),
        }
    except Exception as e:
        logger.debug("Pivot boss strategy error: %s", e)
        return {"strategy": "pivot_boss", "score": 0.0, "direction": None}


# ── Dashboard helper ──────────────────────────────────────────────────────────

def get_pivot_levels_summary(df: pd.DataFrame) -> str:
    """
    Returns a human-readable summary of all pivot levels.
    Useful for Telegram alerts and dashboard.
    """
    if df is None or len(df) < 80:
        return "Insufficient data for pivot calculation"

    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    prev = df_c.iloc[:78]

    H = float(prev["high"].max())  if "high"  in prev.columns else 0
    L = float(prev["low"].min())   if "low"   in prev.columns else 0
    C = float(prev["close"].iloc[-1])

    if H <= 0 or L <= 0:
        return "Unable to calculate pivots"

    cpr   = calc_cpr(H, L, C)
    floor = calc_floor_pivots(H, L, C)
    cam   = calc_camarilla_pivots(H, L, C)
    width = classify_cpr_width(cpr)

    return (
        f"📊 PIVOT LEVELS\n"
        f"CPR: BC={cpr['bc']:.0f} | P={cpr['pivot']:.0f} | TC={cpr['tc']:.0f}"
        f" [{width['classification']}]\n"
        f"Floor: S2={floor['S2']:.0f} S1={floor['S1']:.0f} R1={floor['R1']:.0f}"
        f" R2={floor['R2']:.0f}\n"
        f"Cam:   L4={cam['L4']:.0f} L3={cam['L3']:.0f} H3={cam['H3']:.0f}"
        f" H4={cam['H4']:.0f}\n"
        f"Day type: {width['day_type']} | Note: {width['note']}"
    )
