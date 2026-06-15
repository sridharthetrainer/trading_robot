"""
expiry_regime.py  —  Expiry Day Regime & Rollover Week Detection

EXPIRY DAY IS A DIFFERENT MARKET:
  0DTE (Thursday weekly / last Thursday monthly):
    - Options lose 50-80% theta by 2 PM
    - Price magnetically pulls to max pain in last hour
    - Gamma explosions on large moves
    - Do NOT chase breakouts — mean revert toward max pain

  1DTE (Wednesday):
    - Theta acceleration begins
    - Strangle premium collapses fast
    - Directional trades still work but hold time reduced

  ROLLOVER WEEK (last week of monthly expiry):
    - Heavy near-month unwinding + far-month build
    - Index stays in 200-300 point range
    - Basis (futures premium/discount) meaningful signal

  MONTHLY EXPIRY DAY:
    - Largest OI day of month
    - Biggest max pain pull
    - Biggest gamma risk

SIGNALS PRODUCED:
  is_expiry_day() → bool
  is_monthly_expiry() → bool
  is_rollover_week() → bool
  get_expiry_regime() → dict with score modifiers
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# SEBI CIRCULAR Nov 2024: Weekly expiry restricted to ONE index per exchange
#   NSE: ONLY NIFTY has weekly expiry (Thursday)
#   BSE: ONLY SENSEX has weekly expiry (Friday)
#   BANKNIFTY: monthly only (last Wednesday of month)
#   FINNIFTY:  monthly only (last Tuesday of month)
#   MIDCPNIFTY: monthly only (last Monday of month)
# ─────────────────────────────────────────────────────────────────────────────
WEEKLY_EXPIRY_SYMBOLS  = {"NIFTY"}         # NSE — Thursday weekly
WEEKLY_EXPIRY_BSE      = {"SENSEX"}        # BSE — Friday weekly
MONTHLY_ONLY_SYMBOLS   = {                 # No weekly expiry anymore
    "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNEXT50", "BANKEX"
}

def has_weekly_expiry(symbol: str) -> bool:
    """Return True only if symbol has ACTIVE weekly expiry contracts."""
    s = symbol.upper()
    return s in WEEKLY_EXPIRY_SYMBOLS or s in WEEKLY_EXPIRY_BSE

def get_expiry_day_of_week(symbol: str) -> int:
    """Return weekday of expiry: 0=Mon,1=Tue,2=Wed,3=Thu,4=Fri."""
    s = symbol.upper()
    if s in WEEKLY_EXPIRY_SYMBOLS:
        return 3  # Thursday (NSE NIFTY)
    if s in WEEKLY_EXPIRY_BSE:
        return 4  # Friday (BSE SENSEX)
    # Monthly only — last week of month
    if s == "BANKNIFTY":    return 2  # Wednesday
    if s == "FINNIFTY":     return 1  # Tuesday
    if s == "MIDCPNIFTY":   return 0  # Monday
    if s == "NIFTYNEXT50":  return 3  # Thursday (monthly)
    return 3  # default Thursday



import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _last_thursday(year: int, month: int) -> date:
    """Return the last Thursday of a given month."""
    # Find last day of month
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    # Walk back to Thursday (weekday 3)
    days_back = (last_day.weekday() - 3) % 7
    return last_day - timedelta(days=days_back)


def _all_thursdays_in_month(year: int, month: int) -> list:
    """Return all Thursdays in a month."""
    thursdays = []
    d = date(year, month, 1)
    # Find first Thursday
    while d.weekday() != 3:
        d += timedelta(days=1)
    while d.month == month:
        thursdays.append(d)
        d += timedelta(days=7)
    return thursdays


def get_expiry_regime(today: Optional[date] = None) -> dict:
    """
    Analyze today's expiry regime.

    Returns:
        is_expiry_day:     bool
        is_monthly_expiry: bool
        is_rollover_week:  bool
        days_to_expiry:    int (0 = expiry day, 1 = 1DTE, etc.)
        score_modifiers:   dict
        regime_label:      str
    """
    if today is None:
        today = date.today()

    # All weekly Thursdays this month
    thursdays = _all_thursdays_in_month(today.year, today.month)
    monthly_expiry = thursdays[-1]   # last Thursday = monthly

    # Find next expiry
    upcoming = [t for t in thursdays if t >= today]
    if not upcoming:
        # Spill into next month
        if today.month == 12:
            next_thursdays = _all_thursdays_in_month(today.year + 1, 1)
        else:
            next_thursdays = _all_thursdays_in_month(today.year, today.month + 1)
        upcoming = next_thursdays

    next_expiry = upcoming[0]
    days_to_expiry = (next_expiry - today).days

    is_expiry_day    = days_to_expiry == 0
    is_1dte          = days_to_expiry == 1
    is_monthly_exp   = next_expiry == monthly_expiry and is_expiry_day
    is_rollover_week = (monthly_expiry - today).days <= 5 and not is_expiry_day

    # Score modifiers
    # NOTE: These are now used as NUDGE values in signal_engine:
    # nudge = mult - 1.0, capped at ±0.8 (never kills a signal)
    # e.g. trend=0.6 → nudge=-0.4 (suppressed but not zeroed)
    #      mean_rev=1.6 → nudge=+0.6 (boosted)
    mods = {
        "breakout":    1.0,
        "trend":       1.0,
        "mean_rev":    1.0,
        "scalping":    1.0,
        "max_hold_bars_override": None,
    }

    regime_label = "NORMAL"

    if is_expiry_day:
        if is_monthly_exp:
            regime_label = "MONTHLY_EXPIRY"
            # Very high gamma — strong max pain pull
            mods["breakout"]    = 0.4   # don't chase breakouts
            mods["trend"]       = 0.5
            mods["mean_rev"]    = 1.6   # mean revert toward max pain
            mods["scalping"]    = 1.4
            mods["max_hold_bars_override"] = 6   # shorter holds
        else:
            regime_label = "WEEKLY_EXPIRY"
            mods["breakout"]    = 0.5
            mods["trend"]       = 0.6
            mods["mean_rev"]    = 1.4
            mods["scalping"]    = 1.3
            mods["max_hold_bars_override"] = 8

    elif is_1dte:
        regime_label = "ONE_DTE"
        mods["breakout"]    = 0.7
        mods["trend"]       = 0.8
        mods["mean_rev"]    = 1.2
        mods["max_hold_bars_override"] = 10

    elif is_rollover_week:
        regime_label = "ROLLOVER_WEEK"
        mods["breakout"]    = 0.6   # index usually stays in range
        mods["trend"]       = 0.7
        mods["mean_rev"]    = 1.3

    # min_score recommendation for expiry day (lower = more signals)
    min_score_adj = -1.0 if is_expiry_day else (-0.5 if is_1dte else 0.0)

    return {
        "is_expiry_day":    is_expiry_day,
        "min_score_adjustment": min_score_adj,
        "is_monthly_expiry":is_monthly_exp,
        "is_1dte":          is_1dte,
        "is_rollover_week": is_rollover_week,
        "days_to_expiry":   days_to_expiry,
        "next_expiry":      str(next_expiry),
        "monthly_expiry":   str(monthly_expiry),
        "regime_label":     regime_label,
        "score_modifiers":  mods,
    }


def apply_expiry_score_modifier(strategy: str, base_score: float,
                                 regime: Optional[dict] = None) -> float:
    """
    Apply expiry regime modifier to a strategy score — ADDITIVE only.

    CRITICAL FIX: Previously used MULTIPLY (base_score × 0.5 = 1.75 < min_score)
    which eliminated strategies BEFORE confluence was computed.
    
    Now uses ADDITIVE nudge: convert multiplier to ±2.0 range.
    - Multiplier 0.4 → nudge = -1.0  (penalty, but strategy still votes)
    - Multiplier 1.6 → nudge = +1.0  (boost)
    - Multiplier 1.0 → nudge =  0.0  (neutral)
    
    Strategies with penalties still enter the candidate pool and VOTE.
    The confluence engine then decides if enough strategies agree.
    Individual strategies should NOT be eliminated by expiry regime alone.
    """
    if regime is None:
        regime = get_expiry_regime()
    mods    = regime.get("score_modifiers", {})
    strat_l = strategy.lower()

    # Get the multiplier for this strategy type
    if "break" in strat_l or "orb" in strat_l:
        multiplier = mods.get("breakout", 1.0)
    elif "trend" in strat_l or "ma_cross" in strat_l or "holy_grail" in strat_l:
        multiplier = mods.get("trend", 1.0)
    elif "mean" in strat_l or "vwap" in strat_l or "revert" in strat_l:
        multiplier = mods.get("mean_rev", 1.0)
    elif "scalp" in strat_l:
        multiplier = mods.get("scalping", 1.0)
    else:
        multiplier = 1.0

    # Convert multiplier to additive nudge: 0.5→-1.0, 1.0→0.0, 1.6→+1.0
    # max nudge = ±1.5 to preserve confluence voting while signalling preference
    nudge = round(max(-1.5, min(1.5, (multiplier - 1.0) * 1.5)), 2)
    return round(base_score + nudge, 2)


def get_sip_calendar_boost(today: Optional[date] = None) -> float:
    """
    SIP deployment calendar boost.
    MF managers deploy ₹18,000Cr/month SIP money around 3rd-4th Monday.
    Returns score boost for BUY signals on those days.
    """
    if today is None:
        today = date.today()
    if today.weekday() != 0:   # not Monday
        return 0.0
    # Count which Monday of the month
    day = today.day
    monday_num = (day - 1) // 7 + 1
    if monday_num in (3, 4):
        return 0.6   # SIP deployment window — boost BUY signals
    return 0.0
