"""
backtest_trend.py

Production-ready trend-following backtester.

Strategy
--------
- EMA crossover entries
- Optional ADX trend-strength filter
- Optional ATR/price volatility filter
- Initial ATR stop loss
- ATR trailing stop with delayed activation
- Optional profit target
- Optional exit on opposite crossover
- Optional close on final bar
- Trade CSV export
- Clean performance metrics

Fixes applied
-------------
1. Module-level SlippageModel was hardcoded and un-overridable
   Original: `slippage_model = SlippageModel(0.05, 20)` at the top of
   the file. Instantiated at import time with fixed 0.05% slippage and
   ₹20 brokerage — callers had no way to change these values.
   This also meant that if SlippageModel's constructor changed, any
   import of backtest_trend would fail at module load.

   Fix: SlippageModel is now instantiated inside backtest_trend() using
   the new `slippage_percent` parameter (default 0.05). Brokerage is
   already a parameter (brokerage_per_order).

2. Sharpe ratio was non-standard and inflated
   Original: (mean_return / std) * sqrt(num_trades)
   sqrt(num_trades) is not a valid annualization factor. A strategy
   with 200 trades appeared 14× better than one with 1 trade even if
   they had identical mean/std returns. This made the strategy_selector
   pick high-frequency strategies over genuinely good ones.

   Fix: _compute_annualised_sharpe() — same implementation as backtest.py:
   - Method 1: DatetimeIndex → daily returns → sqrt(252)
   - Method 2: fallback using bar count and interval_minutes

3. STT not modeled in costs
   Added stt_rate parameter (default 0.0005 = 0.05%).
   Applied on the sell side of every exit. Recorded per-trade.

4. interval_minutes parameter added
   Required for Method 2 Sharpe annualization. Default 5 (5-min bars).
   Pass 15 for 15-min, 60 for hourly.
"""

from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from slippage import SlippageModel
from indicators import calculate_atr, calculate_ema, calculate_adx

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instrument lot sizes
# ---------------------------------------------------------------------------
LOT_SIZES: Dict[str, int] = {
    "NIFTY":      50,
    "BANKNIFTY":  50,
    "FINNIFTY":   40,
    "MIDCPNIFTY": 75,
    "SENSEX":     10,
    "BANKEX":     15,
}
DEFAULT_LOT = 10

# NSE intraday session length in minutes
_NSE_SESSION_MINUTES = 375


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------

@dataclass
class Position:
    side:          str
    entry:         float
    entry_idx:     int
    entry_atr:     float
    initial_stop:  float
    trail_stop:    Optional[float] = None
    highest_price: float = field(init=False)
    lowest_price:  float = field(init=False)

    def __post_init__(self) -> None:
        self.highest_price = self.entry
        self.lowest_price  = self.entry


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

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
        raise ValueError("No valid OHLC data after cleaning.")

    return clean


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

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
    trades: List[Dict],
    data: pd.DataFrame,
    initial_capital: float,
    interval_minutes: int = 5,
) -> float:
    """
    Properly annualised Sharpe ratio.

    Method 1 — DatetimeIndex available:
        Group trade P&L by exit date → daily returns → Sharpe × sqrt(252).

    Method 2 — fallback:
        Estimate trades-per-year from bar count and interval_minutes.
    """
    if len(trades) < 2:
        return 0.0

    # ---- Method 1: daily aggregation --------------------------------
    if isinstance(data.index, pd.DatetimeIndex):
        try:
            daily_pnl: Dict[Any, float] = defaultdict(float)
            for t in trades:
                idx = min(int(t.get("exit_idx", len(data) - 1)), len(data) - 1)
                trade_date = data.index[idx].date()
                daily_pnl[trade_date] += float(t["pnl"])

            if len(daily_pnl) >= 2:
                returns = np.array(
                    [v / initial_capital for v in daily_pnl.values()], dtype=float
                )
                std = np.std(returns, ddof=1)
                if std > 0:
                    return round(float(np.mean(returns) / std * np.sqrt(252)), 4)
        except Exception:
            pass

    # ---- Method 2: bar-count based annualization --------------------
    try:
        returns = np.array([t["pnl_pct_capital"] for t in trades], dtype=float)
        std = np.std(returns, ddof=1)
        if std == 0:
            return 0.0

        bars_per_year    = (_NSE_SESSION_MINUTES / max(1, interval_minutes)) * 252
        bars_in_backtest = max(1, len(data))
        trades_per_year  = len(trades) * bars_per_year / bars_in_backtest

        sharpe = (np.mean(returns) / std) * math.sqrt(trades_per_year)
        return round(float(sharpe), 4)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _save_trades_csv(trades: List[Dict], symbol: str) -> str:
    filename   = f"backtest_trend_{symbol}.csv"
    fieldnames = [
        "side", "entry_idx", "exit_idx",
        "entry", "exit", "qty",
        "gross_pnl", "stt", "pnl",
        "bars", "reason",
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
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
                "stt":       round(t.get("stt", 0.0), 2),
                "pnl":       round(t["pnl"],       2),
                "bars":      t["bars"],
                "reason":    t["reason"],
            })
    return filename


# ---------------------------------------------------------------------------
# Core backtest function
# ---------------------------------------------------------------------------

def backtest_trend(
    symbol: str,
    data: pd.DataFrame,
    # Entry parameters
    fast_ema: int                      = 9,
    slow_ema: int                      = 21,
    # Filters
    adx_threshold: Optional[float]    = None,
    min_atr_ratio: float               = 0.0,
    max_atr_ratio: float               = 1.0,
    # Exits
    stop_atr_mult: float               = 2.0,
    trail_atr_mult: float              = 1.5,
    trail_activate: float              = 0.0,
    profit_target_atr_mult: Optional[float] = None,
    exit_on_crossover: bool            = True,
    close_at_end: bool                 = True,
    # Costs
    lot_multiplier: int                = 1,
    initial_capital: float             = 100_000.0,
    brokerage_per_order: float         = 20.0,
    slippage_percent: float            = 0.05,   # was hardcoded module-level
    stt_rate: float                    = 0.0005,  # 0.05% NSE sell-side STT
    # Metrics
    interval_minutes: int              = 5,       # for Sharpe annualization
    verbose: bool                      = True,
) -> Dict:
    """
    Trend-following backtest using EMA crossover.

    BUY signal:  fast EMA crosses above slow EMA
    SELL signal: fast EMA crosses below slow EMA

    Optional filters: ADX threshold, ATR/Close ratio band

    Exits:
        - Initial ATR stop
        - ATR trailing stop after optional activation threshold
        - Optional ATR profit target
        - Optional opposite crossover exit
        - Optional close at end
    """
    data = _validate_input(data)

    if fast_ema <= 1 or slow_ema <= 1:
        raise ValueError("fast_ema and slow_ema must be > 1")
    if fast_ema >= slow_ema:
        raise ValueError("fast_ema must be less than slow_ema")
    if stop_atr_mult <= 0:
        raise ValueError("stop_atr_mult must be > 0")
    if trail_atr_mult < 0:
        raise ValueError("trail_atr_mult must be >= 0")
    if trail_activate < 0:
        raise ValueError("trail_activate must be >= 0")
    if lot_multiplier <= 0:
        raise ValueError("lot_multiplier must be > 0")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")
    if not (0 <= stt_rate <= 0.01):
        raise ValueError("stt_rate must be between 0 and 0.01")
    if min_atr_ratio < 0 or max_atr_ratio <= 0 or min_atr_ratio > max_atr_ratio:
        raise ValueError("Invalid ATR ratio filter bounds")

    min_required = max(fast_ema, slow_ema, 14) + 10
    if len(data) < min_required:
        raise ValueError(
            f"Insufficient data: need at least {min_required} candles, got {len(data)}"
        )

    # SlippageModel is now local — not module-level
    slippage_model = SlippageModel(slippage_percent, brokerage_per_order)

    capital: float            = float(initial_capital)
    equity_curve: List[float] = [capital]
    trades: List[Dict]        = []
    position: Optional[Position] = None

    buy_signals            = 0
    sell_signals           = 0
    total_signals          = 0
    skipped_due_to_filters = 0

    base_lot = LOT_SIZES.get(symbol.upper(), DEFAULT_LOT)
    quantity = int(base_lot * lot_multiplier)

    close_series = data["Close"]
    open_series  = data["Open"]

    ema_fast = calculate_ema(data, fast_ema)
    ema_slow = calculate_ema(data, slow_ema)
    atr      = calculate_atr(data, period=14)
    adx      = calculate_adx(data, period=14) if adx_threshold is not None else None

    valid_start  = max(fast_ema, slow_ema, 14) + 1
    last_bar_idx = len(data) - 1

    def close_position(exit_idx: int, raw_exit_price: float, reason: str) -> None:
        nonlocal capital, position

        if position is None:
            return

        if position.side == "BUY":
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=False)
            gross_pnl   = (actual_exit - position.entry) * quantity
            stt         = actual_exit * quantity * stt_rate  # sell-side exit
        else:
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=True)
            gross_pnl   = (position.entry - actual_exit) * quantity
            stt         = 0.0   # no STT on buy-side

        pnl      = gross_pnl - brokerage_per_order - stt
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
            "stt":             round(stt, 4),
            "pnl":             pnl,
            "pnl_pct_capital": pnl / initial_capital,
            "bars":            exit_idx - position.entry_idx,
            "reason":          reason,
        }
        trades.append(trade)

        if verbose:
            logger.info(
                "EXIT | side=%s entry=%.2f exit=%.2f gross=%.2f stt=%.2f pnl=%.2f bars=%d reason=%s",
                position.side, position.entry, actual_exit,
                gross_pnl, stt, pnl, trade["bars"], reason,
            )

        position = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    for i in range(valid_start, last_bar_idx):
        price       = float(close_series.iloc[i])
        current_atr = float(atr.iloc[i])      if pd.notna(atr.iloc[i])      else np.nan
        fast_now    = float(ema_fast.iloc[i])  if pd.notna(ema_fast.iloc[i])  else np.nan
        slow_now    = float(ema_slow.iloc[i])  if pd.notna(ema_slow.iloc[i])  else np.nan
        fast_prev   = float(ema_fast.iloc[i-1]) if pd.notna(ema_fast.iloc[i-1]) else np.nan
        slow_prev   = float(ema_slow.iloc[i-1]) if pd.notna(ema_slow.iloc[i-1]) else np.nan

        if any(np.isnan(v) for v in (price, current_atr, fast_now, slow_now, fast_prev, slow_prev)):
            continue
        if current_atr <= 0 or price <= 0:
            continue

        atr_ratio        = current_atr / price
        pass_vol_filter  = min_atr_ratio <= atr_ratio <= max_atr_ratio

        if adx is not None:
            current_adx     = float(adx.iloc[i]) if pd.notna(adx.iloc[i]) else np.nan
            pass_adx_filter = (not np.isnan(current_adx)) and (current_adx > adx_threshold)
        else:
            current_adx     = np.nan
            pass_adx_filter = True

        bullish_cross = fast_now > slow_now and fast_prev <= slow_prev
        bearish_cross = fast_now < slow_now and fast_prev >= slow_prev

        # ---- Manage open position -----------------------------------
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
                elif exit_on_crossover and bearish_cross:
                    close_position(i, price, "opposite_crossover")

            else:  # SELL position
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
                elif exit_on_crossover and bullish_cross:
                    close_position(i, price, "opposite_crossover")

        # Skip entry if position still open
        if position is not None:
            continue

        # ---- Signal detection ---------------------------------------
        if bullish_cross or bearish_cross:
            total_signals += 1
            if bullish_cross:
                buy_signals  += 1
            if bearish_cross:
                sell_signals += 1

        if not (bullish_cross or bearish_cross):
            continue

        if not (pass_adx_filter and pass_vol_filter):
            skipped_due_to_filters += 1
            continue

        entry_idx = i + 1
        if entry_idx > last_bar_idx:
            continue

        raw_entry = float(open_series.iloc[entry_idx])

        if bullish_cross:
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
                "ENTRY | idx=%d side=%s price=%.2f fast=%.2f slow=%.2f atr=%.2f adx=%s",
                entry_idx, side, actual_entry, fast_now, slow_now, current_atr,
                f"{current_adx:.2f}" if not np.isnan(current_adx) else "nan",
            )

    # ---- Force close at end -----------------------------------------
    if position is not None and close_at_end:
        close_position(last_bar_idx, float(close_series.iloc[-1]), "market_close")

    # ---- Results -------------------------------------------------------
    total_pnl  = float(sum(t["pnl"]              for t in trades))
    total_stt  = float(sum(t.get("stt", 0.0)     for t in trades))
    num_trades = len(trades)
    win_rate   = float(sum(1 for t in trades if t["pnl"] > 0) / num_trades) if num_trades else 0.0
    max_dd     = _compute_max_drawdown(equity_curve)
    sharpe     = _compute_annualised_sharpe(trades, data, initial_capital, interval_minutes)
    csv_file   = _save_trades_csv(trades, symbol)

    if verbose:
        print("\n" + "=" * 60)
        print(f"TREND BACKTEST ({symbol})")
        print("=" * 60)
        print(f"Total P&L         : ₹{total_pnl:.2f}")
        print(f"Trades            : {num_trades}")
        print(f"Win Rate          : {win_rate * 100:.2f}%")
        print(f"Sharpe            : {sharpe:.2f}")
        print(f"Max Drawdown      : ₹{max_dd:.2f}")
        print(f"Final Capital     : ₹{capital:.2f}")
        print(f"Total STT Paid    : ₹{total_stt:.2f}")
        print(f"Buy Signals       : {buy_signals}")
        print(f"Sell Signals      : {sell_signals}")
        print(f"Total Signals     : {total_signals}")
        print(f"Skipped By Filter : {skipped_due_to_filters}")
        print(f"CSV File          : {csv_file}")

    return {
        "symbol":                  symbol,
        "total_pnl":               total_pnl,
        "num_trades":              num_trades,
        "win_rate":                win_rate,
        "sharpe":                  sharpe,
        "max_drawdown":            max_dd,
        "final_capital":           capital,
        "total_stt":               round(total_stt, 2),
        "buy_signals":             buy_signals,
        "sell_signals":            sell_signals,
        "total_signals":           total_signals,
        "skipped_due_to_filters":  skipped_due_to_filters,
        "csv_file":                csv_file,
        "trades":                  trades,
        "equity_curve":            equity_curve,
        "params": {
            "fast_ema":                  fast_ema,
            "slow_ema":                  slow_ema,
            "adx_threshold":             adx_threshold,
            "min_atr_ratio":             min_atr_ratio,
            "max_atr_ratio":             max_atr_ratio,
            "stop_atr_mult":             stop_atr_mult,
            "trail_atr_mult":            trail_atr_mult,
            "trail_activate":            trail_activate,
            "profit_target_atr_mult":    profit_target_atr_mult,
            "exit_on_crossover":         exit_on_crossover,
            "close_at_end":              close_at_end,
            "lot_multiplier":            lot_multiplier,
            "initial_capital":           initial_capital,
            "brokerage_per_order":       brokerage_per_order,
            "slippage_percent":          slippage_percent,
            "stt_rate":                  stt_rate,
            "interval_minutes":          interval_minutes,
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from angel import AngelOne
    from data_fetcher import DataFetcher

    dummy_angel = AngelOne("", "", "", "", paper_trade=True)
    fetcher     = DataFetcher(dummy_angel, paper_trade=True)

    symbol = "NIFTY"
    data   = fetcher.get_market_data(symbol, interval="5m", days=30)

    if data is None or len(data) == 0:
        print("No data fetched.")
    else:
        result = backtest_trend(
            symbol                  = symbol,
            data                    = data,
            fast_ema                = 9,
            slow_ema                = 21,
            adx_threshold           = 20,
            min_atr_ratio           = 0.0,
            max_atr_ratio           = 1.0,
            stop_atr_mult           = 2.0,
            trail_atr_mult          = 1.5,
            trail_activate          = 0.5,
            profit_target_atr_mult  = None,
            exit_on_crossover       = True,
            close_at_end            = True,
            lot_multiplier          = 1,
            initial_capital         = 100_000.0,
            brokerage_per_order     = 20.0,
            slippage_percent        = 0.05,
            stt_rate                = 0.0005,
            interval_minutes        = 5,
            verbose                 = True,
        )

        print("\n" + "=" * 60)
        print(f"TREND BACKTEST ({symbol})")
        print("=" * 60)
        print(f"Total P&L         : ₹{result['total_pnl']:.2f}")
        print(f"Trades            : {result['num_trades']}")
        print(f"Win Rate          : {result['win_rate'] * 100:.2f}%")
        print(f"Sharpe            : {result['sharpe']:.2f}")
        print(f"Max Drawdown      : ₹{result['max_drawdown']:.2f}")
        print(f"Final Capital     : ₹{result['final_capital']:.2f}")
        print(f"Total STT Paid    : ₹{result['total_stt']:.2f}")
        print(f"Buy Signals       : {result['buy_signals']}")
        print(f"Sell Signals      : {result['sell_signals']}")
        print(f"Total Signals     : {result['total_signals']}")
        print(f"Skipped By Filter : {result['skipped_due_to_filters']}")
        print(f"CSV File          : {result['csv_file']}")
