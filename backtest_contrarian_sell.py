"""
backtest_contrarian_sell.py -- tests whether SELLING the OPPOSITE option
to our own confluence engine's real historical directional calls shows
edge, using REAL option premium (not the underlying-proxy R-multiple
used in the aggregate/per-strategy inversion check the same day).

Hypothesis (user, 2026-09-21): if our system's directional signal isn't
just uninformative but actively unhelpful, could selling AGAINST it --
e.g. sell a PE (collect premium betting the market won't fall) when the
signal said BUY (bullish) -- show real edge? Genuinely different from
the earlier inversion check: that flipped BUY<->SELL and still BOUGHT
the flipped side (long premium, pays theta); this SELLS the opposite
side (short premium, collects theta) -- a different risk/reward shape
entirely, not reducible to a sign-flip of the same R-multiple.

Uses REAL historical signal_log (signal_date, signal_time, side) for
NIFTY only (301 training-eligible signals -- signal_log doesn't store
the exact strike/expiry actually used per signal, so both are
re-derived fresh at signal time: ATM strike via nearest_strike(spot),
nearest weekly expiry, both real-data-driven, same convention as every
other backtest in this project). Real EOD-settle-anchored Black-Scholes
intraday pricer (option_intraday_pricer.py), real transaction costs
(nse_cost_model.py, OPT_SELL side), same leg stop-loss / EOD square-off
convention as naked_straddle_strangle_backtest.py (30% premium rise ->
stop, else 3:10pm square-off) -- single short leg, not a straddle.

Small sample (n<=301) -- flagged prominently, not padded.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from option_intraday_pricer import DayPricer, nearest_strike, nearest_weekly_expiry
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, load_nifty_candles, _prev_trading_day_with_quote,
)

OPTIONS_DB = "options_nifty.db"
SIGNAL_DB = "signal_log.db"
SQUARE_OFF_TIME = dtime(15, 10)
LEG_SL_PCT = 0.30


@dataclass
class ContrarianTrade:
    signal_date: str
    signal_time: str
    original_side: str      # the REAL signal's side (BUY/SELL)
    sold_type: str           # "PE" if original BUY, "CE" if original SELL
    strike: float
    expiry: str
    entry_time: str
    entry_premium: float
    exit_time: str
    exit_premium: float
    exit_reason: str          # LEG_SL / EOD
    qty: int
    gross_pnl: float
    cost: float
    pnl: float


def _load_real_signals() -> List[Dict[str, Any]]:
    con = sqlite3.connect(SIGNAL_DB)
    rows = con.execute(
        "SELECT signal_date, signal_time, side FROM signal_log "
        "WHERE training_eligible=1 AND symbol='NIFTY' AND side IN ('BUY','SELL') "
        "ORDER BY signal_date, signal_time").fetchall()
    con.close()
    return [{"signal_date": r[0], "signal_time": r[1], "side": r[2]} for r in rows]


def backtest_contrarian_sell(
    leg_sl_pct: float = LEG_SL_PCT,
    lots: int = 1,
    lot_size: int = DEFAULT_LOT_SIZE,
    verbose: bool = True,
) -> Dict[str, Any]:
    signals = _load_real_signals()
    candles = load_nifty_candles(interval="5m")
    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    cost_model = get_cost_model()

    trades: List[ContrarianTrade] = []
    skipped_no_pricing = 0
    skipped_no_expiry = 0
    skipped_no_candle = 0

    for sig in signals:
        d_str, t_str, side = sig["signal_date"], sig["signal_time"], sig["side"]
        try:
            day = date.fromisoformat(d_str)
        except ValueError:
            skipped_no_pricing += 1
            continue
        sold_type = "PE" if side == "BUY" else "CE"   # opposite of the original call

        day_bars = candles[(candles.index.date == day) & (candles.index.time >= dtime(9, 15))]
        if day_bars.empty:
            skipped_no_candle += 1
            continue
        # locate the entry bar at/just after signal_time
        try:
            sig_time = datetime.strptime(t_str[:8], "%H:%M:%S").time()
        except ValueError:
            skipped_no_candle += 1
            continue
        entry_bars = day_bars[day_bars.index.time >= sig_time]
        if entry_bars.empty:
            skipped_no_candle += 1
            continue
        entry_ts = entry_bars.index[0]
        spot = float(entry_bars.iloc[0]["close"])

        expiry = nearest_weekly_expiry(opt_conn, d_str)
        if not expiry:
            skipped_no_expiry += 1
            continue
        strike = nearest_strike(spot)
        exp_date = date.fromisoformat(expiry)
        anchor_day_str, anchor = _prev_trading_day_with_quote(opt_conn, day, expiry, strike, sold_type)
        if not anchor:
            skipped_no_pricing += 1
            continue
        eod_settle, eod_underlying = anchor
        anchor_date = date.fromisoformat(anchor_day_str)
        pricer = DayPricer(eod_underlying, strike, anchor_date, exp_date, sold_type, eod_settle)
        if not pricer.valid:
            skipped_no_pricing += 1
            continue
        entry_premium = pricer.price_at(entry_ts.to_pydatetime(), spot)
        if not entry_premium or entry_premium <= 0:
            skipped_no_pricing += 1
            continue

        exit_time = None
        exit_reason = None
        exit_premium = entry_premium
        for bar_ts, bar in day_bars[day_bars.index > entry_ts].iterrows():
            bar_spot = float(bar["close"])
            px = pricer.price_at(bar_ts.to_pydatetime(), bar_spot)
            is_eod = bar_ts.time() >= SQUARE_OFF_TIME
            if px is None:
                if is_eod:
                    break
                continue
            leg_sl_hit = px >= entry_premium * (1 + leg_sl_pct)
            if leg_sl_hit or is_eod:
                exit_time, exit_reason, exit_premium = bar_ts, ("LEG_SL" if leg_sl_hit else "EOD"), px
                break

        if exit_time is None:
            skipped_no_pricing += 1
            continue

        cost = cost_model.round_trip_cost(
            entry_turnover=entry_premium * qty, exit_turnover=exit_premium * qty,
            instrument="OPT_SELL", symbol="NIFTY", entry_side="SELL")
        gross = (entry_premium - exit_premium) * qty
        net = gross - cost
        trades.append(ContrarianTrade(
            signal_date=d_str, signal_time=t_str, original_side=side, sold_type=sold_type,
            strike=strike, expiry=expiry, entry_time=str(entry_ts), entry_premium=round(entry_premium, 2),
            exit_time=str(exit_time), exit_premium=round(exit_premium, 2), exit_reason=exit_reason,
            qty=qty, gross_pnl=round(gross, 2), cost=round(cost, 2), pnl=round(net, 2),
        ))

    opt_conn.close()
    return _summarize(trades, skipped_no_pricing, skipped_no_expiry, skipped_no_candle, len(signals), qty, lot_size, verbose)


def _summarize(trades, skip_price, skip_exp, skip_candle, n_signals, qty, lot_size, verbose):
    n = len(trades)
    if n == 0:
        return {"strategy": "contrarian_sell", "num_trades": 0, "reason": "no_trades",
                "n_signals": n_signals, "skipped_no_pricing": skip_price,
                "skipped_no_expiry": skip_exp, "skipped_no_candle": skip_candle}
    pnls = np.array([t.pnl for t in trades])
    gross = np.array([t.gross_pnl for t in trades])
    total_cost = float(np.array([t.cost for t in trades]).sum())
    win_rate = float((pnls > 0).mean())
    total_pnl = float(pnls.sum())
    ret_std = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / ret_std * np.sqrt(252)) if ret_std > 0 else 0.0
    equity = np.cumsum(pnls)
    max_dd = float((np.maximum.accumulate(equity) - equity).max())
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    if verbose:
        print(f"\n{'='*60}\ncontrarian_sell -- Sell Opposite Option to Our Own Signal\n{'='*60}")
        print(f"Real signals available: {n_signals}  Trades: {n}  "
              f"(skipped: {skip_price} no-pricing, {skip_exp} no-expiry, {skip_candle} no-candle)")
        print(f"Win rate (net of cost)   : {win_rate:.2%}")
        print(f"Gross P&L (qty={qty}, lot={lot_size}): Rs{gross.sum():,.0f}")
        print(f"Total cost               : Rs{total_cost:,.0f}")
        print(f"NET P&L                  : Rs{total_pnl:,.0f}")
        print(f"Sharpe (net, annualized) : {sharpe:.3f}")
        print(f"Max drawdown (net)       : Rs{max_dd:,.0f}")
        print(f"Exit reasons             : {reasons}")

    return {"strategy": "contrarian_sell", "num_trades": n, "win_rate": round(win_rate, 4),
            "gross_pnl": round(float(gross.sum()), 2), "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 2), "exit_reasons": reasons,
            "n_signals": n_signals, "skipped_no_pricing": skip_price,
            "skipped_no_expiry": skip_exp, "skipped_no_candle": skip_candle,
            "qty": qty, "lot_size": lot_size, "trades": [t.__dict__ for t in trades]}


if __name__ == "__main__":
    backtest_contrarian_sell()
