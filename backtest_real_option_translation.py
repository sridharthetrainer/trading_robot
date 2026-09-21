"""
backtest_real_option_translation.py -- RESEARCH_QUEUE_2026-09-13.md item 5,
"Option-level P&L translation". Every R-multiple discussed elsewhere in
this project is computed on the UNDERLYING's price movement as a proxy
(triple_barrier.py's own docstring: this excludes real option execution
costs -- bid/ask spread, IV, theta, delta/DTE selection -- "measured
separately"). This module builds the translation layer: for our own
REAL historical confluence signals, buy the option our system would
ACTUALLY buy (CE on BUY/bullish, PE on SELL/bearish -- the real,
non-inverted, non-contrarian direction, unlike backtest_contrarian_
sell.py's opposite-side test), price it with the real EOD-settle-
anchored Black-Scholes intraday pricer, and compare against the
underlying-proxy tb_r_multiple_net for the SAME signals.

Same NIFTY-only real signal set as backtest_contrarian_sell.py (301
training-eligible signals, strike/expiry re-derived at signal time --
signal_log doesn't store the exact contract used per signal). Same
30% unrealized-loss stop / 3:10pm square-off convention as every other
single-leg backtest in this batch, for direct methodological
comparability.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any, Dict, List, Optional

import numpy as np

from option_intraday_pricer import DayPricer, nearest_strike, nearest_weekly_expiry
from nse_cost_model import get_cost_model
from single_leg_intraday_option_backtest import (
    DEFAULT_LOT_SIZE, load_nifty_candles, _prev_trading_day_with_quote,
)

OPTIONS_DB = "options_nifty.db"
SIGNAL_DB = "signal_log.db"
SQUARE_OFF_TIME = dtime(15, 10)
LOSS_LIMIT_PCT = 0.30    # unrealized premium LOSS limit (long option), symmetric
                          # convention to the selling backtests' 30% leg-SL


@dataclass
class RealOptionTrade:
    signal_date: str
    signal_time: str
    side: str
    bought_type: str     # "CE" on BUY, "PE" on SELL -- the REAL, intended direction
    strike: float
    expiry: str
    entry_premium: float
    exit_premium: float
    exit_reason: str
    qty: int
    gross_pnl: float
    cost: float
    pnl: float
    underlying_proxy_r: Optional[float]   # tb_r_multiple_net for the SAME signal, for comparison


def _load_real_signals_with_proxy() -> List[Dict[str, Any]]:
    con = sqlite3.connect(SIGNAL_DB)
    rows = con.execute(
        "SELECT signal_date, signal_time, side, tb_r_multiple_net FROM signal_log "
        "WHERE training_eligible=1 AND symbol='NIFTY' AND side IN ('BUY','SELL') "
        "ORDER BY signal_date, signal_time").fetchall()
    con.close()
    return [{"signal_date": r[0], "signal_time": r[1], "side": r[2], "proxy_r": r[3]} for r in rows]


def backtest_real_option_translation(
    loss_limit_pct: float = LOSS_LIMIT_PCT,
    lots: int = 1,
    lot_size: int = DEFAULT_LOT_SIZE,
    verbose: bool = True,
) -> Dict[str, Any]:
    signals = _load_real_signals_with_proxy()
    candles = load_nifty_candles(interval="5m")
    qty = lots * lot_size
    opt_conn = sqlite3.connect(OPTIONS_DB)
    cost_model = get_cost_model()

    trades: List[RealOptionTrade] = []
    skipped = 0

    for sig in signals:
        d_str, t_str, side, proxy_r = sig["signal_date"], sig["signal_time"], sig["side"], sig["proxy_r"]
        try:
            day = date.fromisoformat(d_str)
        except ValueError:
            skipped += 1
            continue
        bought_type = "CE" if side == "BUY" else "PE"   # the REAL, intended direction

        day_bars = candles[(candles.index.date == day) & (candles.index.time >= dtime(9, 15))]
        if day_bars.empty:
            skipped += 1
            continue
        try:
            sig_time = datetime.strptime(t_str[:8], "%H:%M:%S").time()
        except ValueError:
            skipped += 1
            continue
        entry_bars = day_bars[day_bars.index.time >= sig_time]
        if entry_bars.empty:
            skipped += 1
            continue
        entry_ts = entry_bars.index[0]
        spot = float(entry_bars.iloc[0]["close"])

        expiry = nearest_weekly_expiry(opt_conn, d_str)
        if not expiry:
            skipped += 1
            continue
        strike = nearest_strike(spot)
        exp_date = date.fromisoformat(expiry)
        anchor_day_str, anchor = _prev_trading_day_with_quote(opt_conn, day, expiry, strike, bought_type)
        if not anchor:
            skipped += 1
            continue
        eod_settle, eod_underlying = anchor
        anchor_date = date.fromisoformat(anchor_day_str)
        pricer = DayPricer(eod_underlying, strike, anchor_date, exp_date, bought_type, eod_settle)
        if not pricer.valid:
            skipped += 1
            continue
        entry_premium = pricer.price_at(entry_ts.to_pydatetime(), spot)
        if not entry_premium or entry_premium <= 0:
            skipped += 1
            continue

        exit_premium = None
        exit_reason = None
        for bar_ts, bar in day_bars[day_bars.index > entry_ts].iterrows():
            bar_spot = float(bar["close"])
            px = pricer.price_at(bar_ts.to_pydatetime(), bar_spot)
            is_eod = bar_ts.time() >= SQUARE_OFF_TIME
            if px is None:
                if is_eod:
                    break
                continue
            loss_hit = px <= entry_premium * (1 - loss_limit_pct)
            if loss_hit or is_eod:
                exit_premium, exit_reason = px, ("LOSS_LIMIT" if loss_hit else "EOD")
                break

        if exit_premium is None:
            skipped += 1
            continue

        cost = cost_model.round_trip_cost(
            entry_turnover=entry_premium * qty, exit_turnover=exit_premium * qty,
            instrument="OPT_BUY", symbol="NIFTY", entry_side="BUY")
        gross = (exit_premium - entry_premium) * qty
        net = gross - cost
        trades.append(RealOptionTrade(
            signal_date=d_str, signal_time=t_str, side=side, bought_type=bought_type,
            strike=strike, expiry=expiry, entry_premium=round(entry_premium, 2),
            exit_premium=round(exit_premium, 2), exit_reason=exit_reason, qty=qty,
            gross_pnl=round(gross, 2), cost=round(cost, 2), pnl=round(net, 2),
            underlying_proxy_r=proxy_r,
        ))

    opt_conn.close()
    return _summarize(trades, skipped, len(signals), qty, lot_size, verbose)


def _summarize(trades, skipped, n_signals, qty, lot_size, verbose):
    n = len(trades)
    if n == 0:
        return {"strategy": "real_option_translation", "num_trades": 0,
                "n_signals": n_signals, "skipped": skipped}
    pnls = np.array([t.pnl for t in trades])
    proxy_rs = np.array([t.underlying_proxy_r for t in trades if t.underlying_proxy_r is not None])
    win_rate = float((pnls > 0).mean())
    total_pnl = float(pnls.sum())
    ret_std = pnls.std(ddof=1) if n > 1 else 0.0
    sharpe = float(pnls.mean() / ret_std * np.sqrt(252)) if ret_std > 0 else 0.0
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    proxy_win_rate = float((proxy_rs > 0).mean()) if len(proxy_rs) else None
    proxy_mean = float(proxy_rs.mean()) if len(proxy_rs) else None

    if verbose:
        print(f"\n{'='*60}\nreal_option_translation -- Real-Premia P&L for Our OWN Signals\n{'='*60}")
        print(f"Real signals: {n_signals}  Trades priced: {n}  (skipped: {skipped})")
        print(f"REAL-OPTION win rate (net of cost): {win_rate:.2%}")
        print(f"REAL-OPTION NET P&L (qty={qty}, lot={lot_size}): Rs{total_pnl:,.0f}")
        print(f"REAL-OPTION Sharpe (net, annualized): {sharpe:.3f}")
        print(f"Exit reasons: {reasons}")
        print(f"\nUNDERLYING-PROXY (tb_r_multiple_net, same {len(proxy_rs)} signals) "
              f"win rate: {proxy_win_rate:.2%}  mean R: {proxy_mean:.4f}")
        agreement = float(np.mean([(t.pnl > 0) == (t.underlying_proxy_r > 0)
                                    for t in trades if t.underlying_proxy_r is not None]))
        print(f"Directional agreement (real-option win <-> proxy R positive, same trade): {agreement:.2%}")

    return {"strategy": "real_option_translation", "num_trades": n,
            "win_rate": round(win_rate, 4), "total_pnl": round(total_pnl, 2),
            "sharpe": round(sharpe, 4), "exit_reasons": reasons,
            "proxy_win_rate": round(proxy_win_rate, 4) if proxy_win_rate is not None else None,
            "proxy_mean_r": round(proxy_mean, 4) if proxy_mean is not None else None,
            "n_signals": n_signals, "skipped": skipped,
            "trades": [t.__dict__ for t in trades]}


if __name__ == "__main__":
    backtest_real_option_translation()
