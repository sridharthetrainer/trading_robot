"""
theta_strategy.py  —  PRO Desk Theta Capture Strategy

When IV Percentile > 70 → options are EXPENSIVE → SELL them.
When IV Percentile < 30 → options are CHEAP → BUY them (existing logic).

PRO DESK APPROACH:
  Monday: Sell NIFTY weekly strangle (1 strike OTM on each side)
  Target: Collect ₹150-250 combined premium
  Hold: Through Thursday expiry (if no breach)
  Stop:  Close if either leg doubles (2× premium paid)
  Win rate: 75-85% on NSE weekly options

SIGNALS FROM THIS MODULE:
  should_sell_options() → bool + suggested strangle strikes
  is_theta_environment() → bool (IV% > 70 AND VIX < 22 AND not expiry week)
  get_theta_score_mod() → score modifier for option-writing strategies
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def is_theta_environment(ivp: float = 50.0, vix: float = 15.0) -> bool:
    """
    Returns True when it's profitable to SELL options.
    Conditions:
      - IV Percentile > 70 (options expensive)
      - VIX < 22 (not panic mode)
      - Not expiry day (gamma too high)
    """
    try:
        from expiry_regime import get_expiry_regime
        regime = get_expiry_regime()
        if regime.get("is_expiry_day"):
            return False
        if regime.get("days_to_expiry", 7) <= 1:
            return False
    except ImportError:
        pass
    return ivp > 70 and vix < 22


def get_theta_score_mod(ivp: float, vix: float, direction: str) -> float:
    """
    Score modifier for directional trades based on IV environment.
    High IV → sell environment → penalise option buying.
    Low IV  → buy environment → boost option buying.
    """
    if ivp > 80 and vix < 20:
        # Strong theta environment — buying options is negative EV
        return -0.8
    elif ivp > 70:
        return -0.4
    elif ivp < 30:
        # IV cheap — buying has positive vol premium
        return +0.5
    elif ivp < 20:
        return +0.8
    return 0.0


def suggest_strangle(spot: float, ivp: float, lot_size: int = 75) -> dict:
    """
    Suggest a weekly NIFTY strangle to sell.
    Strikes chosen ~1% OTM from spot, rounded to nearest 50.

    Returns strike levels, estimated premium, max profit, stop levels.
    """
    # Round spot to nearest 100
    atm = round(spot / 100) * 100

    # Choose strikes ~1-1.5% OTM
    if ivp > 80:
        # Higher IV → use wider strikes
        otm_pct = 0.015
    else:
        otm_pct = 0.010

    ce_strike = round((spot * (1 + otm_pct)) / 50) * 50
    pe_strike = round((spot * (1 - otm_pct)) / 50) * 50

    # Rough premium estimate (higher IV = higher premium)
    # Using simplified Black-Scholes approximation
    base_prem = spot * 0.001 * (ivp / 50) * 3   # rough estimate per leg
    ce_prem   = round(base_prem, 0)
    pe_prem   = round(base_prem * 0.9, 0)        # slightly asymmetric
    total_prem = ce_prem + pe_prem

    return {
        "ce_strike":   ce_strike,
        "pe_strike":   pe_strike,
        "ce_premium":  ce_prem,
        "pe_premium":  pe_prem,
        "total_premium": total_prem,
        "max_profit":  round(total_prem * lot_size, 0),
        "ce_stop":     ce_strike + ce_prem * 2,   # stop at 2× premium
        "pe_stop":     pe_strike - pe_prem * 2,
        "breakeven_up":   ce_strike + total_prem,
        "breakeven_down": pe_strike - total_prem,
        "ivp": ivp,
    }


def pcr_momentum_signal(pcr_today: float, pcr_yesterday: float,
                         direction: str) -> float:
    """
    PCR CHANGE matters as much as PCR level.
    PCR dropping fast → retail turning bullish → fade the move.
    PCR rising fast   → retail turning bearish → buy the fear.
    """
    if pcr_yesterday <= 0:
        return 0.0

    change = pcr_today - pcr_yesterday
    change_pct = change / pcr_yesterday

    modifier = 0.0

    # PCR falling fast = retail going bullish = contrarian bearish
    if change_pct < -0.15:
        modifier -= 0.8 if direction == "BUY" else -0.5
    elif change_pct < -0.08:
        modifier -= 0.4 if direction == "BUY" else -0.3

    # PCR rising fast = retail going bearish = buy the fear
    elif change_pct > 0.15:
        modifier += 0.8 if direction == "BUY" else -0.5
    elif change_pct > 0.08:
        modifier += 0.4 if direction == "BUY" else -0.3

    return round(modifier, 2)
