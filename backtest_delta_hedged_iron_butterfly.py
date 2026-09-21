"""
backtest_delta_hedged_iron_butterfly.py -- tests whether CONTINUOUS
DELTA HEDGING changes the verdict on the already-tested-and-rejected
iron butterfly (backtest_orb... no -- condor_backtest_real.py's
otm_pct=0 variant, REJECTED 2026-09-20: negative expectancy across all
wing widths, win rate 40-47%).

This is the concrete first phase of the "market-maker style" system
discussed 2026-09-21: real vol desks don't hold a STATIC option
position to a fixed exit (every structure tested this session, without
exception, has been static) -- they continuously rebalance delta as the
underlying moves, aiming to isolate the volatility-risk-premium capture
(IV vs subsequently-realized-vol) from directional exposure. This tests
that specific, previously-untested ingredient: does ACTIVE delta
management change the already-measured negative verdict?

Structure: same iron butterfly as condor_backtest_real.py's otm_pct=0
variant (short ATM CE+PE, long OTM wings at wing_pts for defined risk --
kept from the start, not bolted on later, per the explicit lesson from
every naked/undefined-risk structure this session: continuous hedging
ALONE does not protect against a fast gap that outruns discrete
rebalancing, e.g. 2018's "Volmageddon" wiped out funds that were
short vol WITH hedging infrastructure). Entry 9:20 (matching every
other short-straddle-family convention this session), EOD square-off.

Delta hedging: at entry and every subsequent 5-min bar, compute the net
position delta (4 option legs, via greeks_live.compute_greeks using
each leg's own DayPricer-solved IV) and hold an offsetting NIFTY-
futures-equivalent hedge (index-point notional, same "position" unit
convention as backtest_carver_ewmac.py). Rebalance only when net delta
drifts beyond REBALANCE_BAND (in equivalent underlying units), not
every single bar -- a real cost/hedge-accuracy tradeoff (a classic,
well-documented quant tuning parameter, not tuned/optimized here beyond
one reasonable starting value, to avoid a fishing expedition).

TAIL-RISK PROTECTION, built in from the start: if any single 5-min bar's
underlying move exceeds GAP_STOP_MULT times the day's own entry-implied
expected 5-min move (sigma-derived), exit the ENTIRE position (options +
hedge) immediately at that bar -- discrete rebalancing cannot react
faster than one bar's delay, so a fast gap needs its own explicit stop,
not reliance on hedging alone.

Real transaction costs on every leg AND every hedge rebalance
(nse_cost_model.py): OPT_SELL on the two short legs, OPT_BUY on the two
wing legs, FUT on every hedge adjustment.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Any, Dict, List, Optional

import numpy as np

from greeks_live import compute_greeks
from option_intraday_pricer import DayPricer, nearest_strike, otm_strike, nearest_weekly_expiry, year_fraction, RISK_FREE_RATE
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, load_nifty_candles, _prev_trading_day_with_quote,
)

OPTIONS_DB = "options_nifty.db"
ENTRY_TIME = dtime(9, 20)
SQUARE_OFF_TIME = dtime(15, 10)
WING_PTS = 300.0
REBALANCE_BAND = 15.0     # re-hedge only when net delta-equivalent drift exceeds
                          # this many index-point-equivalent units
GAP_STOP_MULT = 3.0       # hard exit if one bar's move exceeds 3x the day's
                          # entry-implied expected 5-min move


@dataclass
class Leg:
    opt_type: str      # "CE" or "PE"
    side: str          # "SELL" or "BUY"
    strike: float
    pricer: DayPricer
    entry_premium: float


def _build_legs(opt_conn, day: date, expiry: str, spot: float, entry_ts) -> Optional[List[Leg]]:
    exp_date = date.fromisoformat(expiry)
    atm = nearest_strike(spot)
    specs = [
        ("CE", "SELL", atm),
        ("PE", "SELL", atm),
        ("CE", "BUY", atm + WING_PTS),
        ("PE", "BUY", atm - WING_PTS),
    ]
    legs = []
    for opt_type, side, strike in specs:
        anchor_day_str, anchor = _prev_trading_day_with_quote(opt_conn, day, expiry, strike, opt_type)
        if not anchor:
            return None
        eod_settle, eod_underlying = anchor
        anchor_date = date.fromisoformat(anchor_day_str)
        pricer = DayPricer(eod_underlying, strike, anchor_date, exp_date, opt_type, eod_settle)
        if not pricer.valid:
            return None
        premium = pricer.price_at(entry_ts.to_pydatetime(), spot)
        if not premium or premium <= 0:
            return None
        legs.append(Leg(opt_type=opt_type, side=side, strike=strike, pricer=pricer, entry_premium=premium))
    return legs


def _leg_delta(leg: Leg, dt: datetime, spot: float) -> float:
    T = year_fraction(dt, leg.pricer.expiry)
    if T <= 0 or not leg.pricer.valid:
        return 0.0
    option = "call" if leg.opt_type == "CE" else "put"
    g = compute_greeks(spot, leg.strike, T, RISK_FREE_RATE, leg.pricer.sigma, option)
    d = g.get("delta", 0.0)
    sign = 1.0 if leg.side == "BUY" else -1.0
    return sign * d


def _net_position_delta(legs: List[Leg], dt: datetime, spot: float) -> float:
    return sum(_leg_delta(leg, dt, spot) for leg in legs)


def _leg_mtm(leg: Leg, dt: datetime, spot: float) -> Optional[float]:
    px = leg.pricer.price_at(dt, spot)
    if px is None:
        return None
    sign = -1.0 if leg.side == "SELL" else 1.0   # P&L sign vs entry, per unit
    return sign * (px - leg.entry_premium)


def backtest_delta_hedged_iron_butterfly(
    lots: int = 1, lot_size: int = DEFAULT_LOT_SIZE,
    wing_pts: float = WING_PTS, rebalance_band: float = REBALANCE_BAND,
    gap_stop_mult: float = GAP_STOP_MULT, verbose: bool = True,
) -> Dict[str, Any]:
    candles = load_nifty_candles(interval="5m")
    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    cost_model = get_cost_model()

    trades: List[Dict[str, Any]] = []
    skipped = 0
    days = sorted(set(candles.index.date))

    for day in days:
        day_bars = candles[(candles.index.date == day) & (candles.index.time >= ENTRY_TIME)]
        if day_bars.empty:
            continue
        expiry = nearest_weekly_expiry(opt_conn, str(day))
        if not expiry:
            skipped += 1
            continue

        entry_ts = day_bars.index[0]
        entry_spot = float(day_bars.iloc[0]["close"])
        legs = _build_legs(opt_conn, day, expiry, entry_spot, entry_ts)
        if legs is None:
            skipped += 1
            continue

        entry_cost = 0.0
        for leg in legs:
            side = "SELL" if leg.side == "SELL" else "BUY"
            instrument = "OPT_SELL" if leg.side == "SELL" else "OPT_BUY"
            entry_cost += cost_model.single_leg_cost(leg.entry_premium * qty, instrument, side, "NIFTY").total

        # day's own entry-implied expected 5-min move, for the gap stop
        avg_sigma = float(np.mean([leg.pricer.sigma for leg in legs if leg.pricer.valid]))
        T_day = year_fraction(entry_ts.to_pydatetime(), legs[0].pricer.expiry)
        expected_5min_move = entry_spot * avg_sigma * np.sqrt(5.0 / (252 * 375)) if T_day > 0 else 1e9

        net_delta = _net_position_delta(legs, entry_ts.to_pydatetime(), entry_spot)
        hedge_units = -net_delta * qty   # index-point-equivalent futures units held
        hedge_entry_price = entry_spot
        hedge_cost_total = 0.0
        if abs(hedge_units) > 0:
            side = "BUY" if hedge_units > 0 else "SELL"
            hedge_cost_total += cost_model.single_leg_cost(abs(hedge_units) * entry_spot, "FUT", side, "NIFTY").total

        prev_spot = entry_spot
        hedge_pnl = 0.0
        exit_reason = "EOD"
        exit_ts = entry_ts
        n_rebalances = 0

        for bar_ts, bar in day_bars[day_bars.index > entry_ts].iterrows():
            spot = float(bar["close"])
            bar_move = abs(spot - prev_spot)
            hedge_pnl += hedge_units * (spot - prev_spot)

            is_eod = bar_ts.time() >= SQUARE_OFF_TIME
            gap_triggered = bar_move > gap_stop_mult * expected_5min_move

            if gap_triggered or is_eod:
                exit_reason = "GAP_STOP" if gap_triggered else "EOD"
                exit_ts = bar_ts
                break

            new_net_delta = _net_position_delta(legs, bar_ts.to_pydatetime(), spot)
            target_hedge = -new_net_delta * qty
            drift = target_hedge - hedge_units
            if abs(drift) > rebalance_band:
                side = "BUY" if drift > 0 else "SELL"
                hedge_cost_total += cost_model.single_leg_cost(abs(drift) * spot, "FUT", side, "NIFTY").total
                hedge_units = target_hedge
                n_rebalances += 1

            prev_spot = spot

        # final settlement at exit_ts
        exit_bars = day_bars[day_bars.index == exit_ts]
        if exit_bars.empty:
            skipped += 1
            continue
        exit_spot = float(exit_bars.iloc[0]["close"])
        hedge_pnl += hedge_units * (exit_spot - prev_spot) if exit_spot != prev_spot else 0.0

        option_pnl = 0.0
        exit_cost = 0.0
        ok = True
        for leg in legs:
            mtm = _leg_mtm(leg, exit_ts.to_pydatetime(), exit_spot)
            if mtm is None:
                ok = False
                break
            option_pnl += mtm * qty
            exit_side = "BUY" if leg.side == "SELL" else "SELL"
            instrument = "OPT_SELL" if leg.side == "SELL" else "OPT_BUY"
            exit_px = leg.pricer.price_at(exit_ts.to_pydatetime(), exit_spot)
            exit_cost += cost_model.single_leg_cost(exit_px * qty, instrument, exit_side, "NIFTY").total
        if not ok:
            skipped += 1
            continue

        # close out remaining hedge
        if abs(hedge_units) > 0:
            side = "SELL" if hedge_units > 0 else "BUY"
            exit_cost += cost_model.single_leg_cost(abs(hedge_units) * exit_spot, "FUT", side, "NIFTY").total

        total_cost = entry_cost + hedge_cost_total + exit_cost
        net_pnl = option_pnl + hedge_pnl - total_cost

        trades.append({
            "date": str(day), "exit_reason": exit_reason, "n_rebalances": n_rebalances,
            "option_pnl": round(option_pnl, 2), "hedge_pnl": round(hedge_pnl, 2),
            "cost": round(total_cost, 2), "net_pnl": round(net_pnl, 2),
        })

    opt_conn.close()
    return _summarize(trades, skipped, len(days), verbose)


def _summarize(trades, skipped, n_days, verbose):
    n = len(trades)
    if n == 0:
        return {"strategy": "delta_hedged_iron_butterfly", "num_trades": 0,
                "candidate_days": n_days, "skipped": skipped}
    pnls = np.array([t["net_pnl"] for t in trades])
    win_rate = float((pnls > 0).mean())
    total_pnl = float(pnls.sum())
    sd = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    avg_rebal = float(np.mean([t["n_rebalances"] for t in trades]))

    if verbose:
        print(f"\n{'='*60}\ndelta_hedged_iron_butterfly\n{'='*60}")
        print(f"Candidate days: {n_days}  Trades: {n}  (skipped: {skipped})")
        print(f"Win rate: {win_rate:.2%}  NET P&L: Rs{total_pnl:,.0f}  Sharpe: {sharpe:.3f}")
        print(f"Avg rebalances/day: {avg_rebal:.1f}  Exit reasons: {reasons}")
        print(f"Total option_pnl: Rs{sum(t['option_pnl'] for t in trades):,.0f}  "
              f"Total hedge_pnl: Rs{sum(t['hedge_pnl'] for t in trades):,.0f}  "
              f"Total cost: Rs{sum(t['cost'] for t in trades):,.0f}")

    return {"strategy": "delta_hedged_iron_butterfly", "num_trades": n,
            "win_rate": round(win_rate, 4), "total_pnl": round(total_pnl, 2),
            "sharpe": round(sharpe, 4), "exit_reasons": reasons,
            "avg_rebalances": round(avg_rebal, 2), "candidate_days": n_days,
            "skipped": skipped, "trades": trades}


if __name__ == "__main__":
    backtest_delta_hedged_iron_butterfly()
