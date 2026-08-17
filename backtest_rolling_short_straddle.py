"""
backtest_rolling_short_straddle.py — "One Time Daily Rolling Straddle"
(seminar strategy, 2026-08-11).

Rules as given (with the two contradictions resolved per user confirmation):
  - 10:00 AM: sell ATM CE + sell ATM PE (10 lots each).
  - Combined P&L hits +/-Rs15,000 -> close both legs, wait 15 minutes
    (confirmed over the summary line's conflicting "5 minutes"), redeploy a
    fresh ATM straddle at the then-current spot. Repeats all day.
  - If EITHER leg's premium rises 20% from its own entry (a loss on a short
    leg) -> close both legs and immediately redeploy a fresh ATM straddle.
    This specific trigger fires AT MOST ONCE per day (stated explicitly);
    the P&L-threshold cycle above keeps operating independently afterward.
  - No square-off time or overall daily cap was stated anywhere in this
    strategy (unlike the other 9) -> confirmed: cycle until market close
    (3:30 PM), no separate total-day P&L cap beyond the per-cycle +-15,000.
  - No candle timeframe was stated either -> defaults to 5-min, consistent
    with every other strategy from this seminar and the primary dataset
    used throughout (candle_cache.db).

This is a genuinely different shape from the other seminar strategies (short
premium, recurring multi-cycle re-entry within a single day, a separate
leg-level stop), so it has its own day-loop rather than forcing it through
single_leg/multi_leg_intraday_option_backtest.py's one-shot-per-day model.
Same Black-Scholes-anchored-to-real-EOD-settle pricing and real transaction
costs (nse_cost_model.py) as everything else in this batch.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from option_intraday_pricer import DayPricer, nearest_strike, nearest_weekly_expiry
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, load_nifty_candles, _prev_trading_day_with_quote,
)

OPTIONS_DB = "options_nifty.db"
ENTRY_TIME = dtime(10, 0)
MARKET_CLOSE = dtime(15, 30)
COOLDOWN = timedelta(minutes=15)
CYCLE_PROFIT = 15000.0
CYCLE_LOSS = -15000.0
LEG_SL_PCT = 0.20


@dataclass
class Cycle:
    entry_date: str
    entry_time: str
    exit_time: str
    exit_reason: str          # PNL_TARGET / PNL_LOSS / LEG_SL / EOD
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


def _open_straddle(opt_conn, day: date, expiry: str, spot: float, bar_time):
    strike = nearest_strike(spot)
    exp_date = date.fromisoformat(expiry)
    legs = {}
    for opt_type in ("CE", "PE"):
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
        legs[opt_type] = {"pricer": pricer, "entry_premium": premium}
    return {"strike": strike, "legs": legs, "entry_time": bar_time}


def backtest_rolling_short_straddle(
    cycle_profit: float = CYCLE_PROFIT,
    cycle_loss: float = CYCLE_LOSS,
    leg_sl_pct: float = LEG_SL_PCT,
    lots: int = 10,
    lot_size: int = DEFAULT_LOT_SIZE,
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
        return {"strategy": "rolling_short_straddle", "num_trades": 0, "reason": "no_underlying_data"}

    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    cycles: List[Cycle] = []
    skipped_no_pricing = 0
    skipped_no_expiry = 0
    cost_model = get_cost_model()

    days = sorted(set(candles.index.date))
    for day in days:
        day_bars = candles[(candles.index.date == day) & (candles.index.time >= dtime(9, 15))]
        if day_bars.empty:
            continue
        expiry = nearest_weekly_expiry(opt_conn, str(day))
        if not expiry:
            skipped_no_expiry += 1
            continue

        entry_bars = day_bars[day_bars.index.time >= ENTRY_TIME]
        if entry_bars.empty:
            continue

        idx = 0
        bar_list = list(entry_bars.iterrows())
        straddle = None
        leg_sl_used_today = False
        cooldown_until: Optional[pd.Timestamp] = None

        while idx < len(bar_list):
            bar_time, bar = bar_list[idx]
            spot = float(bar["close"])

            if straddle is None:
                if cooldown_until is not None and bar_time < cooldown_until:
                    idx += 1
                    continue
                straddle = _open_straddle(opt_conn, day, expiry, spot, bar_time)
                if straddle is None:
                    skipped_no_pricing += 1
                    idx += 1
                    if idx >= len(bar_list):
                        break
                    continue
                idx += 1
                continue

            call = straddle["legs"]["CE"]
            put = straddle["legs"]["PE"]
            call_px = call["pricer"].price_at(bar_time.to_pydatetime(), spot)
            put_px = put["pricer"].price_at(bar_time.to_pydatetime(), spot)
            is_eod = bar_time.time() >= MARKET_CLOSE
            if call_px is None or put_px is None:
                if is_eod:
                    break  # can't price the final close -- drop this dangling cycle
                idx += 1
                continue

            combined_pnl = ((call["entry_premium"] - call_px) + (put["entry_premium"] - put_px)) * qty
            leg_sl_hit = (not leg_sl_used_today) and (
                call_px >= call["entry_premium"] * (1 + leg_sl_pct) or
                put_px >= put["entry_premium"] * (1 + leg_sl_pct)
            )
            pnl_target_hit = combined_pnl >= cycle_profit
            pnl_loss_hit = combined_pnl <= cycle_loss

            if pnl_target_hit or pnl_loss_hit or leg_sl_hit or is_eod:
                reason = ("EOD" if is_eod else
                          "LEG_SL" if leg_sl_hit else
                          "PNL_TARGET" if pnl_target_hit else "PNL_LOSS")
                cost = 0.0
                for opt_type, leg, exit_px in (("CE", call, call_px), ("PE", put, put_px)):
                    cost += cost_model.round_trip_cost(
                        entry_turnover=leg["entry_premium"] * qty, exit_turnover=exit_px * qty,
                        instrument="OPT_SELL", symbol="NIFTY", entry_side="SELL")
                net_pnl = combined_pnl - cost
                cycles.append(Cycle(
                    entry_date=str(day), entry_time=str(straddle["entry_time"]),
                    exit_time=str(bar_time), exit_reason=reason,
                    call_strike=straddle["strike"], put_strike=straddle["strike"],
                    call_entry=round(call["entry_premium"], 2), put_entry=round(put["entry_premium"], 2),
                    call_exit=round(call_px, 2), put_exit=round(put_px, 2),
                    qty=qty, gross_pnl=round(combined_pnl, 2), cost=round(cost, 2),
                    pnl=round(net_pnl, 2),
                ))
                straddle = None
                if reason == "LEG_SL":
                    leg_sl_used_today = True
                    cooldown_until = None   # immediate redeploy, no stated cooldown for this path
                elif reason == "EOD":
                    break
                else:
                    cooldown_until = bar_time + COOLDOWN
            idx += 1

    opt_conn.close()
    return _summarize(cycles, skipped_no_pricing, skipped_no_expiry, len(days), qty, lot_size, verbose)


def _summarize(cycles, skipped_pricing, skipped_expiry, n_days, qty, lot_size, verbose):
    n = len(cycles)
    if n == 0:
        return {"strategy": "rolling_short_straddle", "num_trades": 0, "reason": "no_trades",
                "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
                "skipped_no_expiry": skipped_expiry}
    pnls = np.array([c.pnl for c in cycles])
    gross = np.array([c.gross_pnl for c in cycles])
    total_cost = float(np.array([c.cost for c in cycles]).sum())
    wins = int((pnls > 0).sum())
    win_rate = wins / n
    total_pnl = float(pnls.sum())
    ret_std = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / ret_std * np.sqrt(252)) if ret_std > 0 else 0.0
    equity = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    max_dd = float((running_max - equity).max())
    reasons = {}
    for c in cycles:
        reasons[c.exit_reason] = reasons.get(c.exit_reason, 0) + 1

    if verbose:
        print(f"\n{'='*60}\nrolling_short_straddle — Multi-Cycle Intraday Backtest\n{'='*60}")
        print(f"Candidate trading days  : {n_days}")
        print(f"Cycles (straddle deployments): {n}  (skipped: {skipped_pricing} no-pricing, "
              f"{skipped_expiry} no-expiry)")
        print(f"Win rate (net of cost)   : {win_rate:.2%}")
        print(f"Gross P&L (qty={qty}, lot={lot_size}): Rs{gross.sum():,.0f}")
        print(f"Total cost               : Rs{total_cost:,.0f}")
        print(f"NET P&L                 : Rs{total_pnl:,.0f}")
        print(f"Sharpe (net, annualized) : {sharpe:.3f}")
        print(f"Max drawdown (net)       : Rs{max_dd:,.0f}")
        print(f"Exit reasons             : {reasons}")

    return {
        "strategy": "rolling_short_straddle", "num_trades": n, "win_rate": round(win_rate, 4),
        "gross_pnl": round(float(gross.sum()), 2), "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 2), "exit_reasons": reasons,
        "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
        "skipped_no_expiry": skipped_expiry, "qty": qty, "lot_size": lot_size,
        "trades": [c.__dict__ for c in cycles],
    }


if __name__ == "__main__":
    backtest_rolling_short_straddle()
