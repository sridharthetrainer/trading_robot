"""
multi_leg_intraday_option_backtest.py — shared engine for seminar-sourced
NIFTY multi-leg (straddle / synthetic future / iron condor) intraday option
strategies, generalizing single_leg_intraday_option_backtest.py to N legs
(each independently BUY or SELL, own strike rule).

Same foundations as the single-leg engine: real NIFTY 5-min underlying
(candle_cache.db), Black-Scholes pricing anchored to the PREVIOUS trading
day's real EOD settle per leg (option_intraday_pricer.py, no lookahead),
real transaction costs (nse_cost_model.py) applied per leg at close, every
unpriceable candidate day counted and reported rather than silently dropped.

Combined P&L = sum of each leg's signed P&L:
  BUY leg:  (current_premium - entry_premium) * qty
  SELL leg: (entry_premium - current_premium) * qty
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from option_intraday_pricer import DayPricer
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, MARKET_OPEN, load_nifty_candles, _prev_trading_day_with_quote,
)
from option_intraday_pricer import nearest_weekly_expiry

OPTIONS_DB = "options_nifty.db"


@dataclass
class LegSpec:
    name: str                                    # e.g. "call_leg", "put_leg"
    side: str                                     # "BUY" or "SELL"
    opt_type: str                                  # "CE" or "PE"
    strike_fn: Callable[[float], float]            # spot -> strike


@dataclass
class MultiLegTrade:
    entry_date: str
    entry_time: str
    exit_time: str
    exit_reason: str
    legs: List[Dict[str, Any]]     # per-leg entry/exit/strike/premium detail
    qty: int
    gross_pnl: float
    cost: float
    pnl: float


def _open_leg(
    opt_conn: sqlite3.Connection, spec: LegSpec, day: date, expiry: str, spot: float,
) -> Optional[Dict[str, Any]]:
    strike = spec.strike_fn(spot)
    exp_date = date.fromisoformat(expiry)
    anchor_day_str, anchor = _prev_trading_day_with_quote(
        opt_conn, day, expiry, strike, spec.opt_type)
    if not anchor:
        return None
    eod_settle, eod_underlying = anchor
    anchor_date = date.fromisoformat(anchor_day_str)
    pricer = DayPricer(eod_underlying, strike, anchor_date, exp_date, spec.opt_type, eod_settle)
    if not pricer.valid:
        return None
    return {"spec": spec, "strike": strike, "pricer": pricer}


def run_multi_leg_backtest(
    entry_fn: Callable[[pd.DataFrame], bool],
    leg_specs: List[LegSpec],
    strategy_name: str,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    entry_deadline: Optional[dtime] = None,
    square_off_time: dtime = dtime(15, 25),
    extra_exit_fn: Optional[Callable[[pd.DataFrame], bool]] = None,
    lots: int = 10,
    lot_size: int = DEFAULT_LOT_SIZE,
    min_bars_for_signal: int = 15,
    candle_interval: str = "5m",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    candles = load_nifty_candles(interval=candle_interval)
    if start_date:
        candles = candles[candles.index >= start_date]
    if end_date:
        candles = candles[candles.index <= end_date]
    if candles.empty:
        return {"strategy": strategy_name, "num_trades": 0, "reason": "no_underlying_data"}

    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    trades: List[MultiLegTrade] = []
    skipped_no_pricing = 0
    skipped_no_expiry = 0
    no_entry_by_deadline = 0

    days = sorted(set(candles.index.date))
    for day in days:
        day_bars = candles[(candles.index.date == day) & (candles.index.time >= MARKET_OPEN)]
        if len(day_bars) < min_bars_for_signal + 1:
            continue

        legs_open: Optional[List[Dict[str, Any]]] = None
        entry_bar_time = None
        entered_today = False
        deadline_missed = False

        for i in range(min_bars_for_signal, len(day_bars)):
            bar_time = day_bars.index[i]
            bar = day_bars.iloc[i]
            window = day_bars.iloc[: i + 1]

            if legs_open is None and not entered_today:
                if entry_deadline and bar_time.time() >= entry_deadline:
                    if not deadline_missed:
                        no_entry_by_deadline += 1
                        deadline_missed = True
                    continue
                if entry_fn(window):
                    entered_today = True
                    spot = float(bar["close"])
                    expiry = nearest_weekly_expiry(opt_conn, str(day))
                    if not expiry:
                        skipped_no_expiry += 1
                        break
                    opened = []
                    ok = True
                    for spec in leg_specs:
                        leg = _open_leg(opt_conn, spec, day, expiry, spot)
                        if leg is None:
                            ok = False
                            break
                        leg["entry_premium"] = leg["pricer"].price_at(bar_time.to_pydatetime(), spot)
                        if not leg["entry_premium"] or leg["entry_premium"] <= 0:
                            ok = False
                            break
                        opened.append(leg)
                    if not ok:
                        skipped_no_pricing += 1
                        break
                    legs_open = opened
                    entry_bar_time = bar_time
                    continue

            if legs_open is not None:
                spot = float(bar["close"])
                premiums = []
                combined_pnl = 0.0
                valid = True
                for leg in legs_open:
                    px = leg["pricer"].price_at(bar_time.to_pydatetime(), spot)
                    if px is None:
                        valid = False
                        break
                    premiums.append(px)
                    signed = (px - leg["entry_premium"]) if leg["spec"].side == "BUY" \
                        else (leg["entry_premium"] - px)
                    combined_pnl += signed * qty
                if not valid:
                    continue

                hit_profit = combined_pnl >= profit_target
                hit_loss = combined_pnl <= loss_limit
                hit_time = bar_time.time() >= square_off_time
                hit_extra = bool(extra_exit_fn and extra_exit_fn(window))

                if hit_profit or hit_loss or hit_time or hit_extra:
                    reason = ("PROFIT_TARGET" if hit_profit else
                              "LOSS_LIMIT" if hit_loss else
                              "EXTRA_EXIT" if hit_extra else "TIME_EXIT")
                    cost_model = get_cost_model()
                    total_cost = 0.0
                    leg_details = []
                    for leg, exit_px in zip(legs_open, premiums):
                        entry_side = leg["spec"].side
                        c = cost_model.round_trip_cost(
                            entry_turnover=leg["entry_premium"] * qty,
                            exit_turnover=exit_px * qty,
                            instrument="OPT_BUY" if entry_side == "BUY" else "OPT_SELL",
                            symbol="NIFTY", entry_side=entry_side,
                        )
                        total_cost += c
                        leg_details.append({
                            "name": leg["spec"].name, "side": entry_side,
                            "opt_type": leg["spec"].opt_type, "strike": leg["strike"],
                            "entry_premium": round(leg["entry_premium"], 2),
                            "exit_premium": round(exit_px, 2),
                        })
                    net_pnl = combined_pnl - total_cost
                    trades.append(MultiLegTrade(
                        entry_date=str(day), entry_time=str(entry_bar_time),
                        exit_time=str(bar_time), exit_reason=reason,
                        legs=leg_details, qty=qty,
                        gross_pnl=round(combined_pnl, 2), cost=round(total_cost, 2),
                        pnl=round(net_pnl, 2),
                    ))
                    legs_open = None
                    break  # no re-entry same day (multi-leg strategies here are all single-shot)

    opt_conn.close()
    return _summarize(strategy_name, trades, skipped_no_pricing, skipped_no_expiry,
                       no_entry_by_deadline, len(days), qty, lot_size, verbose)


def _summarize(name, trades, skipped_pricing, skipped_expiry, no_entry_deadline,
                n_days, qty, lot_size, verbose):
    n = len(trades)
    if n == 0:
        return {
            "strategy": name, "num_trades": 0, "reason": "no_trades",
            "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
            "skipped_no_expiry": skipped_expiry, "no_entry_by_deadline": no_entry_deadline,
        }
    pnls = np.array([t.pnl for t in trades])
    gross_pnls = np.array([t.gross_pnl for t in trades])
    total_cost = float(np.array([t.cost for t in trades]).sum())
    wins = int((pnls > 0).sum())
    total_pnl = float(pnls.sum())
    total_gross_pnl = float(gross_pnls.sum())
    win_rate = wins / n
    ret_std = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / ret_std * np.sqrt(252)) if ret_std > 0 else 0.0
    equity = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    max_dd = float((running_max - equity).max())
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    if verbose:
        print(f"\n{'='*60}\n{name} — Multi-Leg Intraday Option Backtest\n{'='*60}")
        print(f"Candidate trading days  : {n_days}")
        print(f"Trades taken            : {n}  (skipped: {skipped_pricing} no-pricing, "
              f"{skipped_expiry} no-expiry, {no_entry_deadline} no-entry-by-deadline)")
        print(f"Win rate (net of cost)   : {win_rate:.2%}")
        print(f"Gross P&L (qty={qty}, lot={lot_size}): Rs{total_gross_pnl:,.0f}")
        print(f"Total cost               : Rs{total_cost:,.0f}")
        print(f"NET P&L                 : Rs{total_pnl:,.0f}")
        print(f"Sharpe (net, annualized) : {sharpe:.3f}")
        print(f"Max drawdown (net)       : Rs{max_dd:,.0f}")
        print(f"Exit reasons             : {reasons}")

    return {
        "strategy": name, "num_trades": n, "win_rate": round(win_rate, 4),
        "gross_pnl": round(total_gross_pnl, 2), "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 2), "exit_reasons": reasons,
        "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
        "skipped_no_expiry": skipped_expiry, "no_entry_by_deadline": no_entry_deadline,
        "qty": qty, "lot_size": lot_size,
        "trades": [{**t.__dict__} for t in trades],
    }
