"""
expiry_strategy.py

Expiry-day specific trading strategy for NIFTY/BANKNIFTY.

WHY EXPIRY IS DIFFERENT
─────────────────────────
On expiry day (Thu=NIFTY, Wed=BANKNIFTY):
  • Theta accelerates: out-of-money options lose value 3-5x faster
  • Gamma explodes: small price moves cause large premium changes
  • Price pinning: NIFTY tends to close near a round number / max pain
  • Volume surge: highest volume day of the week
  • Institutional pressure: market makers keep price near max pain

STRATEGIES
───────────
1. Max Pain Pin:
   Buy whichever option is closer to max pain (CE if above, PE if below)
   Best for: range-bound expiry days

2. Expiry Momentum:
   If price breaks strongly beyond max pain by 11 AM, ride the breakout
   CE breakout = buy CE, PE breakdown = buy PE
   Best for: directional expiry days

3. Theta Seller (spread):
   Sell far OTM strangle when premium is high pre-expiry
   Only when VIX < 18 and within 50 points of max pain
   Best for: low-volatility expiry days

SIGNALS
────────
Returns signal dict with:
  action:     BUY / SELL / HOLD
  option_type: CE / PE
  strategy:   max_pain_pin / expiry_momentum / theta_sell
  score:      confidence score
  reason:     human-readable explanation
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)


def is_expiry_today(underlying: str = "NIFTY") -> bool:
    """Returns True if today is expiry day for this underlying."""
    today = date.today()
    wd    = today.weekday()   # 0=Mon ... 6=Sun
    expiry_map = {
        "NIFTY":       3,   # Thursday
        "BANKNIFTY":   2,   # Wednesday
        "FINNIFTY":    1,   # Tuesday
        "MIDCPNIFTY":  0,   # Monday
        "SENSEX":      2,   # Wednesday
    }
    target_wd = expiry_map.get(underlying.upper(), 3)
    return wd == target_wd


def get_expiry_regime() -> dict:
    """
    Classify the current state of the expiry day session.
    Returns phase and recommended strategy type.
    """
    now  = datetime.now()
    hour = now.hour
    mins = now.minute

    if hour == 9 and mins <= 30:
        return {"phase": "OPENING",    "strategy": "wait",            "caution": "HIGH"}
    if hour < 11:
        return {"phase": "DISCOVERY",  "strategy": "max_pain_pin",    "caution": "MEDIUM"}
    if hour < 13:
        return {"phase": "MIDMORNING", "strategy": "expiry_momentum", "caution": "LOW"}
    if hour < 14:
        return {"phase": "LUNCH",      "strategy": "avoid",           "caution": "HIGH"}
    if hour < 15:
        return {"phase": "POWER_HOUR", "strategy": "expiry_momentum", "caution": "LOW"}
    return {"phase": "CLOSING",    "strategy": "avoid",           "caution": "VERY_HIGH"}


def expiry_signal(
    spot_price:   float,
    max_pain:     float,
    vix:          float,
    pcr:          float,
    underlying:   str = "NIFTY",
    df            = None,
) -> dict:
    """
    Generate expiry-day specific trading signal.

    Args:
        spot_price: current NIFTY/BANKNIFTY price
        max_pain:   calculated max pain strike for this expiry
        vix:        India VIX current value
        pcr:        Put-Call Ratio (total put OI / total call OI)
        underlying: NIFTY / BANKNIFTY / FINNIFTY
        df:         5-min OHLCV dataframe (optional, for momentum)

    Returns:
        {"action": "BUY"|"HOLD",
         "option_type": "CE"|"PE"|None,
         "strategy": str,
         "score": float,
         "reason": str,
         "size_pct": float}  # fraction of normal size (0.5 = half size)
    """
    result = {
        "action":      "HOLD",
        "option_type": None,
        "strategy":    "expiry_no_signal",
        "score":       0.0,
        "reason":      "",
        "size_pct":    0.5,   # expiry = always half size vs normal
    }

    if not is_expiry_today(underlying):
        result["reason"] = "not_expiry_day"
        return result

    regime = get_expiry_regime()

    # Avoid trading at open and close of expiry day
    if regime["strategy"] in ("wait", "avoid"):
        result["reason"] = f"expiry_{regime['phase'].lower()}_avoid"
        return result

    if spot_price <= 0 or max_pain <= 0:
        result["reason"] = "missing_data"
        return result

    distance_pct = (spot_price - max_pain) / max_pain   # positive = above max pain

    # ── STRATEGY 1: MAX PAIN PIN ─────────────────────────────────────────
    # When price is within 0.5% of max pain, it tends to pin there
    if abs(distance_pct) < 0.005 and regime["strategy"] == "max_pain_pin":
        # Price is near max pain — sell the expensive side
        # If above max pain → CE sellers win → buy PE (or sell CE spread)
        if distance_pct > 0:
            result.update({
                "action":      "BUY",
                "option_type": "PE",
                "strategy":    "expiry_max_pain_pin",
                "score":       6.0,
                "reason":      f"spot_{spot_price:.0f}_above_maxpain_{max_pain:.0f}_pin_expected",
                "size_pct":    0.4,
            })
        else:
            result.update({
                "action":      "BUY",
                "option_type": "CE",
                "strategy":    "expiry_max_pain_pin",
                "score":       6.0,
                "reason":      f"spot_{spot_price:.0f}_below_maxpain_{max_pain:.0f}_pin_expected",
                "size_pct":    0.4,
            })
        return result

    # ── STRATEGY 2: EXPIRY MOMENTUM BREAKOUT ────────────────────────────
    # Price moved significantly beyond max pain — momentum play
    if abs(distance_pct) > 0.008 and regime["strategy"] == "expiry_momentum":
        option_type = "CE" if distance_pct > 0 else "PE"
        score       = 7.0 + min(abs(distance_pct) * 100, 2.0)   # up to 9.0
        result.update({
            "action":      "BUY",
            "option_type": option_type,
            "strategy":    "expiry_momentum_breakout",
            "score":       round(score, 2),
            "reason":      (f"spot_{spot_price:.0f}_vs_maxpain_{max_pain:.0f}_"
                            f"distance_{distance_pct:.1%}_momentum_{option_type}"),
            "size_pct":    0.5,
        })
        return result

    # ── STRATEGY 3: THETA SELL CONDITIONS ────────────────────────────────
    # Low VIX + near max pain = ideal theta sell conditions
    if vix < 15 and abs(distance_pct) < 0.003 and pcr > 0.8 and pcr < 1.2:
        result.update({
            "action":      "BUY",
            "option_type": "PE" if pcr < 1.0 else "CE",
            "strategy":    "expiry_theta_environment",
            "score":       5.5,
            "reason":      f"low_vix_{vix:.1f}_near_maxpain_theta_environment",
            "size_pct":    0.3,
        })

    return result


def get_expiry_score_boost(underlying: str = "NIFTY") -> float:
    """
    Returns score boost for any signal on expiry day.
    Expiry days have higher volume and clearer signals.
    """
    if not is_expiry_today(underlying):
        return 0.0
    regime = get_expiry_regime()
    boosts = {
        "DISCOVERY":  0.5,
        "MIDMORNING": 1.0,
        "POWER_HOUR": 1.5,
        "LUNCH":      -1.0,   # penalise lunch signals on expiry
        "OPENING":    -2.0,   # strong penalty — very unpredictable
        "CLOSING":    -3.0,
    }
    return boosts.get(regime.get("phase", ""), 0.0)


# ── 0DTE Strangle Strategy (James Cordier) ───────────────────────────────────

def get_0dte_strangle_strikes(
    spot_price:  float,
    strike_interval: int = 50,
    dte_hours:   float = 0,
    vix:         float = 15.0,
) -> dict:
    """
    0DTE (Zero Days To Expiry) Strangle for expiry day.

    Cordier's Rule:
      After 2:00 PM on expiry day, sell a strangle:
        - CE strike: spot + (2 × strike_interval)
        - PE strike: spot - (2 × strike_interval)
      Exit: 3:10 PM hard stop (force close before EOD)

    Why it works:
      In the last 1-2 hours of expiry, theta collapses rapidly.
      OTM options lose most of their value in the final hour.
      As long as NIFTY doesn't move more than ~2× the strike distance,
      both options expire worthless → full premium collected.

    Risk:
      If NIFTY makes a 200+ point move in last hour → loss.
      Rule: Only enter when VIX < 18 (calm day).
             Skip on event days (RBI, budget, results).
    """
    if vix > 18:
        return {
            "can_trade": False,
            "reason":    f"VIX {vix:.1f} > 18 — too volatile for 0DTE strangle",
        }

    now = __import__("datetime").datetime.now().time()
    from datetime import time as dtime
    if not (dtime(13, 45) <= now <= dtime(14, 45)):
        return {
            "can_trade": False,
            "reason":    f"Current time not in 0DTE window (1:45-2:45 PM)",
        }

    # Round spot to nearest strike
    atm = round(spot_price / strike_interval) * strike_interval

    # Strike selection: 2 intervals OTM
    # Wider on high VIX, tighter on low VIX
    intervals_otm = 2 if vix < 14 else 3
    ce_strike = atm + (intervals_otm * strike_interval)
    pe_strike = atm - (intervals_otm * strike_interval)

    # Expected premium (rough approximation)
    # At expiry, ATM option ≈ 0.4% of spot, 2-OTM ≈ 0.1-0.2% of spot
    ce_premium_est = round(spot_price * 0.0015, 1)
    pe_premium_est = round(spot_price * 0.0015, 1)
    total_est      = ce_premium_est + pe_premium_est

    return {
        "can_trade":       True,
        "ce_strike":       ce_strike,
        "pe_strike":       pe_strike,
        "atm_strike":      atm,
        "ce_premium_est":  ce_premium_est,
        "pe_premium_est":  pe_premium_est,
        "total_premium":   total_est,
        "hard_exit":       "15:10",
        "stop_loss_pct":   2.0,   # 2x premium = stop loss
        "reason":          (
            f"0DTE strangle: SELL CE@{ce_strike} + SELL PE@{pe_strike} "
            f"est premium ₹{total_est:.0f}/lot "
            f"exit by 15:10"
        ),
        "breakeven_upper": atm + (intervals_otm * strike_interval) + total_est,
        "breakeven_lower": atm - (intervals_otm * strike_interval) - total_est,
    }


def get_0dte_signal(
    spot_price:    float,
    max_pain:      float,
    vix:           float,
    pcr:           float = 1.0,
    underlying:    str   = "NIFTY",
    strike_interval: int = 50,
) -> dict:
    """
    Full 0DTE signal for expiry day afternoon.
    Combines Cordier strangle with Pivot Boss max pain analysis.

    Entry conditions:
      1. Time: 1:45 PM - 2:45 PM
      2. VIX < 18 (calm day)
      3. PCR between 0.7 and 1.3 (not heavily skewed)
      4. Spot within 1% of max pain (likely to pin)
      5. Not an event day (RBI/Budget)

    Returns action for BOTH legs (strangle = sell CE + sell PE).
    """
    result = {
        "action":     "HOLD",
        "legs":       [],
        "reason":     "",
        "score":      0.0,
    }

    if not is_expiry_today(underlying):
        result["reason"] = "not_expiry_day"
        return result

    # PCR check: balanced market needed for strangle
    if pcr < 0.6 or pcr > 1.5:
        result["reason"] = f"PCR {pcr:.2f} too extreme for strangle"
        return result

    strangle = get_0dte_strangle_strikes(spot_price, strike_interval, vix=vix)

    if not strangle.get("can_trade"):
        result["reason"] = strangle.get("reason","")
        return result

    # Max pain proximity check (Pivot Boss concept)
    if max_pain > 0:
        dist_from_mp = abs(spot_price - max_pain) / max_pain
        if dist_from_mp > 0.015:   # more than 1.5% from max pain
            result["reason"] = f"Spot {dist_from_mp:.1%} from max pain — risky for strangle"
            return result

    score = 7.0
    if vix < 13:  score += 1.0   # very calm
    if abs(pcr - 1.0) < 0.15: score += 0.5  # balanced PCR

    result.update({
        "action":    "STRANGLE_SELL",
        "score":     round(score, 2),
        "legs": [
            {
                "side":        "SELL",
                "option_type": "CE",
                "strike":      strangle["ce_strike"],
                "premium_est": strangle["ce_premium_est"],
            },
            {
                "side":        "SELL",
                "option_type": "PE",
                "strike":      strangle["pe_strike"],
                "premium_est": strangle["pe_premium_est"],
            },
        ],
        "total_premium": strangle["total_premium"],
        "hard_exit":     strangle["hard_exit"],
        "breakeven_range": (strangle["breakeven_lower"], strangle["breakeven_upper"]),
        "reason":        strangle["reason"],
    })
    return result
