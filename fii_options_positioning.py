"""
fii_options_positioning.py — FII Options Positioning Score

FII writing calls = they expect ceiling → BEARISH
FII writing puts  = they expect support → BULLISH

This is more informative than total PCR because it shows
WHERE institutional money is positioned, not just how much.

Sources:
  NSE participant-wise OI data (daily)
  Option chain strike-level OI
"""
from __future__ import annotations
import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def get_fii_options_positioning(symbol: str = "NIFTY") -> Dict:
    """
    Analyse FII/DII net positions in calls vs puts.
    
    FII net call short (writing calls) → bearish positioning
    FII net put short  (writing puts)  → bullish positioning
    
    Returns score modifier and positioning description.
    """
    try:
        from participant_oi import get_participant_data
        data = get_participant_data()
        if not data:
            return {"score_modifier": 0.0, "positioning": "UNKNOWN"}

        fii = data.get("FII", {})
        # Calls: positive = long (bought), negative = short (written)
        call_long  = float(fii.get("call_long",  0) or 0)
        call_short = float(fii.get("call_short", 0) or 0)
        put_long   = float(fii.get("put_long",   0) or 0)
        put_short  = float(fii.get("put_short",  0) or 0)

        # Net positions
        net_calls  = call_long  - call_short   # positive = net long calls (bullish)
        net_puts   = put_short  - put_long     # positive = net short puts (bullish)
        net_score  = net_calls + net_puts      # combined positioning score

        # Normalise to -5 to +5 range
        max_oi = max(abs(net_score), 1)
        norm   = net_score / max_oi * 2.0
        score_modifier = float(np.clip(norm, -2.0, 2.0))

        if net_calls > 0 and net_puts > 0:
            positioning = "STRONGLY BULLISH — FII long calls + short puts"
        elif net_calls > 0:
            positioning = "BULLISH — FII long calls"
        elif net_puts > 0:
            positioning = "BULLISH — FII short puts (put writing)"
        elif net_calls < 0 and net_puts < 0:
            positioning = "STRONGLY BEARISH — FII short calls + long puts"
        elif net_calls < 0:
            positioning = "BEARISH — FII short calls (call writing)"
        else:
            positioning = "NEUTRAL"

        return {
            "score_modifier": round(score_modifier, 2),
            "positioning":    positioning,
            "net_calls":      round(net_calls, 0),
            "net_puts":       round(net_puts, 0),
            "fii_call_long":  round(call_long, 0),
            "fii_call_short": round(call_short, 0),
            "fii_put_long":   round(put_long, 0),
            "fii_put_short":  round(put_short, 0),
        }
    except Exception as e:
        logger.debug("fii_options: %s", e)
        return {"score_modifier": 0.0, "positioning": "UNAVAILABLE"}


def get_strike_level_fii(
    chain: List[Dict],
    spot:  float,
    window_pct: float = 0.03,
) -> Dict:
    """
    Find strikes where FII has highest concentration.
    High put OI near spot = put writing = support level.
    High call OI near spot = call writing = resistance level.
    """
    if not chain or spot <= 0:
        return {}

    window = spot * window_pct
    near_strikes = [row for row in chain
                    if abs(float(row.get("strikePrice",0)) - spot) <= window]
    if not near_strikes:
        return {}

    put_oi_near  = sum(float(r.get("PE",{}).get("openInterest",0) or 0) for r in near_strikes)
    call_oi_near = sum(float(r.get("CE",{}).get("openInterest",0) or 0) for r in near_strikes)

    pcr_near = put_oi_near / max(call_oi_near, 1)
    if pcr_near > 1.5:
        bias = "BULLISH (high put OI near spot = put writers defending)"
        score_mod = 0.8
    elif pcr_near < 0.7:
        bias = "BEARISH (high call OI near spot = call writers capping)"
        score_mod = -0.8
    else:
        bias = "NEUTRAL"
        score_mod = 0.0

    return {
        "pcr_near_spot":  round(pcr_near, 3),
        "put_oi_near":    round(put_oi_near, 0),
        "call_oi_near":   round(call_oi_near, 0),
        "bias":           bias,
        "score_modifier": score_mod,
    }


def fii_positioning_summary() -> str:
    """Telegram-ready FII options positioning."""
    pos = get_fii_options_positioning()
    lines = [
        "📊 <b>FII OPTIONS POSITIONING</b>",
        f"   Overall: {pos.get('positioning','UNKNOWN')}",
        f"   Score modifier: {pos.get('score_modifier',0):+.2f}",
        "",
        f"   Call long:  {pos.get('fii_call_long',0):>10,.0f}",
        f"   Call short: {pos.get('fii_call_short',0):>10,.0f}",
        f"   Put long:   {pos.get('fii_put_long',0):>10,.0f}",
        f"   Put short:  {pos.get('fii_put_short',0):>10,.0f}",
    ]
    return "\n".join(lines)
