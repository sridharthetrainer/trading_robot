"""
time_regime.py

NSE intraday time-zone regime overlay.

The NSE session has 6 distinct personality zones. Each zone favours
different strategies and risk tolerance levels.

Zones
-----
OPENING_RANGE  09:15–09:30  High volatility, institutional positioning.
               ORB strategy only. No other entries.

PRIMARY_TREND  09:30–11:00  Best window for trend + breakout strategies.
               Highest follow-through on directional moves.

VWAP_ZONE      11:00–13:00  Market consolidates around VWAP.
               VWAP reversion and MR strategies work best.

QUIET_ZONE     13:00–14:30  Low volume, slow grind.
               Scalping only. Avoid breakout trades.

POWER_HOUR     14:30–15:15  Large moves as positions squared before close.
               Trend + breakout resume with high volume.

EOD_ZONE       15:15–15:30  Force-close only. No new entries allowed.

Usage
-----
    from time_regime import get_time_zone, TimeZone, strategy_weights_for_zone

    zone = get_time_zone()
    weights = strategy_weights_for_zone(zone)
    print(zone.name, weights)
"""

from __future__ import annotations

from datetime import datetime, time as dtime
from enum import Enum
from typing import Dict, Optional


class TimeZone(Enum):
    OPENING_RANGE = "OPENING_RANGE"
    PRIMARY_TREND = "PRIMARY_TREND"
    VWAP_ZONE     = "VWAP_ZONE"
    QUIET_ZONE    = "QUIET_ZONE"
    POWER_HOUR    = "POWER_HOUR"
    EOD_ZONE      = "EOD_ZONE"
    PRE_MARKET    = "PRE_MARKET"
    AFTER_HOURS   = "AFTER_HOURS"


# ── Zone boundary definitions ─────────────────────────────────────────────────
_ZONES = [
    (dtime(9,  15), dtime(9,  30), TimeZone.OPENING_RANGE),
    (dtime(9,  30), dtime(11,  0), TimeZone.PRIMARY_TREND),
    (dtime(11,  0), dtime(13,  0), TimeZone.VWAP_ZONE),
    (dtime(13,  0), dtime(14, 30), TimeZone.QUIET_ZONE),
    (dtime(14, 30), dtime(15, 15), TimeZone.POWER_HOUR),
    (dtime(15, 15), dtime(15, 31), TimeZone.EOD_ZONE),
]


def get_time_zone(now: Optional[datetime] = None) -> TimeZone:
    """Return the current NSE session time zone."""
    t = (now or datetime.now()).time()
    for start, end, zone in _ZONES:
        if start <= t < end:
            return zone
    if t < dtime(9, 15):
        return TimeZone.PRE_MARKET
    return TimeZone.AFTER_HOURS


def get_time_zone_num(now: Optional[datetime] = None) -> int:
    """Return zone as integer 0-7 (for ML feature)."""
    mapping = {
        TimeZone.PRE_MARKET:    0,
        TimeZone.OPENING_RANGE: 1,
        TimeZone.PRIMARY_TREND: 2,
        TimeZone.VWAP_ZONE:     3,
        TimeZone.QUIET_ZONE:    4,
        TimeZone.POWER_HOUR:    5,
        TimeZone.EOD_ZONE:      6,
        TimeZone.AFTER_HOURS:   7,
    }
    return mapping.get(get_time_zone(now), 0)


def is_tradeable_zone(now: Optional[datetime] = None) -> bool:
    """True when new entries are allowed."""
    zone = get_time_zone(now)
    return zone in (
        TimeZone.OPENING_RANGE,
        TimeZone.PRIMARY_TREND,
        TimeZone.VWAP_ZONE,
        TimeZone.QUIET_ZONE,
        TimeZone.POWER_HOUR,
    )


def strategy_weights_for_zone(zone: TimeZone) -> Dict[str, float]:
    """
    Return a score multiplier for each strategy in the given time zone.
    Multiplier > 1.0 = boost, < 1.0 = reduced weight.
    Minimum floor is 0.30 — no strategy is ever completely disabled.
    Time weights are now ADDITIVE nudges (converted in signal_engine):
      2.0 → +1.0 nudge (preferred time)
      1.0 → +0.0 nudge (neutral)
      0.3 → -0.7 nudge (not preferred, but still runs)
    This allows ALL strategies to run at ALL times for confluence.

    These are applied to the raw strategy score before ranking so the
    best strategy for the current time of day is naturally selected.
    """
    weights = {
        TimeZone.OPENING_RANGE: {
            "orb":                   2.00,
            "hour_orb":              2.00,   # 1-hour ORB starts here
            "trend":                 0.50,
            "mean_reversion":        0.30,
            "breakout":              0.60,
            "scalping":              0.30,
            "institutional_scalp":   0.50,   # CVD needs volume to build
            "ma_cross":              0.40,
            "vwap_reversion":        0.30,
            "supertrend_mtf":        0.50,
            "order_block":           1.20,   # opening OBs are often respected
            "liquidity_sweep":       1.50,   # opening sweeps are common
            "vpoc_magnet":           0.40,
            "market_structure":      0.60,
        },
        TimeZone.PRIMARY_TREND: {
            "orb":                   0.60,
            "hour_orb":              1.80,   # 1-hour ORB fires here at 10:15
            "trend":                 1.30,
            "mean_reversion":        0.70,
            "breakout":              1.20,
            "scalping":              0.80,
            "institutional_scalp":   1.40,   # best CVD scalping window
            "ma_cross":              1.20,
            "vwap_reversion":        0.80,
            "supertrend_mtf":        1.30,
            "order_block":           1.60,   # OBs very powerful in trend
            "liquidity_sweep":       1.40,   # sweeps followed by strong moves
            "vpoc_magnet":           0.80,
            "market_structure":      1.50,   # BOS/CHOCH most meaningful here
        },
        TimeZone.VWAP_ZONE: {
            "orb":                   0.20,
            "hour_orb":              0.40,
            "trend":                 0.80,
            "mean_reversion":        1.30,
            "breakout":              0.70,
            "scalping":              1.00,
            "institutional_scalp":   1.20,   # CVD scalping during consolidation
            "ma_cross":              0.80,
            "vwap_reversion":        1.50,
            "supertrend_mtf":        0.80,
            "order_block":           1.20,   # OB retests in consolidation
            "liquidity_sweep":       1.00,
            "vpoc_magnet":           1.60,   # VPOC is MOST powerful in VWAP zone
            "market_structure":      0.90,
        },
        TimeZone.QUIET_ZONE: {
            "orb":                   0.10,
            "hour_orb":              0.20,
            "trend":                 0.70,
            "mean_reversion":        1.00,
            "breakout":              0.50,
            "scalping":              1.20,
            "institutional_scalp":   1.30,   # quiet = good for CVD scalping
            "ma_cross":              0.70,
            "vwap_reversion":        1.10,
            "supertrend_mtf":        0.70,
            "order_block":           0.80,
            "liquidity_sweep":       0.60,
            "vpoc_magnet":           1.20,
            "market_structure":      0.60,
        },
        TimeZone.POWER_HOUR: {
            "orb":                   0.20,
            "hour_orb":              0.30,
            "trend":                 1.30,
            "mean_reversion":        0.70,
            "breakout":              1.30,
            "scalping":              0.90,
            "institutional_scalp":   1.20,   # power hour has strong CVD signals
            "ma_cross":              1.20,
            "vwap_reversion":        0.80,
            "supertrend_mtf":        1.30,
            "order_block":           1.40,   # power hour OBs = strong moves
            "liquidity_sweep":       1.60,   # pre-close sweeps = big moves
            "vpoc_magnet":           0.80,
            "market_structure":      1.40,
        },
        TimeZone.EOD_ZONE: {k: 0.0 for k in  # No new entries
            ["orb","trend","mean_reversion","breakout","scalping",
             "ma_cross","vwap_reversion","supertrend_mtf"]},
    }
    default = {k: 1.0 for k in
               ["orb","trend","mean_reversion","breakout","scalping",
                "ma_cross","vwap_reversion","supertrend_mtf",
                "institutional_scalp","order_block","liquidity_sweep",
                "vpoc_magnet","hour_orb","market_structure"]}
    return weights.get(zone, default)


def get_strategy_weight(strategy: str, now: Optional[datetime] = None) -> float:
    """Return the time-zone weight for a specific strategy name."""
    zone = get_time_zone(now)
    weights = strategy_weights_for_zone(zone)
    return weights.get(strategy.lower(), 1.0)


def is_expiry_day() -> bool:
    """True when today is Tuesday (NSE weekly expiry day since 2025-09-01;
    was Thursday before that — fixed 2026-07-14, verified against this
    system's own live option-chain data, see expiry_regime.py module note)."""
    return datetime.now().weekday() == 1


def get_expiry_phase() -> Optional[str]:
    """
    On expiry day, return the phase:
    'morning'  09:30-13:00 — ORB + trend valid
    'afternoon' 13:00-14:30 — quiet, avoid
    'max_pain'  14:30-15:15 — max pain gravity active
    None on non-expiry days.
    """
    if not is_expiry_day():
        return None
    t = datetime.now().time()
    if dtime(9, 30) <= t < dtime(13, 0):
        return "morning"
    if dtime(13, 0) <= t < dtime(14, 30):
        return "afternoon"
    if dtime(14, 30) <= t < dtime(15, 15):
        return "max_pain"
    return None
