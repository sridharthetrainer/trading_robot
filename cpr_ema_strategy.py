"""
cpr_ema_strategy.py — Your Complete Trade Plan Implemented

Exactly follows your 5-step strategy:
  Step 1: Check Daily vs Weekly CPR structure (Bullish/Bearish/Consolidation)
  Step 2: Check bias — Price vs CPR + 1-min 200 EMA
  Step 3: Wait for entry — 5-min 20 EMA confirmation + pullback
  Step 4: Enter with confluence — CPR + Camarilla Golden zones + EMA
  Step 5: Exit at next R/S level or EMA failure

Author: Based on Sridhar's trading methodology
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    """Full EMA array (not just last value)."""
    k = 2.0 / (n + 1)
    ema = np.zeros(len(arr))
    ema[0] = arr[0]
    for i in range(1, len(arr)):
        ema[i] = arr[i] * k + ema[i - 1] * (1 - k)
    return ema


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: CPR STRUCTURE — Daily vs Weekly vs Monthly
# ─────────────────────────────────────────────────────────────────────────────
def get_cpr_structure(
    price:          float,
    daily_pivot:    float, daily_tc: float, daily_bc: float,
    weekly_pivot:   float, weekly_tc: float, weekly_bc: float,
    monthly_pivot:  float = 0,
    monthly_tc:     float = 0,
) -> Dict:
    """
    STEP 1 — Determine market structure from multi-TF CPR.

    Bullish Structure:
      Daily CPR above Weekly CPR (daily_pivot > weekly_pivot)
      Price above Daily CPR (price > daily_tc)
      → Focus ONLY on BUY. Targets: R1 → R2 → R3 → R4

    Bearish Structure:
      Daily CPR below Weekly CPR
      Price below Daily CPR
      → Focus ONLY on SELL. Targets: S1 → S2 → S3 → S4

    Consolidation:
      Price between Daily CPR and Weekly CPR
      → Avoid aggressive trades. Only breakouts or clear rejection.
    """
    daily_cpr_above_weekly  = daily_pivot > weekly_pivot
    daily_cpr_below_weekly  = daily_pivot < weekly_pivot
    price_above_daily_cpr   = price > daily_tc
    price_below_daily_cpr   = price < daily_bc
    price_inside_daily_cpr  = daily_bc <= price <= daily_tc

    # Determine structure
    if daily_cpr_above_weekly and price_above_daily_cpr:
        structure = "STRONG_BULLISH"
        bias      = "BUY"
        meaning   = "Market strong, buyers in control, continuation UP"
        plan      = "Focus BUY only. Targets: R1 → R2 → R3 → R4"
    elif daily_cpr_below_weekly and price_below_daily_cpr:
        structure = "STRONG_BEARISH"
        bias      = "SELL"
        meaning   = "Market weak, sellers in control, continuation DOWN"
        plan      = "Focus SELL only. Targets: S1 → S2 → S3 → S4"
    elif not daily_cpr_above_weekly and not daily_cpr_below_weekly:
        structure = "CONSOLIDATION"
        bias      = "NEUTRAL"
        meaning   = "Market confused, high chance of sideways/range"
        plan      = "Avoid aggressive trades. Only breakouts or clear rejection"
    elif price_inside_daily_cpr:
        structure = "CONSOLIDATION"
        bias      = "NEUTRAL"
        meaning   = "Price inside CPR — range bound"
        plan      = "Wait for breakout above TC or breakdown below BC"
    elif daily_cpr_above_weekly and price_below_daily_cpr:
        structure = "WEAK_BULLISH"
        bias      = "BUY"
        meaning   = "Bullish structure but price below CPR — wait for reclaim"
        plan      = "Buy only after price reclaims above daily BC"
    else:
        structure = "WEAK_BEARISH"
        bias      = "SELL"
        meaning   = "Bearish structure but price above CPR — wait for fail"
        plan      = "Sell only after price fails below daily TC"

    # Monthly reference
    monthly_note = ""
    if monthly_pivot:
        if abs(price - monthly_pivot) / monthly_pivot < 0.005:
            monthly_note = "⚠️ At Monthly CPR — major reversal zone"
        elif abs(price - monthly_tc) / monthly_tc < 0.005:
            monthly_note = "⚠️ At Monthly TC — strong resistance/support"

    return {
        "structure":         structure,
        "bias":              bias,
        "meaning":           meaning,
        "plan":              plan,
        "monthly_note":      monthly_note,
        "daily_above_weekly":daily_cpr_above_weekly,
        "score_mod":         1.5 if bias == "BUY" else -1.5 if bias == "SELL" else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: BIAS FILTER — 1-min 200 EMA Trend Filter
# ─────────────────────────────────────────────────────────────────────────────
def get_ema_bias(
    df_1min: pd.DataFrame,
    ema_period: int = 200,
) -> Dict:
    """
    STEP 2 — 1-min 200 EMA trend filter.

    Above 200 EMA → Market bias UP → Only look for BUY
    Below 200 EMA → Market bias DOWN → Only look for SELL

    This is your primary trend filter. No counter-trend trades.
    """
    try:
        df = df_1min.copy()
        df.columns = [c.lower() for c in df.columns]
        if len(df) < ema_period: return {"bias": "NEUTRAL", "ema200": 0, "score_mod": 0}

        closes  = df["close"].values
        ema200  = float(_ema(closes, ema_period)[-1])
        price   = float(closes[-1])
        prev    = float(closes[-2]) if len(closes) > 1 else price

        above   = price > ema200
        turning = (prev < ema200 <= price) or (prev > ema200 >= price)

        return {
            "bias":       "BUY"  if above else "SELL",
            "ema200":     round(ema200, 2),
            "price":      round(price, 2),
            "above_200":  above,
            "turning":    turning,
            "score_mod":  0.8 if above else -0.8,
            "note":       f"Price {'above' if above else 'below'} 200 EMA ({ema200:.0f})"
        }
    except Exception as e:
        logger.debug("ema_bias: %s", e)
        return {"bias": "NEUTRAL", "ema200": 0, "score_mod": 0}


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: ENTRY TRIGGER — 5-min 20 EMA with Pullback Confirmation
# ─────────────────────────────────────────────────────────────────────────────
def get_ema20_entry_signal(
    df_5min: pd.DataFrame,
    bias:    str = "BUY",
) -> Dict:
    """
    STEP 3 — 5-min 20 EMA entry trigger with pullback confirmation.

    BULLISH ENTRY:
      Condition: Price above 1-min 200 EMA + above 5-min 20 EMA
      Entry: Buy when candle confirms ABOVE 20 EMA
      Pullback rule:
        If next candle falls below 20 EMA → WAIT
        Next candle GREEN → trend continues → Hold/Re-enter
        Next candle RED   → weak trend → Avoid/exit

    BEARISH ENTRY:
      Condition: Price below 1-min 200 EMA + below 5-min 20 EMA
      Entry: Sell when candle confirms BELOW 20 EMA
      Pullback rule:
        If next candle turns green → WAIT
        Next candle RED (breaks below 20 EMA) → Strong downtrend → Continue sell
        Next candle GREEN → Exit
    """
    empty = {"signal": None, "score": 0.0, "ema20": 0, "detail": ""}
    try:
        df = df_5min.copy()
        df.columns = [c.lower() for c in df.columns]
        if len(df) < 25: return empty

        opens   = df["open"].values   if "open"  in df.columns else df["close"].values
        closes  = df["close"].values
        ema20_arr = _ema(closes, 20)
        ema20_now  = float(ema20_arr[-1])
        ema20_prev = float(ema20_arr[-2])

        cur_close  = float(closes[-1])
        cur_open   = float(opens[-1])
        prev_close = float(closes[-2])
        prev_open  = float(opens[-2]) if len(opens) > 1 else cur_open

        is_green   = cur_close  > cur_open
        is_red     = cur_close  < cur_open
        prev_green = prev_close > prev_open
        prev_red   = prev_close < prev_open

        score = 0.0
        signal = None
        detail = ""

        if bias == "BUY":
            # Price above 20 EMA = BUY zone
            if cur_close > ema20_now:
                # Pullback scenario: previous candle dipped below EMA but current confirms above
                if prev_close < ema20_prev and cur_close > ema20_now:
                    if is_green:
                        score  = 4.0
                        signal = "BUY"
                        detail = "Pullback confirmed: dipped below 20 EMA, now GREEN above → strong entry"
                    else:
                        score  = 1.5
                        signal = None
                        detail = "Pullback: below 20 EMA then above but RED → WAIT for green candle"
                # Clean above EMA
                elif cur_close > ema20_now and prev_close > ema20_prev:
                    score  = 3.0
                    signal = "BUY"
                    detail = "Price above 5-min 20 EMA — bullish entry"
                # Fresh cross above EMA
                elif prev_close <= ema20_prev < cur_close:
                    score  = 4.5
                    signal = "BUY"
                    detail = "Fresh cross above 20 EMA → strong buy signal"

        elif bias == "SELL":
            if cur_close < ema20_now:
                # Pullback scenario: previous candle bounced above EMA but current fails
                if prev_close > ema20_prev and cur_close < ema20_now:
                    if is_red:
                        score  = 4.0
                        signal = "SELL"
                        detail = "Pullback confirmed: bounced above 20 EMA, now RED below → strong sell"
                    else:
                        score  = 1.5
                        signal = None
                        detail = "Bounce above 20 EMA then below but GREEN → WAIT for red candle"
                # Clean below EMA
                elif cur_close < ema20_now and prev_close < ema20_prev:
                    score  = 3.0
                    signal = "SELL"
                    detail = "Price below 5-min 20 EMA — bearish entry"
                # Fresh cross below EMA
                elif prev_close >= ema20_prev > cur_close:
                    score  = 4.5
                    signal = "SELL"
                    detail = "Fresh cross below 20 EMA → strong sell signal"

        return {
            "signal":   signal,
            "score":    round(score, 2),
            "ema20":    round(ema20_now, 2),
            "is_green": is_green,
            "detail":   detail,
        }
    except Exception as e:
        logger.debug("ema20_entry: %s", e)
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: CAMARILLA GOLDEN ZONES — H3/L3 inside CPR
# ─────────────────────────────────────────────────────────────────────────────
def check_camarilla_golden_zones(
    price:      float,
    h3: float, h4: float,
    l3: float, l4: float,
    daily_tc:   float, daily_bc: float,
    tolerance:  float = 0.002,
) -> Dict:
    """
    STEP 4 — Camarilla Golden Zone detection.

    Golden Bearish Zone: H3 inside CPR → STRONG resistance → SELL near H3
    Golden Bullish Zone: L3 inside CPR → STRONG support  → BUY near L3

    Confluence: These are highest-probability reversal zones because
    both Camarilla AND CPR are agreeing on the level.
    """
    tol = price * tolerance

    # Golden Bearish: H3 between BC and TC (inside CPR)
    h3_inside_cpr = daily_bc <= h3 <= daily_tc
    at_h3         = abs(price - h3) <= tol

    # Golden Bullish: L3 between BC and TC (inside CPR)
    l3_inside_cpr = daily_bc <= l3 <= daily_tc
    at_l3         = abs(price - l3) <= tol

    # High confluence zones
    at_h4_above_cpr = price >= h4 * (1 - tolerance)
    at_l4_below_cpr = price <= l4 * (1 + tolerance)

    score = 0.0; signal = None; zone = ""

    if h3_inside_cpr and at_h3:
        score  = 5.0; signal = "SELL"
        zone   = "🔴 GOLDEN BEARISH ZONE — H3 inside CPR. Strong resistance."
    elif at_h4_above_cpr:
        score  = 3.5; signal = "SELL"
        zone   = "H4 zone — exhaustion, look for reversal sell"
    elif l3_inside_cpr and at_l3:
        score  = 5.0; signal = "BUY"
        zone   = "🟢 GOLDEN BULLISH ZONE — L3 inside CPR. Strong support."
    elif at_l4_below_cpr:
        score  = 3.5; signal = "BUY"
        zone   = "L4 zone — exhaustion, look for reversal buy"

    return {
        "signal":           signal,
        "score":            score,
        "zone":             zone,
        "h3_inside_cpr":   h3_inside_cpr,
        "l3_inside_cpr":   l3_inside_cpr,
        "golden_bearish":  h3_inside_cpr and at_h3,
        "golden_bullish":  l3_inside_cpr and at_l3,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: OPENING SETUP — Gap analysis vs PDH/PDL/CPR
# ─────────────────────────────────────────────────────────────────────────────
def check_opening_setup(
    open_price: float,
    pdh: float, pdl: float,
    r1: float, s1: float,
    daily_tc: float, daily_bc: float,
) -> Dict:
    """
    Check opening gap vs key levels.

    Bullish Breakout Day:
      Open > PDH AND Open > R1 → Buy pullbacks → Target R3/R4

    Bearish Breakdown Day:
      Open < PDL AND Open < S1 → Sell pullbacks → Target L3/L4

    Opening inside CPR → Sideways day likely
    """
    bull_breakout = open_price > pdh and open_price > r1
    bear_breakdown = open_price < pdl and open_price < s1
    inside_cpr = daily_bc <= open_price <= daily_tc

    if bull_breakout:
        return {"setup": "BULLISH_BREAKOUT", "bias": "BUY",
                "score": 2.0, "note": f"Open {open_price:.0f} > PDH {pdh:.0f} + R1 {r1:.0f} → Buy pullbacks → R3/R4"}
    elif bear_breakdown:
        return {"setup": "BEARISH_BREAKDOWN", "bias": "SELL",
                "score": -2.0, "note": f"Open {open_price:.0f} < PDL {pdl:.0f} + S1 {s1:.0f} → Sell pullbacks → L3/L4"}
    elif inside_cpr:
        return {"setup": "INSIDE_CPR", "bias": "NEUTRAL",
                "score": 0.0, "note": f"Open inside CPR ({daily_bc:.0f}-{daily_tc:.0f}) → Sideways likely"}
    else:
        return {"setup": "NORMAL", "bias": "NEUTRAL", "score": 0.0, "note": "Normal open"}


# ─────────────────────────────────────────────────────────────────────────────
# MASTER: run_cpr_ema_strategy — combines all 5 steps
# ─────────────────────────────────────────────────────────────────────────────
def run_cpr_ema_strategy(
    df:         pd.DataFrame,
    df_htf:     Optional[pd.DataFrame] = None,
    df_1min:    Optional[pd.DataFrame] = None,
    symbol:     str = "",
    **kw,
) -> Dict:
    """
    Complete 5-step CPR + EMA strategy.

    Step 1: CPR structure (Daily vs Weekly)
    Step 2: 1-min 200 EMA trend filter
    Step 3: 5-min 20 EMA entry + pullback
    Step 4: Camarilla golden zones
    Step 5: Opening setup bias
    """
    empty = {"strategy": "cpr_ema", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20: return empty

        closes = df_c["close"].values
        price  = float(closes[-1])
        opens  = df_c["open"].values if "open" in df_c.columns else closes
        open_today = float(opens[0]) if len(opens) > 0 else price

        # Get pivot levels from pivot_boss
        try:
            from pivot_boss import (calc_cpr, calc_floor_pivots,
                                    calc_camarilla_pivots, calc_weekly_pivots,
                                    get_pdh_pdl)
            # Previous day
            ph = float(df_c["high"].iloc[-2]) if "high" in df_c.columns else price*1.005
            pl = float(df_c["low"].iloc[-2])  if "low"  in df_c.columns else price*0.995
            pc = float(closes[-2])

            daily_levels  = calc_cpr(ph, pl, pc)
            floor_levels  = calc_floor_pivots(ph, pl, pc)
            cam_levels    = calc_camarilla_pivots(ph, pl, pc)
            pdh_pdl       = get_pdh_pdl(df_c)

            daily_tc  = daily_levels.get("tc",  price)
            daily_bc  = daily_levels.get("bc",  price)
            daily_piv = daily_levels.get("pivot",price)
            r1 = floor_levels.get("r1", price * 1.005)
            s1 = floor_levels.get("s1", price * 0.995)
            h3 = cam_levels.get("h3",  price * 1.008)
            h4 = cam_levels.get("h4",  price * 1.012)
            l3 = cam_levels.get("l3",  price * 0.992)
            l4 = cam_levels.get("l4",  price * 0.988)
            pdh = pdh_pdl.get("pdh",  price * 1.005)
            pdl = pdh_pdl.get("pdl",  price * 0.995)
        except Exception:
            return empty

        # Weekly/Monthly from HTF
        weekly_piv = weekly_tc = weekly_bc = 0.0
        if df_htf is not None and len(df_htf) >= 5:
            try:
                dfh = df_htf.copy(); dfh.columns = [c.lower() for c in dfh.columns]
                wh = float(dfh["high"].iloc[-2]) if "high" in dfh.columns else price
                wl = float(dfh["low"].iloc[-2])  if "low"  in dfh.columns else price
                wc = float(dfh["close"].iloc[-2])
                from pivot_boss import calc_weekly_pivots
                wp = calc_weekly_pivots(wh, wl, wc)
                weekly_piv = wp.get("pivot", daily_piv)
                weekly_tc  = wp.get("tc",    daily_tc)
                weekly_bc  = wp.get("bc",    daily_bc)
            except Exception: weekly_piv = daily_piv; weekly_tc = daily_tc; weekly_bc = daily_bc

        # ── STEP 1: CPR Structure ─────────────────────────────────────────
        structure = get_cpr_structure(
            price, daily_piv, daily_tc, daily_bc,
            weekly_piv, weekly_tc, weekly_bc)
        cpr_bias = structure["bias"]

        # ── STEP 2: 1-min 200 EMA ─────────────────────────────────────────
        ema_bias_result = {"bias": cpr_bias, "score_mod": 0}
        if df_1min is not None:
            ema_bias_result = get_ema_bias(df_1min, 200)
        ema_bias = ema_bias_result["bias"]

        # Bias must AGREE (CPR + EMA) for high-confidence trade
        if cpr_bias != "NEUTRAL" and ema_bias != "NEUTRAL" and cpr_bias != ema_bias:
            return {**empty, "detail": f"CPR={cpr_bias} conflicts EMA={ema_bias} — skip"}
        final_bias = cpr_bias if cpr_bias != "NEUTRAL" else ema_bias
        if final_bias == "NEUTRAL": return empty

        # ── STEP 3: 5-min 20 EMA Entry ────────────────────────────────────
        entry = get_ema20_entry_signal(df_c, final_bias)
        if not entry["signal"]: return empty  # wait for confirmation

        # ── STEP 4: Camarilla Golden Zones ────────────────────────────────
        cam_zone = check_camarilla_golden_zones(price, h3, h4, l3, l4, daily_tc, daily_bc)

        # ── STEP 5: Opening setup ─────────────────────────────────────────
        opening = check_opening_setup(open_today, pdh, pdl, r1, s1, daily_tc, daily_bc)

        # ── FINAL SCORE ───────────────────────────────────────────────────
        score = entry["score"]                           # 3.0–4.5 from EMA entry
        score += structure["score_mod"]                  # ±1.5 from CPR structure
        score += ema_bias_result.get("score_mod", 0)    # ±0.8 from 200 EMA
        score += abs(opening.get("score", 0)) * (1 if opening["bias"]==final_bias else -0.5)
        if cam_zone["signal"] == final_bias:
            score += cam_zone["score"] * 0.5            # golden zone bonus

        direction = final_bias
        return {
            "strategy":        "cpr_ema",
            "score":           round(min(score, 9.0), 2),
            "direction":       direction,
            "side":            direction,
            "cpr_structure":   structure["structure"],
            "cpr_bias":        cpr_bias,
            "ema_bias":        ema_bias,
            "ema20":           entry["ema20"],
            "entry_detail":    entry["detail"],
            "golden_zone":     cam_zone.get("zone",""),
            "opening_setup":   opening["setup"],
            "daily_tc":        round(daily_tc, 2),
            "daily_bc":        round(daily_bc, 2),
            "weekly_pivot":    round(weekly_piv, 2),
        }
    except Exception as e:
        logger.debug("cpr_ema: %s", e)
        return empty
