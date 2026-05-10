"""
backtest_mr_enhanced.py

Production-ready enhanced mean-reversion backtester.

Features
--------
- RSI + Bollinger Bands entries
- Optional trend filter (SMA)
- Optional ADX filter
- Optional ATR/price volatility filter
- ATR stop loss
- ATR trailing stop with optional delayed activation
- Optional ATR-based profit target
- Clean performance metrics
- CSV trade export
- Compatible with backtest_mr_grid.py, backtest_mr_validate.py

Fixes applied
-------------
1. Sharpe ratio inflated by ~14× (same bug as backtest.py / backtest_trend.py)
   Original: (mean/std) * sqrt(num_trades)
   If a strategy makes 200 trades the multiplier is sqrt(200) = 14.1.
   A modest mean/std = 0.2 would produce Sharpe = 2.8 — strategy selector
   would always prefer high-frequency mean-reversion strategies.

   Fix: proper annualized Sharpe via daily P&L grouping when trades have
   timestamps, or bar-count estimate when they don't.  Both methods match
   the approach used in backtest.py and backtest_trend.py.

2. Module-level SlippageModel uses positional args
   SlippageModel(0.05, 20) where 20 is passed as min_slippage_amount
   (renamed from min_ticks in the fixed slippage.py).
   Still works positionally, but the comment is updated to match the
   new parameter name so future readers don't confuse it with brokerage.
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from slippage import SlippageModel
from indicators import calculate_atr, calculate_rsi, calculate_sma, calculate_adx

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 0.05% slippage, 20-paise minimum (market-impact floor, not brokerage)
slippage_model = SlippageModel(
    percent_slippage    = 0.05,
    min_slippage_amount = 20.0,
)

LOT_SIZES = {
    "NIFTY":      50,
    "BANKNIFTY":  50,
    "FINNIFTY":   40,
    "MIDCPNIFTY": 75,
    "SENSEX":     10,
    "BANKEX":     15,
}
DEFAULT_LOT = 10


@dataclass
class Position:
    side:          str
    entry:         float
    entry_idx:     int
    entry_atr:     float
    initial_stop:  float
    trail_stop:    Optional[float] = None
    highest_price: float           = field(init=False)
    lowest_price:  float           = field(init=False)

    def __post_init__(self) -> None:
        self.highest_price = self.entry
        self.lowest_price  = self.entry


def _validate_input(data: pd.DataFrame) -> pd.DataFrame:
    required_cols = {"Open", "High", "Low", "Close"}
    missing = required_cols - set(data.columns)
    if missing:
        raise ValueError(f"Data missing required columns: {sorted(missing)}")

    clean = data.copy()
    for col in ["Open", "High", "Low", "Close"]:
        clean[col] = pd.to_numeric(clean[col], errors="coerce")

    clean = clean.dropna(subset=["Open", "High", "Low", "Close"]).reset_index(drop=True)
    if clean.empty:
        raise ValueError("No valid OHLC rows available after cleaning.")
    return clean


def _compute_max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak   = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak   = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return float(max_dd)


def _compute_annualised_sharpe(
    trades:           List[Dict],
    data:             pd.DataFrame,
    initial_capital:  float,
    interval_minutes: int = 5,
) -> float:
    """
    Properly annualised Sharpe matching backtest.py / backtest_trend.py.

    Method 1 (preferred): group trade P&L by calendar date and compute
    daily returns, then annualise with sqrt(252).

    Method 2 (fallback): estimate bars-per-year from interval_minutes
    and the actual number of bars in `data`, then scale accordingly.
    """
    if len(trades) < 2:
        return 0.0

    # Method 1: daily grouping
    try:
        df_t = pd.DataFrame(trades)
        if "exit_idx" in df_t.columns and isinstance(data.index, pd.DatetimeIndex):
            df_t["date"] = df_t["exit_idx"].apply(
                lambda idx: data.index[int(idx)].date()
                if isinstance(idx, (int, np.integer)) and 0 <= int(idx) < len(data)
                else None
            )
            df_t = df_t.dropna(subset=["date"])
            if not df_t.empty:
                daily_pnl = df_t.groupby("date")["pnl"].sum()
                daily_ret = daily_pnl / initial_capital
                if daily_ret.std(ddof=1) > 0:
                    return float((daily_ret.mean() / daily_ret.std(ddof=1)) * math.sqrt(252))
    except Exception:
        pass

    # Method 2: bar-count estimate
    returns = np.array([t.get("pnl_pct_capital", t["pnl"] / initial_capital)
                        for t in trades], dtype=float)
    if np.std(returns, ddof=1) == 0:
        return 0.0

    bars_per_day  = (6.25 * 60) / max(interval_minutes, 1)   # NSE session = 6h15m
    bars_per_year = bars_per_day * 252
    num_bars      = max(len(data), 1)
    scale         = math.sqrt(bars_per_year / num_bars * len(returns))
    return float((np.mean(returns) / np.std(returns, ddof=1)) * scale)


def _save_trades_csv(trades: List[Dict], symbol: str) -> str:
    filename   = f"backtest_mr_{symbol}.csv"
    fieldnames = ["side", "entry_idx", "exit_idx", "entry", "exit",
                  "qty", "gross_pnl", "pnl", "bars", "reason"]
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "side":      t["side"],
                "entry_idx": t["entry_idx"],
                "exit_idx":  t["exit_idx"],
                "entry":     round(t["entry"],     2),
                "exit":      round(t["exit"],      2),
                "qty":       t["qty"],
                "gross_pnl": round(t["gross_pnl"], 2),
                "pnl":       round(t["pnl"],       2),
                "bars":      t["bars"],
                "reason":    t["reason"],
            })
    return filename


def backtest_mr(
    symbol:                  str,
    data:                    pd.DataFrame,
    rsi_period:              int            = 14,
    bb_period:               int            = 20,
    bb_std:                  float          = 2.0,
    oversold:                int            = 30,
    overbought:              int            = 70,
    trend_sma_period:        Optional[int]  = 200,
    adx_threshold:           Optional[float] = 25,
    min_atr_ratio:           float          = 0.0,
    max_atr_ratio:           float          = 1.0,
    stop_atr_mult:           float          = 1.5,
    profit_target_atr_mult:  Optional[float] = None,
    trail_atr_mult:          float          = 1.0,
    trail_activate:          float          = 0.0,
    close_at_end:            bool           = True,
    lot_multiplier:          int            = 1,
    initial_capital:         float          = 100_000.0,
    brokerage_per_order:     float          = 20.0,
    interval_minutes:        int            = 5,
    verbose:                 bool           = True,
) -> Dict:
    """
    Enhanced mean-reversion backtest.

    Strategy logic
    --------------
    BUY:  RSI < oversold  AND Close < lower Bollinger Band
    SELL: RSI > overbought AND Close > upper Bollinger Band

    Optional filters
    ----------------
    trend_sma_period: BUY only below SMA, SELL only above SMA
    adx_threshold:    Trade only when ADX < threshold
    ATR ratio:        Trade only when min_atr_ratio <= ATR/Close <= max_atr_ratio

    Exits
    -----
    - Initial ATR stop
    - Optional ATR trailing stop
    - Optional ATR profit target
    - Market close on final bar if close_at_end=True
    """
    data = _validate_input(data)

    if rsi_period <= 1:         raise ValueError("rsi_period must be > 1")
    if bb_period <= 1:          raise ValueError("bb_period must be > 1")
    if bb_std <= 0:             raise ValueError("bb_std must be > 0")
    if oversold >= overbought:  raise ValueError("oversold must be less than overbought")
    if stop_atr_mult <= 0:      raise ValueError("stop_atr_mult must be > 0")
    if trail_atr_mult < 0:      raise ValueError("trail_atr_mult must be >= 0")
    if trail_activate < 0:      raise ValueError("trail_activate must be >= 0")
    if lot_multiplier <= 0:     raise ValueError("lot_multiplier must be > 0")
    if initial_capital <= 0:    raise ValueError("initial_capital must be > 0")
    if min_atr_ratio < 0 or max_atr_ratio <= 0 or min_atr_ratio > max_atr_ratio:
        raise ValueError("Invalid ATR ratio filter bounds")

    min_required = max(rsi_period, bb_period, 14, trend_sma_period or 0) + 10
    if len(data) < min_required:
        raise ValueError(
            f"Insufficient data: need at least {min_required} candles, got {len(data)}"
        )

    capital      = float(initial_capital)
    equity_curve: List[float]  = [capital]
    trades:       List[Dict]   = []
    position:     Optional[Position] = None

    buy_signals = sell_signals = total_signals = skipped_due_to_filters = 0

    base_lot = LOT_SIZES.get(symbol.upper(), DEFAULT_LOT)
    quantity = int(base_lot * lot_multiplier)

    close_series = data["Close"]
    open_series  = data["Open"]

    rsi        = calculate_rsi(data, rsi_period)
    bb_mid     = close_series.rolling(bb_period).mean()
    bb_stddev  = close_series.rolling(bb_period).std()
    upper_bb   = bb_mid + bb_std * bb_stddev
    lower_bb   = bb_mid - bb_std * bb_stddev
    atr        = calculate_atr(data, period=14)
    adx        = calculate_adx(data, period=14)
    trend_sma  = calculate_sma(close_series, trend_sma_period) if trend_sma_period else None

    valid_start  = max(rsi_period, bb_period, 14, trend_sma_period or 0)
    last_bar_idx = len(data) - 1

    def close_position(exit_idx: int, raw_exit_price: float, reason: str) -> None:
        nonlocal capital, position
        if position is None:
            return

        if position.side == "BUY":
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=False)
            gross_pnl   = (actual_exit - position.entry) * quantity
        else:
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=True)
            gross_pnl   = (position.entry - actual_exit) * quantity

        pnl = gross_pnl - brokerage_per_order
        capital += pnl
        equity_curve.append(capital)

        trade = {
            "side":            position.side,
            "entry_idx":       position.entry_idx,
            "exit_idx":        exit_idx,
            "entry":           position.entry,
            "exit":            actual_exit,
            "qty":             quantity,
            "gross_pnl":       gross_pnl,
            "pnl":             pnl,
            "pnl_pct_capital": pnl / initial_capital,
            "bars":            exit_idx - position.entry_idx,
            "reason":          reason,
        }
        trades.append(trade)

        if verbose:
            logger.info(
                "EXIT | side=%s entry=%.2f exit=%.2f pnl=%.2f bars=%d reason=%s",
                position.side, position.entry, actual_exit, pnl,
                trade["bars"], reason,
            )
        position = None

    for i in range(valid_start, last_bar_idx):
        price         = float(close_series.iloc[i])
        current_atr   = float(atr.iloc[i])   if pd.notna(atr.iloc[i])   else np.nan
        current_rsi   = float(rsi.iloc[i])   if pd.notna(rsi.iloc[i])   else np.nan
        current_adx   = float(adx.iloc[i])   if pd.notna(adx.iloc[i])   else np.nan
        current_upper = float(upper_bb.iloc[i]) if pd.notna(upper_bb.iloc[i]) else np.nan
        current_lower = float(lower_bb.iloc[i]) if pd.notna(lower_bb.iloc[i]) else np.nan

        if any(np.isnan(v) for v in [price, current_atr, current_rsi,
                                      current_upper, current_lower]):
            continue
        if current_atr <= 0 or price <= 0:
            continue

        atr_ratio       = current_atr / price
        pass_vol_filter = min_atr_ratio <= atr_ratio <= max_atr_ratio
        pass_adx_filter = (
            adx_threshold is None
            or (not np.isnan(current_adx) and current_adx < adx_threshold)
        )

        # Manage open position
        if position is not None:
            if position.side == "BUY":
                position.highest_price = max(position.highest_price, price)
                stop_price = position.initial_stop

                if trail_atr_mult > 0:
                    profit_move    = position.highest_price - position.entry
                    activate_level = trail_activate * position.entry_atr
                    if profit_move >= activate_level:
                        new_trail = position.highest_price - trail_atr_mult * current_atr
                        position.trail_stop = (
                            new_trail if position.trail_stop is None
                            else max(position.trail_stop, new_trail)
                        )
                        stop_price = max(stop_price, position.trail_stop)

                target_price = (
                    position.entry + profit_target_atr_mult * position.entry_atr
                    if profit_target_atr_mult is not None else None
                )
                if price <= stop_price:
                    close_position(i, stop_price, "stop_loss")
                elif target_price is not None and price >= target_price:
                    close_position(i, target_price, "profit_target")

            else:  # SELL
                position.lowest_price = min(position.lowest_price, price)
                stop_price = position.initial_stop

                if trail_atr_mult > 0:
                    profit_move    = position.entry - position.lowest_price
                    activate_level = trail_activate * position.entry_atr
                    if profit_move >= activate_level:
                        new_trail = position.lowest_price + trail_atr_mult * current_atr
                        position.trail_stop = (
                            new_trail if position.trail_stop is None
                            else min(position.trail_stop, new_trail)
                        )
                        stop_price = min(stop_price, position.trail_stop)

                target_price = (
                    position.entry - profit_target_atr_mult * position.entry_atr
                    if profit_target_atr_mult is not None else None
                )
                if price >= stop_price:
                    close_position(i, stop_price, "stop_loss")
                elif target_price is not None and price <= target_price:
                    close_position(i, target_price, "profit_target")

        if position is not None:
            continue

        buy_allowed = sell_allowed = True
        if trend_sma is not None:
            trend_value = trend_sma.iloc[i]
            if pd.isna(trend_value):
                continue
            buy_allowed  = price < trend_value
            sell_allowed = price > trend_value

        long_signal  = buy_allowed  and current_rsi < oversold  and price < current_lower
        short_signal = sell_allowed and current_rsi > overbought and price > current_upper

        if long_signal or short_signal:
            total_signals += 1
        if long_signal:
            buy_signals += 1
        elif short_signal:
            sell_signals += 1

        if not (long_signal or short_signal):
            continue
        if not (pass_vol_filter and pass_adx_filter):
            skipped_due_to_filters += 1
            continue

        entry_idx = i + 1
        if entry_idx > last_bar_idx:
            continue

        raw_entry = float(open_series.iloc[entry_idx])

        if long_signal:
            actual_entry  = slippage_model.apply_slippage(raw_entry, is_buy=True)
            initial_stop  = actual_entry - stop_atr_mult * current_atr
            side          = "BUY"
        else:
            actual_entry  = slippage_model.apply_slippage(raw_entry, is_buy=False)
            initial_stop  = actual_entry + stop_atr_mult * current_atr
            side          = "SELL"

        capital -= brokerage_per_order
        equity_curve.append(capital)

        position = Position(
            side         = side,
            entry        = actual_entry,
            entry_idx    = entry_idx,
            entry_atr    = current_atr,
            initial_stop = initial_stop,
        )

        if verbose:
            logger.info(
                "ENTRY | idx=%d side=%s price=%.2f rsi=%.2f atr=%.2f adx=%s",
                entry_idx, side, actual_entry, current_rsi, current_atr,
                f"{current_adx:.2f}" if not np.isnan(current_adx) else "nan",
            )

    if position is not None and close_at_end:
        close_position(last_bar_idx, float(close_series.iloc[-1]), "market_close")

    total_pnl    = float(sum(t["pnl"] for t in trades))
    num_trades   = len(trades)
    win_rate     = float(sum(1 for t in trades if t["pnl"] > 0) / num_trades) if num_trades else 0.0
    max_drawdown = _compute_max_drawdown(equity_curve)
    sharpe       = _compute_annualised_sharpe(trades, data, initial_capital, interval_minutes)
    csv_file     = _save_trades_csv(trades, symbol)

    return {
        "symbol":                  symbol,
        "total_pnl":               total_pnl,
        "num_trades":              num_trades,
        "win_rate":                win_rate,
        "sharpe":                  sharpe,
        "max_drawdown":            max_drawdown,
        "final_capital":           capital,
        "buy_signals":             buy_signals,
        "sell_signals":            sell_signals,
        "total_signals":           total_signals,
        "skipped_due_to_filters":  skipped_due_to_filters,
        "csv_file":                csv_file,
        "trades":                  trades,
        "equity_curve":            equity_curve,
        "params": {
            "rsi_period":             rsi_period,
            "bb_period":              bb_period,
            "bb_std":                 bb_std,
            "oversold":               oversold,
            "overbought":             overbought,
            "trend_sma_period":       trend_sma_period,
            "adx_threshold":          adx_threshold,
            "min_atr_ratio":          min_atr_ratio,
            "max_atr_ratio":          max_atr_ratio,
            "stop_atr_mult":          stop_atr_mult,
            "profit_target_atr_mult": profit_target_atr_mult,
            "trail_atr_mult":         trail_atr_mult,
            "trail_activate":         trail_activate,
            "close_at_end":           close_at_end,
            "lot_multiplier":         lot_multiplier,
            "initial_capital":        initial_capital,
            "brokerage_per_order":    brokerage_per_order,
            "interval_minutes":       interval_minutes,
        },
    }
