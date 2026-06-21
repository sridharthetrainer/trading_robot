"""
strike_pcr.py  —  Strike-level PCR map from NSE option chain.

TOTAL PCR is noisy. Strike-level PCR reveals where market makers defend.
If 22500 CE has 10× the OI of 22500 PE → max pain is at 22500.
Market makers will defend that level aggressively on expiry.

SIGNALS:
  Nearest resistance = strike with highest CE OI above spot
  Nearest support    = strike with highest PE OI below spot
  Max pain strike    = where total OI loss is minimised
  PCR at ATM         = bearish if PCR < 0.7, bullish if PCR > 1.3
"""
from __future__ import annotations
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def build_strike_pcr_map(option_chain: dict, spot: float) -> dict:
    """
    Build strike-level PCR map from option chain data.

    Args:
        option_chain: raw NSE option chain dict
        spot:         current underlying price

    Returns:
        {
          "pcr_atm":          float,     # PCR at ATM strike ±1
          "max_pain":         float,     # max pain strike price
          "resistance":       float,     # nearest CE OI wall above spot
          "support":          float,     # nearest PE OI wall below spot
          "signal":           "BULLISH" | "BEARISH" | "NEUTRAL",
          "score_mod":        float,     # score modifier for current direction
          "ce_oi_by_strike":  dict,
          "pe_oi_by_strike":  dict,
        }
    """
    empty = {"pcr_atm": 1.0, "max_pain": spot, "resistance": 0.0,
             "support": 0.0, "signal": "NEUTRAL", "score_mod": 0.0,
             "ce_oi_by_strike": {}, "pe_oi_by_strike": {}}
    try:
        # Parse option chain — handle both NSE API formats
        records = (option_chain.get("records", {}).get("data", [])
                   or option_chain.get("data", []))
        if not records:
            return empty

        ce_oi: Dict[float, float] = {}
        pe_oi: Dict[float, float] = {}

        for row in records:
            strike = float(row.get("strikePrice", 0))
            if strike <= 0:
                continue
            ce = row.get("CE", {}) or {}
            pe = row.get("PE", {}) or {}
            if ce:
                ce_oi[strike] = float(ce.get("openInterest", 0) or 0)
            if pe:
                pe_oi[strike] = float(pe.get("openInterest", 0) or 0)

        if not ce_oi or not pe_oi:
            return empty

        all_strikes = sorted(set(ce_oi) | set(pe_oi))

        # PCR at ATM (nearest 3 strikes)
        atm = min(all_strikes, key=lambda s: abs(s - spot))
        atm_idx = all_strikes.index(atm)
        atm_range = all_strikes[max(0, atm_idx-1): atm_idx+2]
        atm_ce = sum(ce_oi.get(s, 0) for s in atm_range)
        atm_pe = sum(pe_oi.get(s, 0) for s in atm_range)
        pcr_atm = round(atm_pe / atm_ce, 3) if atm_ce > 0 else 1.0

        # Resistance = strike above spot with highest CE OI
        above = {s: v for s, v in ce_oi.items() if s > spot}
        resistance = max(above, key=above.get) if above else 0.0

        # Support = strike below spot with highest PE OI
        below = {s: v for s, v in pe_oi.items() if s < spot}
        support = max(below, key=below.get) if below else 0.0

        # Max pain = strike where total ITM loss is minimum
        max_pain = _calc_max_pain(ce_oi, pe_oi, all_strikes, spot)

        # Signal
        if pcr_atm < 0.7:
            signal   = "BEARISH"
            score_ce = -0.5   # too many calls → bearish
            score_pe = +0.5
        elif pcr_atm > 1.3:
            signal   = "BULLISH"
            score_ce = +0.5
            score_pe = -0.5
        else:
            signal   = "NEUTRAL"
            score_ce = 0.0
            score_pe = 0.0

        # Proximity to walls
        dist_resist = (resistance - spot) / spot if resistance > spot else 1.0
        dist_support = (spot - support)  / spot if support < spot else 1.0

        # Near resistance — penalise BUY
        if 0 < dist_resist < 0.005:    score_ce -= 1.0
        # Near support — penalise SELL
        if 0 < dist_support < 0.005:   score_pe -= 1.0

        return {
            "pcr_atm":         pcr_atm,
            "max_pain":        max_pain,
            "resistance":      resistance,
            "support":         support,
            "signal":          signal,
            "score_buy":       round(score_ce, 2),
            "score_sell":      round(score_pe, 2),
            "ce_oi_by_strike": {str(k): v for k, v in ce_oi.items()},
            "pe_oi_by_strike": {str(k): v for k, v in pe_oi.items()},
        }
    except Exception as e:
        logger.debug("Strike PCR error: %s", e)
        return empty


def _calc_max_pain(ce_oi: dict, pe_oi: dict,
                   strikes: list, spot: float) -> float:
    """Max pain = strike where total ITM option loss is minimum."""
    try:
        min_pain = float("inf")
        pain_strike = spot
        for test_strike in strikes:
            # Total loss to option sellers at expiry = test_strike
            ce_pain = sum(max(0, test_strike - s) * oi
                          for s, oi in ce_oi.items() if s < test_strike)
            pe_pain = sum(max(0, s - test_strike) * oi
                          for s, oi in pe_oi.items() if s > test_strike)
            total   = ce_pain + pe_pain
            if total < min_pain:
                min_pain   = total
                pain_strike = test_strike
        return float(pain_strike)
    except Exception:
        return spot


def get_score_mod(pcr_map: dict, direction: str) -> float:
    """Score modifier for a signal direction based on strike PCR."""
    if direction == "BUY":
        return float(pcr_map.get("score_buy", 0.0))
    elif direction == "SELL":
        return float(pcr_map.get("score_sell", 0.0))
    return 0.0
