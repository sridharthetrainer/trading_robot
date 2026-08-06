"""
backtest_fibonacci.py

Backtest for a Fibonacci retracement bounce strategy.

Standard retail Fibonacci technique: find a recent swing high/low, compute
retracement levels between them via indicators.calculate_fibonacci_levels()
(already implemented, previously unused anywhere in this codebase), and
enter when price pulls back to a key level (38.2%/50%/61.8%) and shows a
rejection back in the swing's direction, with an RSI confirmation. Stop
beyond the next level out; target back toward the swing extreme.

This strategy is explicitly a LOW-PRIOR candidate: Fibonacci retracements
have a weaker evidentiary base than the momentum/mean-reversion indicators
already tested in this codebase (retracement clustering near round numbers
isn't unique to the Fibonacci ratios specifically), and every other rule
strategy tested this session failed walk-forward validation. Built to
actually test the hypothesis through the same rigorous gate, not because
it's expected to pass.

Usage:  python backtest_fibonacci.py [--symbol NIFTY] [--days 30]
"""
from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from indicators import calculate_fibonacci_levels, calculate_rsi, calculate_atr

logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(levelname)s | %(message)s")

DEFAULT_SYMBOL      = "NIFTY"
DEFAULT_DAYS        = 30
DEFAULT_CAPITAL     = 100_000.0
DEFAULT_LOT         = 65
DEFAULT_LOTS        = 1
DEFAULT_BROK        = 20.0
DEFAULT_STT         = 0.000125
DEFAULT_SLIP        = 0.05
SWING_LOOKBACK      = 30      # bars used to find the swing high/low
DEFAULT_ENTRY_LEVEL = "0.618"  # which retracement level triggers entry
DEFAULT_STOP_LEVEL  = "0.786"  # stop placed beyond this level
DEFAULT_RSI_MIN_LONG  = 35     # long: RSI must be recovering, not collapsing
DEFAULT_RSI_MAX_SHORT = 65


def _ohlc_cols(data: pd.DataFrame):
    close_col = next((c for c in ("Close", "close", "CLOSE") if c in data.columns), None)
    high_col  = next((c for c in ("High", "high", "HIGH") if c in data.columns), None)
    low_col   = next((c for c in ("Low", "low", "LOW") if c in data.columns), None)
    if not (close_col and high_col and low_col):
        raise ValueError("Missing required OHLC columns")
    return high_col, low_col, close_col


def backtest_fibonacci(
    symbol: str, data: pd.DataFrame,
    entry_level: str = DEFAULT_ENTRY_LEVEL, stop_level: str = DEFAULT_STOP_LEVEL,
    swing_lookback: int = SWING_LOOKBACK,
    rsi_min_long: float = DEFAULT_RSI_MIN_LONG, rsi_max_short: float = DEFAULT_RSI_MAX_SHORT,
    initial_capital: float = DEFAULT_CAPITAL, lot_size: int = DEFAULT_LOT,
    lots: int = DEFAULT_LOTS, brokerage: float = DEFAULT_BROK,
    stt_rate: float = DEFAULT_STT, slippage_pct: float = DEFAULT_SLIP,
    interval_minutes: int = 5, close_at_end: bool = True,
    verbose: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    if data is None or len(data) < swing_lookback + 20:
        return _empty(symbol, "no_data")

    high_col, low_col, close_col = _ohlc_cols(data)
    rsi_s = calculate_rsi(data, 14)
    atr_s = calculate_atr(data, 14)

    capital  = float(initial_capital)
    qty      = lots * lot_size
    equity   = [capital]
    trades: List[Dict] = []
    position: Optional[Dict[str, Any]] = None

    for i in range(swing_lookback, len(data)):
        close = float(data[close_col].iloc[i])
        rsi_v = float(rsi_s.iloc[i]) if pd.notna(rsi_s.iloc[i]) else 50.0
        atr_v = float(atr_s.iloc[i]) if pd.notna(atr_s.iloc[i]) else close * 0.005
        if close <= 0:
            equity.append(capital); continue

        # Manage open position
        if position:
            side = position["side"]
            eod = (hasattr(data.index[i], "time") and
                   data.index[i].time() >= __import__("datetime").time(15, 10))
            exit_now = eod
            if side == "BUY" and (close >= position["target"] or close <= position["stop"]):
                exit_now = True
            if side == "SELL" and (close <= position["target"] or close >= position["stop"]):
                exit_now = True
            if exit_now:
                exit_p = close * (1 - slippage_pct / 100 if side == "BUY" else 1 + slippage_pct / 100)
                gross = (exit_p - position["entry"]) * qty if side == "BUY" else (position["entry"] - exit_p) * qty
                costs = brokerage + exit_p * qty * stt_rate
                pnl = gross - costs
                capital += pnl
                trades.append({**position, "exit": exit_p, "pnl": pnl})
                position = None
            equity.append(capital); continue

        # Find the swing high/low over the lookback window (excludes current bar)
        window = data.iloc[i - swing_lookback:i]
        swing_high = float(window[high_col].max())
        swing_low = float(window[low_col].min())
        if swing_high <= swing_low:
            equity.append(capital); continue

        levels = calculate_fibonacci_levels(swing_high, swing_low)
        entry_px = levels[entry_level]
        stop_px = levels[stop_level]
        band = max(atr_v * 0.15, swing_high * 0.0005)  # tolerance for "at the level"

        # Determine swing direction from where price currently sits relative
        # to the window's midpoint history (which extreme came more recently
        # tells us which retracement direction is live)
        idx_high = window[high_col].idxmax()
        idx_low = window[low_col].idxmin()
        up_swing = idx_low < idx_high  # low came first, then high -> retracing down from a rally

        if up_swing:
            # Long: price pulled back down to entry_level, RSI recovering (not collapsing)
            at_level = abs(close - entry_px) <= band
            if at_level and rsi_v >= rsi_min_long and rsi_v > float(rsi_s.iloc[i - 1] or rsi_v):
                stop = min(stop_px, close - atr_v)  # never worse than 1 ATR
                target = swing_high
                if target > close and stop < close:
                    entry = close * (1 + slippage_pct / 100)
                    position = {"side": "BUY", "entry": entry, "stop": stop, "target": target}
                    capital -= brokerage
        else:
            # Short: price retraced up to entry_level, RSI weakening (not spiking)
            at_level = abs(close - entry_px) <= band
            if at_level and rsi_v <= rsi_max_short and rsi_v < float(rsi_s.iloc[i - 1] or rsi_v):
                stop = max(stop_px, close + atr_v)
                target = swing_low
                if target < close and stop > close:
                    entry = close * (1 - slippage_pct / 100)
                    position = {"side": "SELL", "entry": entry, "stop": stop, "target": target}
                    capital -= brokerage

        equity.append(capital)

    if close_at_end and position and len(data):
        last_close = float(data[close_col].iloc[-1])
        if last_close > 0:
            side = position["side"]
            exit_p = last_close * (1 - slippage_pct / 100 if side == "BUY" else 1 + slippage_pct / 100)
            gross = (exit_p - position["entry"]) * qty if side == "BUY" else (position["entry"] - exit_p) * qty
            costs = brokerage + exit_p * qty * stt_rate
            pnl = gross - costs
            capital += pnl
            trades.append({**position, "exit": exit_p, "pnl": pnl})
            equity.append(capital)

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    wr = wins / n if n else 0.0
    eq = pd.Series(equity)
    dd = float((eq.cummax() - eq).max())
    ret_s = eq.pct_change().dropna()
    sharpe = float(ret_s.mean() / ret_s.std() * (252 * 75) ** 0.5) if len(ret_s) > 1 and ret_s.std() > 0 else 0.0

    if verbose:
        print(f"\n{'='*55}\nFibonacci Retracement Backtest — {symbol}\n{'='*55}")
        print(f"Net Profit  : Rs.{total_pnl:>10,.2f}")
        print(f"Total Trades: {n}")
        print(f"Win Rate    : {wr:.2%}")
        print(f"Sharpe Ratio: {sharpe:.4f}")
        print(f"Max Drawdown: Rs.{dd:>10,.2f}")

    return {"symbol": symbol, "total_pnl": round(total_pnl, 2), "num_trades": n,
            "win_rate": round(wr, 4), "sharpe": round(sharpe, 4),
            "max_drawdown": round(dd, 2), "final_capital": round(capital, 2)}


def _empty(symbol, reason):
    return {"symbol": symbol, "total_pnl": 0, "num_trades": 0, "win_rate": 0,
            "sharpe": 0, "max_drawdown": 0, "final_capital": 0, "reason": reason}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()

    from candle_cache import get_cached_candles
    data = get_cached_candles(args.symbol, "5m", days=args.days)
    if data is None or data.empty:
        print("No data"); raise SystemExit(1)
    data = data.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                 "close": "Close", "volume": "Volume"})
    backtest_fibonacci(args.symbol, data)
