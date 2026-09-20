"""
naked_straddle_strangle_backtest.py -- real-premia-anchored backtest for
STANDALONE (naked, undefined-risk) option-selling structures: 09:20 Short
Straddle (catalog ID C1) and Short Strangle OTM (C2), the two untested
CORE/ADVANCED "standalone" entries from option_strategy_registry.py's
42-strategy spec. This is the "STANDALONE" half of the standalone-vs-pair
option-selling audit (2026-09-20) -- the "pair" half (credit spreads, iron
condor, iron butterfly) is in condor_backtest_real.py / credit_spread_
backtest_real.py.

Different in kind from E4 (Rolling Short Straddle, already tested and
REJECTED, -Rs2,897,571): E4 cycles in and out multiple times per day with
a combined-P&L threshold; C1/C2 here are a SINGLE entry per day (fixed
entry time, one straddle/strangle, held to square-off or a leg stop-loss),
same shape as this project's other single_leg_intraday_option_backtest.py
seminar strategies but for a SHORT (not long) 2-leg position. Reuses
option_intraday_pricer.py's EOD-settle-anchored Black-Scholes intraday
pricer -- same real-data discipline as every other intraday-option
backtest built this session, not condor_backtest_real.py's weekly EOD-only
settlement (this needs live intraday premium tracking for the leg-level
stop-loss check).

Rules:
  - Entry at ENTRY_TIME: sell ATM CE + ATM PE (straddle, otm_strikes=0) or
    N-strikes-OTM CE + OTM PE (strangle, otm_strikes>0).
  - EITHER leg's premium rising more than leg_sl_pct from its own entry ->
    close BOTH legs immediately (undefined-risk structure, no protective
    wing -- this is the whole point of testing it against the paired
    spreads, which cap this same risk by construction).
  - Otherwise hold to SQUARE_OFF_TIME.
  - Exactly one entry per day, no re-entry (unlike E4).

Same real transaction-cost model (nse_cost_model.py) as every other
backtest in this batch.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, time as dtime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from option_intraday_pricer import DayPricer, nearest_strike, otm_strike, nearest_weekly_expiry
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, load_nifty_candles, _prev_trading_day_with_quote,
)

OPTIONS_DB = "options_nifty.db"
SQUARE_OFF_TIME = dtime(15, 10)
LEG_SL_PCT = 0.30


@dataclass
class Cycle:
    entry_date: str
    entry_time: str
    exit_time: str
    exit_reason: str          # LEG_SL / EOD
    call_strike: float
    put_strike: float
    call_entry: float
    put_entry: float
    call_exit: float
    put_exit: float
    qty: int
    gross_pnl: float
    cost: float
    pnl: float


def _open_position(opt_conn, day: date, expiry: str, spot: float, bar_time, otm_strikes: int):
    if otm_strikes == 0:
        call_strike = nearest_strike(spot)
        put_strike = call_strike
    else:
        call_strike = otm_strike(spot, "CE", otm_strikes)
        put_strike = otm_strike(spot, "PE", otm_strikes)
    exp_date = date.fromisoformat(expiry)
    legs = {}
    for opt_type, strike in (("CE", call_strike), ("PE", put_strike)):
        anchor_day_str, anchor = _prev_trading_day_with_quote(opt_conn, day, expiry, strike, opt_type)
        if not anchor:
            return None
        eod_settle, eod_underlying = anchor
        anchor_date = date.fromisoformat(anchor_day_str)
        pricer = DayPricer(eod_underlying, strike, anchor_date, exp_date, opt_type, eod_settle)
        if not pricer.valid:
            return None
        premium = pricer.price_at(bar_time.to_pydatetime(), spot)
        if not premium or premium <= 0:
            return None
        legs[opt_type] = {"pricer": pricer, "entry_premium": premium, "strike": strike}
    return {"legs": legs, "entry_time": bar_time}


def backtest_naked_selling(
    entry_time: dtime,
    otm_strikes: int = 0,
    leg_sl_pct: float = LEG_SL_PCT,
    lots: int = 1,
    lot_size: int = DEFAULT_LOT_SIZE,
    strategy_name: str = "naked_selling",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    candles = load_nifty_candles(interval="5m")
    if start_date:
        candles = candles[candles.index >= start_date]
    if end_date:
        candles = candles[candles.index <= end_date]
    if candles.empty:
        return {"strategy": strategy_name, "num_trades": 0, "reason": "no_underlying_data"}

    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    cycles: List[Cycle] = []
    skipped_no_pricing = 0
    skipped_no_expiry = 0
    cost_model = get_cost_model()

    days = sorted(set(candles.index.date))
    for day in days:
        day_bars = candles[(candles.index.date == day) & (candles.index.time >= entry_time)]
        if day_bars.empty:
            continue
        expiry = nearest_weekly_expiry(opt_conn, str(day))
        if not expiry:
            skipped_no_expiry += 1
            continue

        entry_bar_time = day_bars.index[0]
        entry_spot = float(day_bars.iloc[0]["close"])
        pos = _open_position(opt_conn, day, expiry, entry_spot, entry_bar_time, otm_strikes)
        if pos is None:
            skipped_no_pricing += 1
            continue

        call, put = pos["legs"]["CE"], pos["legs"]["PE"]
        exit_time = None
        exit_reason = None
        call_px = call["entry_premium"]
        put_px = put["entry_premium"]

        for bar_time, bar in day_bars.iloc[1:].iterrows():
            spot = float(bar["close"])
            call_px = call["pricer"].price_at(bar_time.to_pydatetime(), spot)
            put_px = put["pricer"].price_at(bar_time.to_pydatetime(), spot)
            is_eod = bar_time.time() >= SQUARE_OFF_TIME
            if call_px is None or put_px is None:
                if is_eod:
                    break
                continue
            leg_sl_hit = (call_px >= call["entry_premium"] * (1 + leg_sl_pct) or
                          put_px >= put["entry_premium"] * (1 + leg_sl_pct))
            if leg_sl_hit or is_eod:
                exit_time, exit_reason = bar_time, ("LEG_SL" if leg_sl_hit else "EOD")
                break

        if exit_time is None:
            skipped_no_pricing += 1
            continue

        gross = ((call["entry_premium"] - call_px) + (put["entry_premium"] - put_px)) * qty
        cost = 0.0
        for opt_type, leg, exit_px in (("CE", call, call_px), ("PE", put, put_px)):
            cost += cost_model.round_trip_cost(
                entry_turnover=leg["entry_premium"] * qty, exit_turnover=exit_px * qty,
                instrument="OPT_SELL", symbol="NIFTY", entry_side="SELL")
        net = gross - cost
        cycles.append(Cycle(
            entry_date=str(day), entry_time=str(entry_bar_time), exit_time=str(exit_time),
            exit_reason=exit_reason, call_strike=call["strike"], put_strike=put["strike"],
            call_entry=round(call["entry_premium"], 2), put_entry=round(put["entry_premium"], 2),
            call_exit=round(call_px, 2), put_exit=round(put_px, 2), qty=qty,
            gross_pnl=round(gross, 2), cost=round(cost, 2), pnl=round(net, 2),
        ))

    opt_conn.close()
    return _summarize(strategy_name, cycles, skipped_no_pricing, skipped_no_expiry, len(days), qty, lot_size, verbose)


def _summarize(name, cycles, skipped_pricing, skipped_expiry, n_days, qty, lot_size, verbose):
    n = len(cycles)
    if n == 0:
        return {"strategy": name, "num_trades": 0, "reason": "no_trades",
                "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
                "skipped_no_expiry": skipped_expiry}
    pnls = np.array([c.pnl for c in cycles])
    gross = np.array([c.gross_pnl for c in cycles])
    total_cost = float(np.array([c.cost for c in cycles]).sum())
    win_rate = float((pnls > 0).mean())
    total_pnl = float(pnls.sum())
    ret_std = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / ret_std * np.sqrt(252)) if ret_std > 0 else 0.0
    equity = np.cumsum(pnls)
    max_dd = float((np.maximum.accumulate(equity) - equity).max())
    reasons: Dict[str, int] = {}
    for c in cycles:
        reasons[c.exit_reason] = reasons.get(c.exit_reason, 0) + 1

    if verbose:
        print(f"\n{'='*60}\n{name}\n{'='*60}")
        print(f"Candidate trading days   : {n_days}")
        print(f"Trades (skipped: {skipped_pricing} no-pricing, {skipped_expiry} no-expiry): {n}")
        print(f"Win rate (net of cost)   : {win_rate:.2%}")
        print(f"Gross P&L (qty={qty}, lot={lot_size}): Rs{gross.sum():,.0f}")
        print(f"Total cost               : Rs{total_cost:,.0f}")
        print(f"NET P&L                  : Rs{total_pnl:,.0f}")
        print(f"Sharpe (net, annualized) : {sharpe:.3f}")
        print(f"Max drawdown (net)       : Rs{max_dd:,.0f}")
        print(f"Exit reasons             : {reasons}")

    return {"strategy": name, "num_trades": n, "win_rate": round(win_rate, 4),
            "gross_pnl": round(float(gross.sum()), 2), "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 2), "exit_reasons": reasons,
            "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
            "skipped_no_expiry": skipped_expiry, "qty": qty, "lot_size": lot_size,
            "trades": [c.__dict__ for c in cycles]}


def backtest_c1_short_straddle_0920(**kwargs) -> Dict[str, Any]:
    return backtest_naked_selling(entry_time=dtime(9, 20), otm_strikes=0,
                                   strategy_name="C1_short_straddle_0920", **kwargs)


def backtest_c2_short_strangle_otm(otm_strikes: int = 3, **kwargs) -> Dict[str, Any]:
    return backtest_naked_selling(entry_time=dtime(9, 30), otm_strikes=otm_strikes,
                                   strategy_name=f"C2_short_strangle_otm{otm_strikes}", **kwargs)


if __name__ == "__main__":
    backtest_c1_short_straddle_0920()
    backtest_c2_short_strangle_otm()
