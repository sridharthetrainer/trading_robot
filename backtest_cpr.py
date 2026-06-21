"""
backtest_cpr.py — standalone CPR (Central Pivot Range) breakout backtest for the
validation harness. Reuses backtest_trend's cost / Sharpe / lot conventions so
the result is directly comparable with the other swept strategies.

Entry : price crosses ABOVE TC  -> BUY  (CPR breakout, trend-up day)
        price crosses BELOW BC  -> SELL (CPR breakdown, trend-down day)
Filter: optional 'narrow CPR' (width <= narrow_cpr_pct of pivot = trend day).
Exits : ATR initial stop, ATR trailing stop, opposite breakout, EOD close.

CAVEAT (inherited from the harness proxy): P&L is (exit-entry)*lot in INDEX
POINTS — a directional FUTURES proxy and an OPTIMISTIC upper bound versus real
option-buying (no theta/IV decay). Intraday only (flat by end of day).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from slippage import SlippageModel
from indicators import calculate_atr
from backtest_trend import (
    _validate_input, _compute_max_drawdown, _compute_annualised_sharpe,
    LOT_SIZES, DEFAULT_LOT,
)

logger = logging.getLogger(__name__)


def _day_groups(data: pd.DataFrame, interval_minutes: int) -> pd.Index:
    """A per-bar 'trading day' label, robust to how the harness passes data:
    real DatetimeIndex → calendar date; a datetime column → its date; else a
    fixed bars-per-day fallback (NSE 375-min session / interval)."""
    if isinstance(data.index, pd.DatetimeIndex):
        return pd.Index([ts.date() for ts in data.index])
    for col in ("Date", "Datetime", "datetime", "timestamp", "Timestamp", "time"):
        if col in data.columns:
            dt = pd.to_datetime(data[col], errors="coerce")
            if dt.notna().any():
                return pd.Index(dt.dt.date.values)
    bpd = max(1, int(round(375 / max(1, interval_minutes))))   # bars per session
    return pd.Index(np.arange(len(data)) // bpd)


def _daily_cpr(data: pd.DataFrame, groups: pd.Index) -> pd.DataFrame:
    """Per-bar CPR levels derived from the PREVIOUS day's H/L/C (no look-ahead)."""
    d   = groups
    agg = data.groupby(d).agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
    prev = agg.shift(1)                       # previous day's H/L/C
    piv  = (prev.H + prev.L + prev.C) / 3.0
    bc   = (prev.H + prev.L) / 2.0
    tc   = 2.0 * piv - bc
    out = pd.DataFrame(index=data.index)
    out["piv"]  = piv.reindex(d).values
    out["tc"]   = tc.reindex(d).values
    out["bc"]   = bc.reindex(d).values
    out["date"] = d
    return out


def backtest_cpr(
    symbol: str,
    data: pd.DataFrame,
    narrow_cpr_pct: float       = 0.6,    # CPR width %% below which it's a trend day
    require_narrow: bool        = False,  # only trade narrow-CPR (trend) days
    stop_atr_mult: float        = 1.5,
    trail_atr_mult: float       = 1.0,
    atr_period: int             = 14,
    exit_on_opposite: bool      = True,
    invert_signals: bool        = False,
    close_at_end: bool          = True,   # accepted for harness compat; CPR is
                                          # always intraday (flat by EOD anyway)
    lot_multiplier: int         = 1,
    initial_capital: float      = 100_000.0,
    brokerage_per_order: float  = 20.0,
    slippage_percent: float     = 0.05,
    stt_rate: float             = 0.0002,  # futures sell-side, matches backtest_trend
    interval_minutes: int       = 5,
    verbose: bool               = False,
) -> Dict:
    # Clean OHLC but PRESERVE the DatetimeIndex (daily CPR needs the dates).
    missing = {"Open", "High", "Low", "Close"} - set(data.columns)
    if missing:
        raise ValueError(f"Data missing required columns: {sorted(missing)}")
    if stop_atr_mult <= 0:
        raise ValueError("stop_atr_mult must be > 0")
    data = data.copy()
    for col in ("Open", "High", "Low", "Close"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=["Open", "High", "Low", "Close"])
    if data.empty:
        raise ValueError("No valid OHLC data after cleaning.")

    groups  = _day_groups(data, interval_minutes)
    atr     = calculate_atr(data, period=atr_period)
    cpr     = _daily_cpr(data, groups)
    close_s = data["Close"]
    # Reset-index copy for the Sharpe helper → uses the same bar-count (Method 2)
    # path as the other swept strategies, so the ranking stays comparable.
    data_ri = data.reset_index(drop=True)

    slippage_model = SlippageModel(slippage_percent, 0.5)
    quantity = int(LOT_SIZES.get(symbol.upper(), DEFAULT_LOT) * lot_multiplier)

    capital = initial_capital
    equity_curve: List[float] = [capital]
    trades: List[Dict] = []
    position: Optional[Dict[str, Any]] = None

    def close_position(exit_idx: int, raw_exit: float, reason: str) -> None:
        nonlocal capital, position
        if position is None:
            return
        if position["side"] == "BUY":
            ex    = slippage_model.apply_slippage(raw_exit, is_buy=False)
            gross = (ex - position["entry"]) * quantity
            stt   = ex * quantity * stt_rate
        else:
            ex    = slippage_model.apply_slippage(raw_exit, is_buy=True)
            gross = (position["entry"] - ex) * quantity
            stt   = 0.0
        pnl = gross - brokerage_per_order - stt
        capital += pnl
        equity_curve.append(capital)
        trades.append({
            "side": position["side"], "entry_idx": position["entry_idx"],
            "exit_idx": exit_idx, "entry": position["entry"], "exit": ex,
            "pnl": pnl, "pnl_pct_capital": pnl / initial_capital, "reason": reason,
        })
        position = None

    n     = len(data)
    start = max(atr_period + 1, 2)
    for i in range(start, n):
        price = float(close_s.iloc[i]); prev = float(close_s.iloc[i - 1])
        a   = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else np.nan
        tc  = cpr["tc"].iloc[i]; bc = cpr["bc"].iloc[i]; piv = cpr["piv"].iloc[i]
        if any(pd.isna(v) for v in (price, prev, a, tc, bc, piv)) or a <= 0 or piv <= 0:
            continue
        is_last_of_day = (i == n - 1) or (cpr["date"].iloc[i + 1] != cpr["date"].iloc[i])
        closed_this_bar = False

        # ---- manage an open position ----
        if position is not None:
            if position["side"] == "BUY":
                position["peak"] = max(position["peak"], price)
                position["stop"] = max(position["stop"], position["peak"] - trail_atr_mult * a)
                if price <= position["stop"]:
                    close_position(i, position["stop"], "trail_stop"); closed_this_bar = True
                elif exit_on_opposite and prev >= bc and price < bc:
                    close_position(i, price, "opposite"); closed_this_bar = True
            else:
                position["peak"] = min(position["peak"], price)
                position["stop"] = min(position["stop"], position["peak"] + trail_atr_mult * a)
                if price >= position["stop"]:
                    close_position(i, position["stop"], "trail_stop"); closed_this_bar = True
                elif exit_on_opposite and prev <= tc and price > tc:
                    close_position(i, price, "opposite"); closed_this_bar = True

        if position is not None and is_last_of_day:
            close_position(i, price, "eod"); closed_this_bar = True
        if is_last_of_day or closed_this_bar:
            continue   # no fresh entry on the last bar of a day / a just-closed bar

        # ---- entries (only when flat) ----
        if position is None:
            width_pct = (tc - bc) / piv * 100.0
            if require_narrow and width_pct > narrow_cpr_pct:
                continue
            if prev <= tc and price > tc:           # breakout above TC
                side = "SELL" if invert_signals else "BUY"
                stop = price + stop_atr_mult * a if side == "SELL" else price - stop_atr_mult * a
                position = {"side": side, "entry": price, "entry_idx": i,
                            "stop": stop, "peak": price}
            elif prev >= bc and price < bc:         # breakdown below BC
                side = "BUY" if invert_signals else "SELL"
                stop = price - stop_atr_mult * a if side == "BUY" else price + stop_atr_mult * a
                position = {"side": side, "entry": price, "entry_idx": i,
                            "stop": stop, "peak": price}

    if position is not None:
        close_position(n - 1, float(close_s.iloc[-1]), "final")

    num_trades = len(trades)
    win_rate   = float(sum(1 for t in trades if t["pnl"] > 0) / num_trades) if num_trades else 0.0
    total_pnl  = float(sum(t["pnl"] for t in trades))
    sharpe     = _compute_annualised_sharpe(trades, data_ri, initial_capital, interval_minutes)
    max_dd     = _compute_max_drawdown(equity_curve)
    if verbose:
        logger.info("CPR %s: trades=%d win=%.1f%% pnl=%.0f sharpe=%.2f",
                    symbol, num_trades, win_rate * 100, total_pnl, sharpe)
    return {
        "strategy": "cpr", "symbol": symbol,
        "total_pnl": total_pnl, "num_trades": num_trades,
        "win_rate": win_rate, "sharpe": sharpe, "max_drawdown": max_dd,
        "params": {
            "narrow_cpr_pct": narrow_cpr_pct,
            "require_narrow": require_narrow,
            "stop_atr_mult": stop_atr_mult,
            "trail_atr_mult": trail_atr_mult,
            "atr_period": atr_period,
            "exit_on_opposite": exit_on_opposite,
            "invert_signals": invert_signals,
        },
        "trades": trades,
    }
