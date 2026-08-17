"""
single_leg_intraday_option_backtest.py — shared engine for seminar-sourced
NIFTY single-leg (buy one CE or one PE) intraday option strategies that all
share the same shape:

  - Start watching NIFTY 5-min candles at 9:15 AM.
  - A signal function decides CE / PE / no-signal for the current bar.
  - At most ONE trade per day (no re-entry after square-off).
  - Exit on unrealized P&L hitting a rupee profit target or loss limit, else
    a fixed time-based square-off.

Individual strategies (backtest_bollinger_otm_reversal.py,
backtest_sma20_atm_option.py, ...) just supply a signal function and a
strike-selection rule; this module owns the day-loop, position tracking, and
the Black-Scholes-anchored-to-real-EOD-settle pricing (option_intraday_
pricer.py) so that P&L simulation logic isn't duplicated per strategy.

Every unpriceable signal (missing/illiquid option quote for that day's
strike) is counted and reported, never silently dropped -- otherwise the
tradeable sample silently biases toward whatever happens to be easiest to
price.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any, Callable, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from option_intraday_pricer import (
    DayPricer, load_eod_settle, nearest_weekly_expiry, nearest_strike, otm_strike,
)
from nse_cost_model import get_cost_model

DEFAULT_LOT_SIZE = 65          # current NIFTY lot size (MasterContract_NFO.csv);
                                # applied uniformly across 2020-2026 history --
                                # NOT historically accurate (lot size changed
                                # several times over that span), flagged here
                                # so results aren't read as more precise than
                                # they are.
CANDLES_DB = "candle_cache.db"
OPTIONS_DB = "options_nifty.db"
MARKET_OPEN = dtime(9, 15)


@dataclass
class Trade:
    entry_date: str
    direction: str          # "CE" or "PE"
    strike: float
    expiry: str
    entry_time: str
    entry_underlying: float
    entry_premium: float
    exit_time: str
    exit_underlying: float
    exit_premium: float
    exit_reason: str
    qty: int
    gross_pnl: float
    cost: float
    pnl: float               # net of cost -- this is what P&L stats use
    iv_used: float


def load_nifty_candles(symbol: str = "NIFTY", interval: str = "5m", db: str = CANDLES_DB) -> pd.DataFrame:
    conn = sqlite3.connect(db)
    try:
        df = pd.read_sql(
            "SELECT timestamp, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND interval=? ORDER BY timestamp", conn, params=(symbol, interval))
    finally:
        conn.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    return df


def load_nifty_5m(symbol: str = "NIFTY", db: str = CANDLES_DB) -> pd.DataFrame:
    return load_nifty_candles(symbol, "5m", db)


def _prev_trading_day_with_quote(
    conn: sqlite3.Connection, d: date, expiry: str, strike: float, opt_type: str, lookback: int = 5,
):
    """Walk back up to `lookback` calendar days to find the previous day this
    exact contract actually has a real EOD quote (handles weekends/holidays
    without hardcoding a trading calendar)."""
    from datetime import timedelta
    for i in range(1, lookback + 1):
        cand = (d - timedelta(days=i)).isoformat()
        res = load_eod_settle(conn, cand, expiry, strike, opt_type)
        if res:
            return cand, res
    return None, None


def run_single_leg_backtest(
    signal_fn: Callable[[pd.DataFrame], Optional[str]],
    strike_fn: Callable[[float, str], float],
    strategy_name: str,
    profit_target: float = 30000.0,
    loss_limit: float = -20000.0,
    square_off_time: dtime = dtime(15, 10),
    lots: int = 1,
    lot_size: int = DEFAULT_LOT_SIZE,
    min_bars_for_signal: int = 25,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sigma_shock: Union[float, Dict[str, float]] = 0.0,
    extra_cost_pct: float = 0.0,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    signal_fn(window_df) -> "CE" | "PE" | None
        Called once per 5-min bar (window_df = all bars up to and including
        the current one, oldest-first). Returning "CE"/"PE" means: enter a
        CE/PE buy at this bar's close, if no position is open today yet.

    strike_fn(spot, direction) -> strike
        e.g. lambda spot, d: nearest_strike(spot)              # ATM
             lambda spot, d: otm_strike(spot, d, 10)            # 10 OTM

    sigma_shock / extra_cost_pct: both default 0.0 (no behavior change).
    For sensitivity testing only -- perturbs the pricer's IV and/or adds
    extra round-trip cost (as a fraction of notional, both legs) on top of
    the real cost model, to check whether a result survives plausible
    pricing/execution mis-calibration rather than being a modeling artifact.

    sigma_shock may be a single float (flat, symmetric shock -- the original
    design) OR a dict like {"CE": 0.08, "PE": 0.02} to apply a DIFFERENT
    shock per entry direction -- e.g. modeling the leverage effect (IV rises
    more after a downside break than an upside one). A CE entry here means
    price just broke BELOW the lower band (a down-move triggered it); a PE
    entry means price broke ABOVE the upper band (an up-move triggered it) --
    so a larger CE-side shock is the market-microstructure-motivated
    asymmetric stress test, not an arbitrary split.
    """
    candles = load_nifty_5m()
    if start_date:
        candles = candles[candles.index >= start_date]
    if end_date:
        candles = candles[candles.index <= end_date]
    if candles.empty:
        return _empty(strategy_name, "no_underlying_data")

    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)

    trades: List[Trade] = []
    skipped_no_pricing = 0
    skipped_no_expiry = 0

    days = sorted(set(candles.index.date))
    for day in days:
        day_bars = candles[(candles.index.date == day) & (candles.index.time >= MARKET_OPEN)]
        if len(day_bars) < min_bars_for_signal + 1:
            continue

        position: Optional[Dict[str, Any]] = None
        entered_today = False

        for i in range(min_bars_for_signal, len(day_bars)):
            bar_time = day_bars.index[i]
            bar = day_bars.iloc[i]
            window = day_bars.iloc[: i + 1]

            if position is None and not entered_today:
                direction = signal_fn(window)
                if direction in ("CE", "PE"):
                    entered_today = True  # one shot per day, win or miss
                    spot = float(bar["close"])
                    expiry = nearest_weekly_expiry(opt_conn, str(day))
                    if not expiry:
                        skipped_no_expiry += 1
                        break
                    strike = strike_fn(spot, direction)
                    exp_date = date.fromisoformat(expiry)
                    anchor_day_str, anchor = _prev_trading_day_with_quote(
                        opt_conn, day, expiry, strike, direction)
                    if not anchor:
                        skipped_no_pricing += 1
                        break
                    eod_settle, eod_underlying = anchor
                    anchor_date = date.fromisoformat(anchor_day_str)
                    shock = sigma_shock.get(direction, 0.0) if isinstance(sigma_shock, dict) else sigma_shock
                    pricer = DayPricer(
                        eod_underlying, strike, anchor_date, exp_date, direction, eod_settle,
                        sigma_shock=shock)
                    if not pricer.valid:
                        skipped_no_pricing += 1
                        break
                    entry_premium = pricer.price_at(bar_time.to_pydatetime(), spot)
                    if not entry_premium or entry_premium <= 0:
                        skipped_no_pricing += 1
                        break
                    position = {
                        "direction": direction, "strike": strike, "expiry": expiry,
                        "entry_time": bar_time, "entry_underlying": spot,
                        "entry_premium": entry_premium, "pricer": pricer,
                    }
                    continue

            if position is not None:
                spot = float(bar["close"])
                premium = position["pricer"].price_at(bar_time.to_pydatetime(), spot)
                if premium is None:
                    continue
                # Exit is triggered off GROSS unrealized P&L -- that's what a
                # live P&L monitor shows (costs are only realized on close).
                unreal_pnl = (premium - position["entry_premium"]) * qty
                hit_profit = unreal_pnl >= profit_target
                hit_loss = unreal_pnl <= loss_limit
                hit_time = bar_time.time() >= square_off_time
                if hit_profit or hit_loss or hit_time:
                    reason = "PROFIT_TARGET" if hit_profit else "LOSS_LIMIT" if hit_loss else "TIME_EXIT"
                    cost_model = get_cost_model()
                    cost = cost_model.round_trip_cost(
                        entry_turnover=position["entry_premium"] * qty,
                        exit_turnover=premium * qty,
                        instrument="OPT_BUY", symbol="NIFTY", entry_side="BUY",
                    )
                    if extra_cost_pct:
                        cost += extra_cost_pct * (position["entry_premium"] + premium) * qty
                    net_pnl = unreal_pnl - cost
                    trades.append(Trade(
                        entry_date=str(day), direction=position["direction"],
                        strike=position["strike"], expiry=position["expiry"],
                        entry_time=str(position["entry_time"]),
                        entry_underlying=position["entry_underlying"],
                        entry_premium=round(position["entry_premium"], 2),
                        exit_time=str(bar_time), exit_underlying=spot,
                        exit_premium=round(premium, 2), exit_reason=reason,
                        qty=qty, gross_pnl=round(unreal_pnl, 2), cost=round(cost, 2),
                        pnl=round(net_pnl, 2),
                        iv_used=round(position["pricer"].sigma, 4),
                    ))
                    position = None
                    break  # no re-entry same day

    opt_conn.close()
    return _summarize(strategy_name, trades, skipped_no_pricing, skipped_no_expiry,
                       len(days), qty, lot_size, verbose)


def _empty(name: str, reason: str) -> Dict[str, Any]:
    return {"strategy": name, "num_trades": 0, "reason": reason}


def _summarize(name, trades, skipped_pricing, skipped_expiry, n_days, qty, lot_size, verbose):
    n = len(trades)
    if n == 0:
        return {
            "strategy": name, "num_trades": 0, "reason": "no_trades",
            "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
            "skipped_no_expiry": skipped_expiry,
        }
    pnls = np.array([t.pnl for t in trades])          # net of cost
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
        print(f"\n{'='*60}\n{name} — Single-Leg Intraday Option Backtest\n{'='*60}")
        print(f"Candidate trading days : {n_days}")
        print(f"Trades taken           : {n}  (skipped: {skipped_pricing} no-pricing, "
              f"{skipped_expiry} no-expiry)")
        print(f"Win rate (net of cost)  : {win_rate:.2%}")
        print(f"Gross P&L (qty={qty}, lot={lot_size}): Rs{total_gross_pnl:,.0f}")
        print(f"Total cost (brokerage+STT+slippage): Rs{total_cost:,.0f}")
        print(f"NET P&L                : Rs{total_pnl:,.0f}")
        print(f"Sharpe (net, annualized): {sharpe:.3f}")
        print(f"Max drawdown (net)      : Rs{max_dd:,.0f}")
        print(f"Exit reasons            : {reasons}")

    return {
        "strategy": name, "num_trades": n, "win_rate": round(win_rate, 4),
        "gross_pnl": round(total_gross_pnl, 2), "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 2), "exit_reasons": reasons,
        "candidate_days": n_days, "skipped_no_pricing": skipped_pricing,
        "skipped_no_expiry": skipped_expiry, "qty": qty, "lot_size": lot_size,
        "trades": [t.__dict__ for t in trades],
    }
