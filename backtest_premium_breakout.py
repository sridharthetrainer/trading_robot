"""
backtest_premium_breakout.py -- "Target Premium Breakout" (external strategy,
sourced from public GitHub repo udhay8005/nifty_bot, files core/strategy.py +
config.py, read 2026-09-20 as part of the "check external repos, extract
LOGIC, backtest it in our own framework" effort. Borrowing the METHODOLOGY
only -- never the repo's own claimed results, which were never independently
verified and are not trusted here).

Rules exactly as specified in that repo:
  - 9:25 (OBSERVATION): scan the option chain, find the CE strike and the PE
    strike (independently, so they may land on different strikes) whose
    current premium is closest to a target of Rs180.
  - 9:30-9:35 (ENTRY WINDOW, one shot): watch those two specific instruments.
    The first one whose premium rises above Rs180 is bought (source also
    requires a 5-second sustain re-check -- see APPROXIMATIONS). CE is
    checked before PE in the source's loop, so if both would fire in the
    same instant, CE wins -- replicated here. At most one trade per day.
  - Entry fill: triggering LTP + Rs5 buffer (source places a marketable
    limit order 5pts above the observed LTP, not a bare market fill).
  - Initial stop: entry - 20pts. Initial target: entry + 40pts.
  - Once price reaches entry+20pts, SL moves to breakeven (entry) -- once,
    never moved back down.
  - From 9:45 onward, SL trails up to the low of the previous completed
    5-min candle IN THE OPTION'S OWN PRICE -- only if that's an
    improvement (never trails down).
  - Hard time exit at 10:00 AM regardless of P&L. The entire trade
    lifecycle is at most ~30-35 minutes.
  - NOT modeled here (an account-level circuit breaker, not a per-trade
    rule): source also halts all NEW entries for the rest of the week if
    cumulative weekly P&L < -Rs10,000. Irrelevant to a per-trade backtest
    of whether the entry/exit rule itself has edge.

APPROXIMATIONS (unavoidable given this project's data: EOD-settle-anchored
Black-Scholes intraday pricer over 5-min underlying bars -- see
option_intraday_pricer.py's own docstring for why this is the
least-fabricated approach available here, not a substitute for real option
tick history):
  - The "5-second sustain" check cannot be tested at 5-min bar granularity;
    every breakout detected here is assumed to sustain. That is a bias in
    this strategy's FAVOR relative to the real bot (which would filter out
    some wick-only breakouts) -- so if this still fails, the real strategy
    is unlikely to do better.
  - Within the entry-window bar (9:30-9:34:59, the only 5-min bar fully
    inside the source's 9:30-9:35 checking loop), CE's modeled premium is
    evaluated at that bar's HIGH underlying price (the spot level that
    maximizes CE premium within the bar) and PE's at the bar's LOW
    underlying price (maximizes PE premium) -- i.e. each leg gets its
    best-case chance to trigger. Another bias in the strategy's favor.
  - Strike search radius: ATM +/- 500 points (10 strikes @ 50pt step) each
    side, per option type, per day -- wide enough that "closest to 180" is
    very unlikely to be strike-constrained rather than premium-constrained.
  - Trailing/risk-free management only evaluated from the bar AFTER entry
    onward (5-min granularity means the entry bar itself isn't re-examined
    intrabar for management).

Same real transaction-cost model (nse_cost_model.py) as every other backtest
in this batch. If this clears an initial screen, the next step is feeding it
through validation_harness.py for the full walk-forward + deflated-Sharpe +
locked-holdout treatment applied to every other strategy in this system --
a single backtest run like this one is a screen, not a validation.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, time as dtime
from typing import Any, Dict, List, Optional

import numpy as np

from option_intraday_pricer import DayPricer, nearest_strike, nearest_weekly_expiry
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, load_nifty_candles, _prev_trading_day_with_quote,
)

OPTIONS_DB = "options_nifty.db"
OBSERVATION_TIME = dtime(9, 25)
ENTRY_START = dtime(9, 30)
ENTRY_END = dtime(9, 35)          # exclusive -- only the 9:30 5-min bar qualifies
SQUARE_OFF_TIME = dtime(10, 0)
TRAIL_ACTIVATION_TIME = dtime(9, 45)
TARGET_PREMIUM = 180.0
TARGET_POINTS = 40.0
SL_POINTS = 20.0
RISK_FREE_TRIGGER = 20.0
ENTRY_BUFFER = 5.0
STRIKE_RADIUS = 10                # +/- 10 * 50pt = +/- 500pt search band


@dataclass
class Trade:
    entry_date: str
    side: str                 # "CE" or "PE"
    strike: float
    expiry: str
    entry_time: str
    entry_premium: float
    exit_time: str
    exit_premium: float
    exit_reason: str          # TARGET / SL / TIME_EXIT
    qty: int
    gross_pnl: float
    cost: float
    pnl: float


def _find_closest_strike(opt_conn, day: date, expiry: str, opt_type: str, spot: float,
                          obs_ts, radius: int = STRIKE_RADIUS) -> Optional[Dict[str, Any]]:
    """Search a strike ladder around ATM; return the pricer/strike/premium for
    whichever strike's modeled premium at `obs_ts` is closest to TARGET_PREMIUM."""
    atm = nearest_strike(spot)
    exp_date = date.fromisoformat(expiry)
    best = None
    for n in range(-radius, radius + 1):
        strike = atm + n * 50.0
        anchor_day_str, anchor = _prev_trading_day_with_quote(opt_conn, day, expiry, strike, opt_type)
        if not anchor:
            continue
        eod_settle, eod_underlying = anchor
        anchor_date = date.fromisoformat(anchor_day_str)
        pricer = DayPricer(eod_underlying, strike, anchor_date, exp_date, opt_type, eod_settle)
        if not pricer.valid:
            continue
        premium = pricer.price_at(obs_ts.to_pydatetime(), spot)
        if not premium or premium <= 0:
            continue
        diff = abs(premium - TARGET_PREMIUM)
        if best is None or diff < best[0]:
            best = (diff, pricer, strike, premium)
    if best is None:
        return None
    _, pricer, strike, premium = best
    return {"pricer": pricer, "strike": strike, "premium_at_obs": premium}


def backtest_premium_breakout(
    target_premium: float = TARGET_PREMIUM,
    target_points: float = TARGET_POINTS,
    sl_points: float = SL_POINTS,
    lots: int = 1,
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
        return {"strategy": "premium_breakout", "num_trades": 0, "reason": "no_underlying_data"}

    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    trades: List[Trade] = []
    skipped_no_pricing = 0
    skipped_no_expiry = 0
    no_breakout_days = 0
    cost_model = get_cost_model()

    days = sorted(set(candles.index.date))
    for day in days:
        day_bars = candles[(candles.index.date == day) & (candles.index.time >= dtime(9, 15)) &
                            (candles.index.time <= SQUARE_OFF_TIME)]
        if day_bars.empty:
            continue
        expiry = nearest_weekly_expiry(opt_conn, str(day))
        if not expiry:
            skipped_no_expiry += 1
            continue

        obs_bars = day_bars[day_bars.index.time == OBSERVATION_TIME]
        if obs_bars.empty:
            continue
        obs_ts = obs_bars.index[0]
        obs_spot = float(obs_bars.iloc[0]["close"])

        ce_pick = _find_closest_strike(opt_conn, day, expiry, "CE", obs_spot, obs_ts)
        pe_pick = _find_closest_strike(opt_conn, day, expiry, "PE", obs_spot, obs_ts)
        if ce_pick is None or pe_pick is None:
            skipped_no_pricing += 1
            continue

        entry_bars = day_bars[(day_bars.index.time >= ENTRY_START) & (day_bars.index.time < ENTRY_END)]
        if entry_bars.empty:
            no_breakout_days += 1
            continue
        entry_ts = entry_bars.index[0]
        entry_row = entry_bars.iloc[0]
        bar_high = float(entry_row["high"])
        bar_low = float(entry_row["low"])

        side = None
        picked = None
        trigger_premium = None
        ce_trigger = ce_pick["pricer"].price_at(entry_ts.to_pydatetime(), bar_high)
        if ce_trigger and ce_trigger > target_premium:
            side, picked, trigger_premium = "CE", ce_pick, ce_trigger
        else:
            pe_trigger = pe_pick["pricer"].price_at(entry_ts.to_pydatetime(), bar_low)
            if pe_trigger and pe_trigger > target_premium:
                side, picked, trigger_premium = "PE", pe_pick, pe_trigger

        if side is None:
            no_breakout_days += 1
            continue

        entry_premium = trigger_premium + ENTRY_BUFFER
        pricer = picked["pricer"]
        strike = picked["strike"]
        sl = entry_premium - sl_points
        target = entry_premium + target_points
        risk_free_done = False

        rest_bars = day_bars[day_bars.index > entry_ts]
        exit_premium = None
        exit_reason = None
        exit_ts = entry_ts
        prev_bar_unfav_premium = None  # previous completed bar's unfavorable-extreme premium

        for bar_ts, bar in rest_bars.iterrows():
            spot_close = float(bar["close"])
            spot_high = float(bar["high"])
            spot_low = float(bar["low"])
            is_time_exit = bar_ts.time() >= SQUARE_OFF_TIME

            if side == "CE":
                bar_prem_fav = pricer.price_at(bar_ts.to_pydatetime(), spot_high)
                bar_prem_unfav = pricer.price_at(bar_ts.to_pydatetime(), spot_low)
            else:
                bar_prem_fav = pricer.price_at(bar_ts.to_pydatetime(), spot_low)
                bar_prem_unfav = pricer.price_at(bar_ts.to_pydatetime(), spot_high)
            bar_prem_close = pricer.price_at(bar_ts.to_pydatetime(), spot_close)

            if bar_prem_fav is None or bar_prem_unfav is None or bar_prem_close is None:
                if is_time_exit:
                    exit_reason = "TIME_EXIT_NO_PRICE"
                    exit_ts = bar_ts
                    break
                continue

            if bar_prem_fav >= target:
                exit_premium, exit_reason, exit_ts = target, "TARGET", bar_ts
                break
            if bar_prem_unfav <= sl:
                sl_reason = "SL_INITIAL" if sl <= entry_premium - sl_points + 1e-9 else (
                    "SL_BREAKEVEN" if abs(sl - entry_premium) < 1e-9 else "SL_TRAILING")
                exit_premium, exit_reason, exit_ts = sl, sl_reason, bar_ts
                break
            if is_time_exit:
                exit_premium, exit_reason, exit_ts = bar_prem_close, "TIME_EXIT", bar_ts
                break

            if not risk_free_done and bar_prem_fav >= entry_premium + RISK_FREE_TRIGGER:
                if sl < entry_premium:
                    sl = entry_premium
                    risk_free_done = True

            if bar_ts.time() >= TRAIL_ACTIVATION_TIME and prev_bar_unfav_premium is not None:
                if prev_bar_unfav_premium > sl and prev_bar_unfav_premium < bar_prem_close:
                    sl = prev_bar_unfav_premium

            prev_bar_unfav_premium = bar_prem_unfav

        if exit_premium is None:
            skipped_no_pricing += 1
            continue

        cost = cost_model.round_trip_cost(
            entry_turnover=entry_premium * qty, exit_turnover=exit_premium * qty,
            instrument="OPT_BUY", symbol="NIFTY", entry_side="BUY")
        gross_pnl = (exit_premium - entry_premium) * qty
        net_pnl = gross_pnl - cost

        trades.append(Trade(
            entry_date=str(day), side=side, strike=strike, expiry=expiry,
            entry_time=str(entry_ts), entry_premium=round(entry_premium, 2),
            exit_time=str(exit_ts), exit_premium=round(exit_premium, 2),
            exit_reason=exit_reason, qty=qty,
            gross_pnl=round(gross_pnl, 2), cost=round(cost, 2), pnl=round(net_pnl, 2),
        ))

    opt_conn.close()
    return _summarize(trades, skipped_no_pricing, skipped_no_expiry, no_breakout_days,
                       len(days), qty, lot_size, verbose)


def _summarize(trades, skipped_pricing, skipped_expiry, no_breakout_days, n_days, qty, lot_size, verbose):
    n = len(trades)
    if n == 0:
        return {"strategy": "premium_breakout", "num_trades": 0, "reason": "no_trades",
                "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
                "skipped_no_expiry": skipped_expiry, "no_breakout_days": no_breakout_days}

    pnls = np.array([t.pnl for t in trades])
    gross = np.array([t.gross_pnl for t in trades])
    total_cost = float(np.array([t.cost for t in trades]).sum())
    wins = int((pnls > 0).sum())
    win_rate = wins / n
    total_pnl = float(pnls.sum())
    ret_std = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / ret_std * np.sqrt(252)) if ret_std > 0 else 0.0
    equity = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    max_dd = float((running_max - equity).max())
    reasons: Dict[str, int] = {}
    side_counts: Dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        side_counts[t.side] = side_counts.get(t.side, 0) + 1

    if verbose:
        print(f"\n{'='*60}\npremium_breakout -- Target-Premium Momentum Scalp\n{'='*60}")
        print(f"Candidate trading days   : {n_days}")
        print(f"Trades taken             : {n}  (no-breakout days: {no_breakout_days}, "
              f"skipped: {skipped_pricing} no-pricing, {skipped_expiry} no-expiry)")
        print(f"Side split               : {side_counts}")
        print(f"Win rate (net of cost)   : {win_rate:.2%}")
        print(f"Gross P&L (qty={qty}, lot={lot_size}): Rs{gross.sum():,.0f}")
        print(f"Total cost               : Rs{total_cost:,.0f}")
        print(f"NET P&L                  : Rs{total_pnl:,.0f}")
        print(f"Sharpe (net, annualized) : {sharpe:.3f}")
        print(f"Max drawdown (net)       : Rs{max_dd:,.0f}")
        print(f"Exit reasons             : {reasons}")

    return {
        "strategy": "premium_breakout", "num_trades": n, "win_rate": round(win_rate, 4),
        "gross_pnl": round(float(gross.sum()), 2), "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 2), "exit_reasons": reasons, "side_split": side_counts,
        "candidate_days": n_days, "no_breakout_days": no_breakout_days,
        "skipped_no_pricing": skipped_pricing, "skipped_no_expiry": skipped_expiry,
        "qty": qty, "lot_size": lot_size,
        "trades": [t.__dict__ for t in trades],
    }


if __name__ == "__main__":
    backtest_premium_breakout()
