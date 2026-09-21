"""
trading_time_pricer.py -- a TRADING-TIME-WEIGHTED variant of
option_intraday_pricer.py's DayPricer, built to test whether this
project's straight-calendar-time convention (option_intraday_pricer.
year_fraction: raw elapsed seconds / seconds-in-a-year) was distorting
already-tested weekly option-selling verdicts.

Premise, verified against real NIFTY data 2026-09-21 (candle_cache.db,
full 334-day 5-min history): intraday variance accrues at 2.45x the
rate per hour compared to overnight variance -- 46.4% of TOTAL realized
variance occurs in just 26.1% of TOTAL elapsed calendar time. Weekend
gaps (3-day) show variance-per-calendar-hour roughly 45% lower than a
normal single-day overnight gap (0.77e-6 vs 1.41e-6), consistent with
the same premise, though the 2-day category was noisy (n=12) and not
perfectly monotonic -- a real, if imperfect, empirical confirmation.

This does NOT itself prove a tradeable mispricing -- it only establishes
that TIME, measured in raw calendar terms, is the wrong clock for how
NIFTY actually generates variance. Whether the REAL option market
already correctly prices this (via IV term structure, weekend theta
decay conventions professional desks already use, etc.) can't be
directly tested here -- this project only has REAL EOD settle prices,
not real intraday quotes finely time-stamped across the overnight
boundary. What CAN be tested: does re-solving IV from the same real EOD
settle prices, and repricing intraday, using a TRADING-TIME clock
instead of calendar time, change the verdict on the already-rejected
weekly option-selling structures (bull put spread, iron condor)?

OVERNIGHT_WEIGHT = 0.408 (overnight_var_per_hour / intraday_var_per_hour
from the measurement above, i.e. non-trading hours count as ~41% as
much "time" as trading hours for volatility purposes). TRADING_HOURS_PER_DAY
= 6.25 (9:15-15:30). Reference annualization denominator is built so
T=1.0 for exactly one calendar year, keeping this consistent with the
un-weighted pricer's annualization convention.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from greeks_live import compute_greeks
from option_intraday_pricer import implied_vol, MARKET_CLOSE, RISK_FREE_RATE

TRADING_START = dtime(9, 15)
TRADING_END = dtime(15, 30)
TRADING_HOURS_PER_DAY = 6.25
OVERNIGHT_WEIGHT = 0.408   # measured: overnight_var_per_hour / intraday_var_per_hour

# Reference: effective weighted hours in one calendar year, for consistent
# annualization (252 trading days assumed, a standard convention already
# used elsewhere in this project, e.g. Sharpe annualization).
_TRADING_DAYS_PER_YEAR = 252
_CALENDAR_HOURS_PER_YEAR = 365.25 * 24
_TRADING_HOURS_PER_YEAR = _TRADING_DAYS_PER_YEAR * TRADING_HOURS_PER_DAY
_OVERNIGHT_HOURS_PER_YEAR = _CALENDAR_HOURS_PER_YEAR - _TRADING_HOURS_PER_YEAR
_EFFECTIVE_HOURS_PER_YEAR = _TRADING_HOURS_PER_YEAR + _OVERNIGHT_HOURS_PER_YEAR * OVERNIGHT_WEIGHT


def _hours_split(from_dt: datetime, to_dt: datetime) -> tuple[float, float]:
    """Decompose [from_dt, to_dt) into (trading_hours, overnight_hours),
    day by day. Weekends treated as fully non-trading (a reasonable
    simplification -- NSE holidays beyond weekends are not modeled)."""
    if to_dt <= from_dt:
        return 0.0, 0.0
    trading_hours = 0.0
    overnight_hours = 0.0
    cur = from_dt
    while cur < to_dt:
        day_end = datetime.combine(cur.date(), dtime(23, 59, 59, 999999))
        segment_end = min(to_dt, day_end + timedelta(microseconds=1))
        is_weekday = cur.weekday() < 5
        sess_start = datetime.combine(cur.date(), TRADING_START)
        sess_end = datetime.combine(cur.date(), TRADING_END)
        if is_weekday and segment_end > sess_start and cur < sess_end:
            overlap_start = max(cur, sess_start)
            overlap_end = min(segment_end, sess_end)
            if overlap_end > overlap_start:
                trading_hours += (overlap_end - overlap_start).total_seconds() / 3600.0
        total_seg_hours = (segment_end - cur).total_seconds() / 3600.0
        seg_trading_hours = 0.0
        if is_weekday and segment_end > sess_start and cur < sess_end:
            overlap_start = max(cur, sess_start)
            overlap_end = min(segment_end, sess_end)
            if overlap_end > overlap_start:
                seg_trading_hours = (overlap_end - overlap_start).total_seconds() / 3600.0
        overnight_hours += max(0.0, total_seg_hours - seg_trading_hours)
        cur = segment_end
    return trading_hours, overnight_hours


def trading_time_year_fraction(from_dt: datetime, expiry_d: date) -> float:
    if from_dt.tzinfo is not None:
        from_dt = from_dt.replace(tzinfo=None)
    expiry_dt = datetime.combine(expiry_d, MARKET_CLOSE)
    if expiry_dt <= from_dt:
        return 0.0
    trading_hours, overnight_hours = _hours_split(from_dt, expiry_dt)
    effective_hours = trading_hours + overnight_hours * OVERNIGHT_WEIGHT
    return effective_hours / _EFFECTIVE_HOURS_PER_YEAR


class TradingTimeDayPricer:
    """Same anchoring logic as option_intraday_pricer.DayPricer (solve IV
    once from a real T-1 EOD settle, reprice intraday off real spot), but
    using trading_time_year_fraction instead of calendar year_fraction for
    BOTH the IV solve and the repricing -- a consistent trading-time clock
    throughout, not a calendar clock at one end and trading-time at the
    other."""

    def __init__(self, S_eod: float, K: float, anchor_date: date, expiry: date,
                 opt_type: str, eod_settle: float, r: float = RISK_FREE_RATE):
        self.K = K
        self.expiry = expiry
        self.opt_type = opt_type
        self.r = r
        anchor_dt = datetime.combine(anchor_date, MARKET_CLOSE)
        t_eod = trading_time_year_fraction(anchor_dt, expiry)
        solved = implied_vol(eod_settle, S_eod, K, t_eod, r, opt_type)
        self.sigma = max(0.01, solved) if solved is not None else None

    @property
    def valid(self) -> bool:
        return self.sigma is not None and self.sigma > 0

    def price_at(self, dt: datetime, S: float) -> Optional[float]:
        if not self.valid:
            return None
        T = trading_time_year_fraction(dt, self.expiry)
        if T <= 0:
            intrinsic = (S - self.K) if self.opt_type == "CE" else (self.K - S)
            return max(0.0, intrinsic)
        option = "call" if self.opt_type == "CE" else "put"
        g = compute_greeks(S, self.K, T, self.r, self.sigma, option)
        return g.get("price")
