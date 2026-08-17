"""
option_intraday_pricer.py — Black-Scholes intraday option pricer anchored to
REAL end-of-day NIFTY option settlement prices.

Why this exists: several seminar-sourced strategies (backtest_bollinger_otm_
reversal.py, backtest_orb_synthetic_future.py) need an intraday premium path
to evaluate same-day rupee P&L exit thresholds, but the only real historical
NIFTY option data in this system (options_nifty.db, 2020-2026) is EOD -- one
settle price per strike/expiry/day, not tick-level.

This is deliberately NOT the same mistake as backtest_iron_condor.py (blocked
-- "invents option credit and loss severity from underlying prices" from a
flat % heuristic with no grounding in real premia at all). Here, every day's
price path is anchored to that day's REAL settle price:

  1. Look up the real EOD settle for the traded (date, expiry, strike,
     opt_type) from options_nifty.db.
  2. Back out the implied vol that reproduces that real settle price via
     Black-Scholes (bisection -- robust where Newton's step blows up on
     near-zero vega deep OTM).
  3. Reprice the option at each intraday bar using that solved IV (held
     constant through the day -- real IV drifts intraday; this is the
     approximation) and the REAL 5-min underlying price, with time decaying
     continuously toward expiry.

Any backtest using this MUST report how many candidate days were skipped for
missing/unsolvable pricing data (see `valid` on DayPricer) -- silently
dropping those days would bias the sample toward whatever's easiest to price.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time as dtime
from typing import Optional, Tuple

from greeks_live import compute_greeks

RISK_FREE_RATE = 0.065
STRIKE_STEP = 50.0
MARKET_CLOSE = dtime(15, 30)


def nearest_strike(spot: float, step: float = STRIKE_STEP) -> float:
    return round(spot / step) * step


def otm_strike(spot: float, opt_type: str, n_strikes: int, step: float = STRIKE_STEP) -> float:
    """CE OTM is ABOVE spot; PE OTM is BELOW spot."""
    atm = nearest_strike(spot, step)
    return atm + n_strikes * step if opt_type == "CE" else atm - n_strikes * step


def year_fraction(from_dt: datetime, expiry_d: date) -> float:
    if from_dt.tzinfo is not None:
        from_dt = from_dt.replace(tzinfo=None)  # naive IST wall-clock throughout
    expiry_dt = datetime.combine(expiry_d, MARKET_CLOSE)
    seconds = (expiry_dt - from_dt).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24 * 3600)


def implied_vol(
    price: float, S: float, K: float, T: float, r: float, opt_type: str,
    lo: float = 0.01, hi: float = 3.0, tol: float = 1e-4, max_iter: int = 60,
) -> Optional[float]:
    """Bisection search for the sigma reproducing `price` via Black-Scholes."""
    if T <= 0 or price <= 0 or S <= 0 or K <= 0:
        return None
    option = "call" if opt_type == "CE" else "put"
    lo_px = compute_greeks(S, K, T, r, lo, option).get("price", 0.0)
    hi_px = compute_greeks(S, K, T, r, hi, option).get("price", 0.0)
    if price <= lo_px or price >= hi_px:
        return None  # outside a sane vol range -- don't extrapolate
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        mid_px = compute_greeks(S, K, T, r, mid, option).get("price", 0.0)
        if abs(mid_px - price) < tol:
            return mid
        if mid_px < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class DayPricer:
    """Solves IV once from a real EOD settle, then prices any intraday
    (timestamp, spot) off that same strike/expiry.

    `anchor_date` should be the PREVIOUS trading day's EOD settle, not the
    day being traded -- anchoring to the SAME day's close would leak that
    day's own outcome (the settle print happens ~3:30pm, after any morning
    entry) into the price used to evaluate the morning's trade. Using T-1's
    IV means one day of staleness but zero lookahead."""

    def __init__(
        self, S_eod: float, K: float, anchor_date: date, expiry: date,
        opt_type: str, eod_settle: float, r: float = RISK_FREE_RATE,
        sigma_shock: float = 0.0,
    ):
        """sigma_shock: absolute IV perturbation (e.g. -0.05 = 5 vol points
        lower) applied AFTER solving IV from the real settle. Default 0.0 is
        the unperturbed, unchanged behavior -- this exists for sensitivity
        testing (does a strategy's edge survive plausible IV mis-calibration,
        given the pricer only has yesterday's IV, not today's), not for
        normal use."""
        self.K = K
        self.expiry = expiry
        self.opt_type = opt_type
        self.r = r
        t_eod = year_fraction(datetime.combine(anchor_date, MARKET_CLOSE), expiry)
        solved = implied_vol(eod_settle, S_eod, K, t_eod, r, opt_type)
        self.sigma = max(0.01, solved + sigma_shock) if solved is not None else None

    @property
    def valid(self) -> bool:
        return self.sigma is not None and self.sigma > 0

    def price_at(self, dt: datetime, S: float) -> Optional[float]:
        if not self.valid:
            return None
        T = year_fraction(dt, self.expiry)
        if T <= 0:
            intrinsic = (S - self.K) if self.opt_type == "CE" else (self.K - S)
            return max(0.0, intrinsic)
        option = "call" if self.opt_type == "CE" else "put"
        g = compute_greeks(S, self.K, T, self.r, self.sigma, option)
        return g.get("price")


def load_eod_settle(
    conn: sqlite3.Connection, d: str, expiry: str, strike: float, opt_type: str,
) -> Optional[Tuple[float, float]]:
    """Returns (settle_price, underlying_close) or None if not quoted."""
    row = conn.execute(
        "SELECT settle, close, underlying FROM options_eod "
        "WHERE date=? AND expiry=? AND strike=? AND opt_type=?",
        (d, expiry, strike, opt_type),
    ).fetchone()
    if not row:
        return None
    settle, close, underlying = row
    px = settle if settle and settle > 0 else close
    if not px or px <= 0 or not underlying or underlying <= 0:
        return None
    return (float(px), float(underlying))


def nearest_weekly_expiry(conn: sqlite3.Connection, d: str) -> Optional[str]:
    """Smallest quoted expiry on/after date d."""
    row = conn.execute(
        "SELECT DISTINCT expiry FROM options_eod WHERE date=? AND expiry>=? "
        "ORDER BY expiry LIMIT 1",
        (d, d),
    ).fetchone()
    return row[0] if row else None
