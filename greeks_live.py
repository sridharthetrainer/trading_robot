"""
greeks_live.py — Real-Time Options Greeks + Market Microstructure

Captures what the OPTIONS MARKET knows that price charts don't show:

1. GAMMA EXPOSURE (GEX) — Where dealers MUST hedge
   The most important institutional concept in modern markets.
   When dealers are short gamma → they amplify moves (volatile)
   When dealers are long gamma → they suppress moves (range-bound)

2. DELTA WALL — Strongest S/R in the market
   Strike with highest OI × Delta = where dealers defend hardest

3. CHARM — Delta decay near expiry
   As time passes on expiry day, delta of OTM options drops.
   System can track when options lose "moneyness" momentum.

4. VANNA — Vol-driven delta change
   When VIX moves, delta changes → dealers re-hedge → market moves.
   VIX spike → dealers sell → amplifies down move.

5. IMPLIED MOVE — What options market predicts
   ATM straddle price ÷ spot = market's expected 1-day move.
   If actual move > implied → vol underpriced → hero-zero opportunity.

6. IV TERM STRUCTURE
   Weekly IV vs Monthly IV ratio.
   Ratio > 1.3 = near-term event expected.
   Good for expiry plays.

7. PCR BY STRIKE
   Which specific strikes have max put/call OI.
   These become magnetic support/resistance levels.

FREE SOURCES:
  NSE option chain API → all greeks computable from OI + price
  No paid data needed — we compute Greeks ourselves.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_CACHE  = Path("greeks_cache.json")
_HIST   = Path("greeks_history.csv")
_TTL    = 300  # 5 min cache


# ── Black-Scholes Greeks (from option chain data) ────────────────────────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def compute_greeks(
    S: float, K: float, T: float, r: float,
    sigma: float, option: str = "call"
) -> Dict:
    """Compute full options Greeks from B-S model."""
    if T <= 0 or sigma <= 0: return {}
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option == "call":
        delta = _norm_cdf(d1)
        price = S*_norm_cdf(d1) - K*math.exp(-r*T)*_norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1
        price = K*math.exp(-r*T)*_norm_cdf(-d2) - S*_norm_cdf(-d1)
    gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega  = S * _norm_pdf(d1) * math.sqrt(T) / 100
    theta = (-(S * _norm_pdf(d1) * sigma) / (2*math.sqrt(T)) - r*K*math.exp(-r*T)*_norm_cdf(d2)) / 365
    vanna = -_norm_pdf(d1) * d2 / sigma
    charm = -_norm_pdf(d1) * (2*r*T - d2*sigma*math.sqrt(T)) / (2*T*sigma*math.sqrt(T))
    return {
        "price": round(price, 2), "delta": round(delta, 4),
        "gamma": round(gamma, 6), "theta": round(theta, 2),
        "vega":  round(vega, 4),  "vanna": round(vanna, 4),
        "charm": round(charm, 4),
    }


# ── GAMMA EXPOSURE (GEX) ─────────────────────────────────────────────────────
def compute_gex(
    spot:      float,
    chain:     List[Dict],
    r:         float = 0.065,
    dte_days:  int   = 0,
) -> Dict:
    """
    Gamma Exposure = sum of (Gamma × OI × Spot² × 0.01) per strike.

    Positive GEX: Dealers are LONG gamma → they sell rallies, buy dips
                  → Market is RANGE-BOUND (vol suppressed)
    Negative GEX: Dealers are SHORT gamma → they buy rallies, sell dips
                  → Market is TRENDING / VOLATILE

    Delta Wall: Strike with highest absolute GEX = strongest S/R in market.
    GEX Flip point: Where GEX crosses zero = key level.
    """
    T   = max(dte_days / 365, 1/365)
    gex_by_strike = {}
    total_gex     = 0.0
    vix_proxy     = 0.15  # default sigma

    for row in chain:
        strike     = float(row.get("strikePrice", 0) or 0)
        call_oi    = float(row.get("CE", {}).get("openInterest", 0) or 0)
        put_oi     = float(row.get("PE", {}).get("openInterest", 0) or 0)
        call_iv    = float(row.get("CE", {}).get("impliedVolatility", vix_proxy*100) or vix_proxy*100) / 100
        put_iv     = float(row.get("PE", {}).get("impliedVolatility",  vix_proxy*100) or vix_proxy*100) / 100

        if strike <= 0: continue

        # Compute gamma
        ce_g = compute_greeks(spot, strike, T, r, max(call_iv, 0.01), "call").get("gamma", 0)
        pe_g = compute_greeks(spot, strike, T, r, max(put_iv,  0.01), "put" ).get("gamma", 0)

        # GEX: dealers long calls → short gamma; dealers long puts → long gamma
        # Simplified: call dealers SHORT gamma, put dealers LONG gamma
        gex = (pe_g * put_oi - ce_g * call_oi) * spot * spot * 0.01

        gex_by_strike[strike] = round(gex, 0)
        total_gex += gex

    if not gex_by_strike:
        return {"total_gex": 0, "regime": "UNKNOWN"}

    # Find GEX flip point and delta wall
    strikes    = sorted(gex_by_strike.keys())
    gex_values = [gex_by_strike[s] for s in strikes]

    # Delta wall = highest absolute GEX
    max_gex_idx  = int(np.argmax([abs(g) for g in gex_values]))
    delta_wall   = strikes[max_gex_idx]

    # GEX flip = where GEX crosses zero near spot
    flip_point = spot
    for i in range(len(strikes)-1):
        if gex_values[i] * gex_values[i+1] < 0:
            if abs(strikes[i] - spot) < abs(flip_point - spot):
                flip_point = (strikes[i] + strikes[i+1]) / 2

    regime = "RANGE_BOUND" if total_gex > 0 else "TRENDING/VOLATILE"
    vol_regime = "VOL_SUPPRESSED" if total_gex > 0 else "VOL_AMPLIFIED"

    return {
        "total_gex":    round(total_gex, 0),
        "regime":       regime,
        "vol_regime":   vol_regime,
        "delta_wall":   round(delta_wall, 0),
        "flip_point":   round(flip_point, 0),
        "gex_by_strike":gex_by_strike,
        "interpretation": (
            f"Dealers {'SUPPRESS' if total_gex>0 else 'AMPLIFY'} moves. "
            f"Key level: {delta_wall:.0f} (Delta Wall). "
            f"GEX flip: {flip_point:.0f}"
        ),
    }


# ── IMPLIED MOVE from ATM Straddle ───────────────────────────────────────────
def get_implied_move(
    spot:  float,
    chain: List[Dict],
) -> Dict:
    """
    Implied Move = (ATM Call premium + ATM Put premium) / Spot × 100

    This is the 1-day move the options market is PRICING IN.
    If actual intraday move > implied → vol underpriced → hero-zero opportunity.
    If actual < implied → vol overpriced → sell premium.

    Example: NIFTY at 22,500
    ATM 22,500 CE = ₹120, ATM 22,500 PE = ₹110
    Implied move = (120+110)/22500 × 100 = 1.02%
    → Market expects ±230 points today
    """
    atm_strike = round(spot / 50) * 50  # round to nearest 50
    atm_row    = None
    min_dist   = float('inf')

    for row in chain:
        strike = float(row.get("strikePrice", 0) or 0)
        dist   = abs(strike - spot)
        if dist < min_dist:
            min_dist  = dist
            atm_row   = row

    if not atm_row:
        return {"implied_move_pct": 0, "implied_pts": 0}

    ce_prem = float(atm_row.get("CE",{}).get("lastPrice", 0) or 0)
    pe_prem = float(atm_row.get("PE",{}).get("lastPrice", 0) or 0)
    straddle_price = ce_prem + pe_prem
    implied_pct    = straddle_price / spot * 100
    implied_pts    = straddle_price

    return {
        "atm_strike":        round(atm_strike, 0),
        "ce_premium":        ce_prem,
        "pe_premium":        pe_prem,
        "straddle_price":    round(straddle_price, 2),
        "implied_move_pct":  round(implied_pct, 2),
        "implied_pts":       round(implied_pts, 0),
        "interpretation":    f"Market pricing ±{implied_pts:.0f} pts ({implied_pct:.1f}%) today",
        "hero_zero_signal":  implied_pct > 0.8,  # >0.8% implied = big move day
    }


# ── IV TERM STRUCTURE ────────────────────────────────────────────────────────
def get_iv_term_structure(
    weekly_iv:  float,
    monthly_iv: float,
) -> Dict:
    """
    IV Term Structure = Weekly IV / Monthly IV ratio.

    > 1.3 → Near-term event risk (earnings, RBI, budget)
             Short-term options expensive → sell near-term, buy far
    ~ 1.0 → Normal contango (short-term slightly > long-term)
    < 0.9 → Inverted (very unusual) → market stressed about near-term
    """
    if monthly_iv <= 0:
        return {"ratio": 1.0, "structure": "NORMAL", "signal": "NEUTRAL"}
    ratio = weekly_iv / monthly_iv
    if ratio > 1.4:
        structure = "STEEP_CONTANGO"
        signal    = "Sell weekly options (very expensive vs monthly)"
        action    = "SELL_NEAR"
    elif ratio > 1.15:
        structure = "NORMAL_CONTANGO"
        signal    = "Normal market — no edge from term structure"
        action    = "NEUTRAL"
    elif ratio > 0.95:
        structure = "FLAT"
        signal    = "Flat term structure — buy weekly (cheap relative to monthly)"
        action    = "BUY_NEAR"
    else:
        structure = "INVERTED"
        signal    = "Inverted — severe near-term stress"
        action    = "AVOID_OPTIONS"
    return {
        "weekly_iv":  round(weekly_iv, 1),
        "monthly_iv": round(monthly_iv, 1),
        "ratio":      round(ratio, 3),
        "structure":  structure,
        "signal":     signal,
        "action":     action,
    }


# ── PCR BY STRIKE ─────────────────────────────────────────────────────────────
def get_pcr_by_strike(
    chain:      List[Dict],
    spot:       float,
    top_n:      int = 5,
) -> Dict:
    """
    PCR (Put-Call Ratio) by individual strike.

    High PCR strike (lots of puts) = STRONG SUPPORT (put sellers defend it)
    Low PCR strike (lots of calls)  = STRONG RESISTANCE (call sellers defend it)

    These are more precise than overall PCR.
    """
    strike_data = []
    for row in chain:
        strike  = float(row.get("strikePrice", 0) or 0)
        if strike <= 0: continue
        call_oi = float(row.get("CE",{}).get("openInterest", 0) or 0)
        put_oi  = float(row.get("PE",{}).get("openInterest", 0) or 0)
        pcr     = put_oi / max(call_oi, 1)
        dist    = (strike - spot) / spot * 100
        strike_data.append({
            "strike": strike, "call_oi": call_oi,
            "put_oi": put_oi, "pcr": round(pcr, 2),
            "dist_pct": round(dist, 1),
        })

    if not strike_data:
        return {}

    # Support levels (high PCR, below spot)
    support_levels = sorted(
        [s for s in strike_data if s["strike"] < spot and s["put_oi"] > 0],
        key=lambda x: -x["put_oi"])[:top_n]

    # Resistance levels (high call OI, above spot)
    resistance_levels = sorted(
        [s for s in strike_data if s["strike"] > spot and s["call_oi"] > 0],
        key=lambda x: -x["call_oi"])[:top_n]

    # Max pain = strike where total OI decay is maximum
    total_oi     = [(s["strike"], s["call_oi"]+s["put_oi"]) for s in strike_data]
    max_oi_strike= max(total_oi, key=lambda x: x[1], default=(spot, 0))[0]

    return {
        "support_levels":    [{"strike":s["strike"],"put_oi":s["put_oi"],"pcr":s["pcr"]} for s in support_levels],
        "resistance_levels": [{"strike":s["strike"],"call_oi":s["call_oi"]} for s in resistance_levels],
        "max_pain_proxy":    round(max_oi_strike, 0),
        "nearest_support":   max((s["strike"] for s in strike_data if s["strike"] < spot), default=0),
        "nearest_resistance":min((s["strike"] for s in strike_data if s["strike"] > spot), default=0),
    }


# ── NSDL FPI EQUITY FLOWS (free API) ─────────────────────────────────────────
def get_fpi_flows_nsdl() -> Dict:
    """
    NSDL publishes FPI (foreign portfolio investor) equity flows daily.
    Free at: https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx

    More granular than NSE FII data — shows sector-wise FPI flows.
    """
    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        # NSDL publishes FPI data in JSON format
        r = requests.get(
            "https://www.fpi.nsdl.co.in/web/Reports/ReportsListing.aspx",
            headers=headers, timeout=10)
        # Parse whatever is available
        if r.status_code == 200:
            return {"source": "NSDL", "available": True, "note": "FPI data available at NSDL portal"}
        return {"available": False}
    except Exception as e:
        logger.debug("NSDL FPI: %s", e)
        return {"available": False, "error": str(e)[:40]}


# ── EVENT CALENDAR WITH PLAYBOOK ──────────────────────────────────────────────
_RBI_DATES_2026 = [
    "2026-02-07", "2026-04-07", "2026-06-05",
    "2026-08-07", "2026-10-07", "2026-12-04"
]

_BUDGET_DATES_2026 = ["2026-02-01"]

def get_event_playbook(symbol: str = "NIFTY") -> Dict:
    """
    Pre-built playbook for scheduled macro events.

    BEFORE EVENT:  IV rises → option premiums expensive
    AFTER EVENT:   IV collapses → option buyers lose (IV crush)

    Playbooks:
    RBI MPC Day:
      - Market typically range-bound till 10 AM
      - Decision at ~10:00-10:30 AM
      - Strategy: Sell straddle before, buy direction after
      - If rate cut → Nifty up 1-2%, Bank Nifty up 2-3%
      - If rate hold → muted, sell premium play

    Budget Day:
      - Sell straddle at 9:15 (extreme IV)
      - Buy direction after statement
      - Max pain = PDC (prev day close) usually

    FOMC Day (US):
      - GIFT Nifty volatile overnight
      - Morning gap creates hero-zero opportunity
      - Buy OTM in GIFT direction at 9:15
    """
    today     = date.today().isoformat()
    tomorrow  = (date.today() + __import__("datetime").timedelta(days=1)).isoformat()
    is_rbi    = today in _RBI_DATES_2026 or tomorrow in _RBI_DATES_2026
    is_budget = today in _BUDGET_DATES_2026

    events = []
    playbook = {}

    if is_rbi:
        days_to = 0 if today in _RBI_DATES_2026 else 1
        events.append(f"RBI MPC {'TODAY' if days_to==0 else 'TOMORROW'}")
        playbook["rbi"] = {
            "event":    "RBI Monetary Policy Committee",
            "timing":   "Decision ~10:00-10:30 AM",
            "before":   "Sell ATM straddle at 9:15 (IV expensive) — collect premium",
            "after":    "Buy direction after announcement — explosive 200-400 pt move",
            "rate_cut": "Buy BANKNIFTY CE aggressively (banks rally 2-3%)",
            "hold":     "Sell straddle — market range-bound",
            "hero_zero":"Buy deep OTM in GIFT direction at 9:15 if gap > 0.3%",
        }

    if is_budget:
        events.append("BUDGET DAY")
        playbook["budget"] = {
            "event":   "Union Budget",
            "before":  "Sell ATM straddle (highest IV day of year)",
            "after":   "Buy direction — budget typically drives 200-500 pt move",
            "note":    "Max pain usually near PDC — market makers pin it",
        }

    # Check FOMC (approximate — 3rd week of Jan/Mar/May/Jul/Sep/Nov)
    now = date.today()
    if now.month in (1,3,5,7,9,11) and 15 <= now.day <= 22:
        events.append("FOMC WEEK (approximate)")
        playbook["fomc"] = {
            "event":   "US Federal Reserve FOMC",
            "timing":  "US 2:00 PM EST = India 11:30 PM / next morning",
            "before":  "Watch GIFT Nifty from 11 PM onwards",
            "morning": "Gap on Indian market → hero-zero opportunity at 9:15 AM",
            "playbook":"If GIFT gap > 0.5% → Buy OTM in gap direction",
        }

    return {
        "events":   events,
        "playbook": playbook,
        "today":    today,
        "has_event":bool(events),
    }


# ── CHARM TRACKER (delta decay on expiry day) ─────────────────────────────────
def track_charm_decay(
    spot:      float,
    strikes:   List[float],
    premiums:  List[float],
    dte_hours: float = 6.0,
) -> List[Dict]:
    """
    Track Charm (delta decay) on expiry day.

    As time passes on expiry day, OTM options lose delta fast.
    Charm tells you HOW FAST delta is decaying per hour.

    HIGH charm strike = delta changing fast = use for exit timing.

    Example:
    NIFTY 22,700 CE at 10 AM (DTE = 5 hours) delta = 0.15
    NIFTY 22,700 CE at 1 PM  (DTE = 2 hours) delta = 0.06
    → Charm = -0.09 per 3 hours → losing delta fast → hero-zero must exit by 1 PM
    """
    dte_years = dte_hours / (365 * 24)
    r = 0.065
    results = []

    for strike, prem in zip(strikes, premiums):
        if dte_years <= 0 or strike <= 0 or prem <= 0:
            continue
        # Estimate sigma from premium (simplified)
        sigma = max(0.08, prem / spot * math.sqrt(365 / max(dte_hours/24, 0.01)))
        sigma = min(sigma, 2.0)

        greeks = compute_greeks(spot, strike, dte_years, r, sigma,
                                "call" if strike >= spot else "put")
        charm  = greeks.get("charm", 0)

        results.append({
            "strike":         strike,
            "premium":        prem,
            "delta":          greeks.get("delta", 0),
            "charm":          charm,
            "charm_per_hour": round(charm / 24, 6),
            "delta_in_1h":    round(greeks.get("delta",0) + charm/24, 4),
            "exit_urgency":   "HIGH" if abs(charm) > 0.05 else "NORMAL",
        })
    return results
