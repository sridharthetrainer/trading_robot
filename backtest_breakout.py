"""
backtest_breakout.py

Production-ready Donchian breakout backtester.

Strategy
--------
- Buy when price breaks above prior Donchian channel high
- Sell when price breaks below prior Donchian channel low
- Optional ADX trend-strength filter
- Optional ATR/price volatility filter
- Initial ATR stop loss
- ATR trailing stop with delayed activation
- Optional ATR profit target
- Optional exit on opposite breakout
- Optional close on final bar
- Trade CSV export
- Clean performance metrics
"""
from __future__ import annotations


def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)


import csv
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from slippage import SlippageModel
from indicators import calculate_atr, calculate_adx

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

slippage_model = SlippageModel(percent_slippage=0.05, min_slippage_amount=20.0)

LOT_SIZES = {
    "NIFTY": 50,
    "BANKNIFTY": 50,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 75,
    "SENSEX": 10,
    "BANKEX": 15,
}
DEFAULT_LOT = 10


@dataclass
class Position:
    side: str
    entry: float
    entry_idx: int
    entry_atr: float
    initial_stop: float
    trail_stop: Optional[float] = None
    highest_price: float = field(init=False)
    lowest_price: float = field(init=False)

    def __post_init__(self) -> None:
        self.highest_price = self.entry
        self.lowest_price = self.entry


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


def _compute_max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        dd = peak - value
        max_dd = max(max_dd, dd)
    return float(max_dd)


def _compute_annualised_sharpe(
    trades: List[Dict],
    data: pd.DataFrame,
    initial_capital: float,
    interval_minutes: int = 5,
) -> float:
    """
    Properly annualised Sharpe — matches backtest.py / backtest_trend.py.

    Method 1: group trade PnL by date → daily returns → sqrt(252).
    Method 2: bar-count estimate fallback.
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
                    return float(
                        (daily_ret.mean() / daily_ret.std(ddof=1)) * math.sqrt(252)
                    )
    except Exception:
        pass

    # Method 2: bar-count estimate
    returns = np.array(
        [t.get("pnl_pct_capital", t["pnl"] / initial_capital) for t in trades],
        dtype=float,
    )
    std = np.std(returns, ddof=1)
    if std == 0:
        return 0.0

    bars_per_day  = (6.25 * 60) / max(interval_minutes, 1)
    bars_per_year = bars_per_day * 252
    num_bars      = max(len(data), 1)
    scale         = math.sqrt(bars_per_year / num_bars * len(returns))
    return float((np.mean(returns) / std) * scale)



def _save_trades_csv(trades: List[Dict], symbol: str) -> str:
    filename = f"backtest_breakout_{symbol}.csv"
    fieldnames = [
        "side",
        "entry_idx",
        "exit_idx",
        "entry",
        "exit",
        "qty",
        "gross_pnl",
        "pnl",
        "bars",
        "reason",
    ]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(
                {
                    "side": t["side"],
                    "entry_idx": t["entry_idx"],
                    "exit_idx": t["exit_idx"],
                    "entry": round(t["entry"], 2),
                    "exit": round(t["exit"], 2),
                    "qty": t["qty"],
                    "gross_pnl": round(t["gross_pnl"], 2),
                    "pnl": round(t["pnl"], 2),
                    "bars": t["bars"],
                    "reason": t["reason"],
                }
            )

    return filename


def backtest_breakout(
    symbol: str,
    data: pd.DataFrame,
    # Entry parameters
    channel_period: int = 20,
    # Filters
    adx_threshold: Optional[float] = None,
    min_atr_ratio: float = 0.0,
    max_atr_ratio: float = 1.0,
    # Exits
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 1.5,
    trail_activate: float = 0.0,
    profit_target_atr_mult: Optional[float] = None,
    exit_on_opposite_breakout: bool = True,
    close_at_end: bool = True,
    # Other
    lot_multiplier: int = 1,
    initial_capital: float = 100000.0,
    brokerage_per_order: float = 20.0,
    stt_rate: float = 0.0005,          # 0.05% NSE sell-side options STT
    interval_minutes: int = 5,
    verbose: bool = True,
) -> Dict:
    """
    Donchian breakout backtest.

    BUY signal:
        Close breaks above previous Donchian high

    SELL signal:
        Close breaks below previous Donchian low

    Optional filters:
        - ADX must be above adx_threshold
        - ATR/Close must be between min_atr_ratio and max_atr_ratio

    Exits:
        - Initial ATR stop
        - ATR trailing stop after optional activation threshold
        - Optional ATR profit target
        - Optional opposite breakout exit
        - Optional close at end
    """
    data = _validate_input(data)

    if channel_period <= 1:
        raise ValueError("channel_period must be > 1")
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
    if min_atr_ratio < 0 or max_atr_ratio <= 0 or min_atr_ratio > max_atr_ratio:
        raise ValueError("Invalid ATR ratio filter bounds")

    min_required = max(channel_period, 14) + 10
    if len(data) < min_required:
        raise ValueError(f"Insufficient data: need at least {min_required} candles, got {len(data)}")

    capital = float(initial_capital)
    equity_curve: List[float] = [capital]
    trades: List[Dict] = []
    position: Optional[Position] = None

    buy_signals = 0
    sell_signals = 0
    total_signals = 0
    skipped_due_to_filters = 0

    base_lot = LOT_SIZES.get(symbol.upper(), DEFAULT_LOT)
    quantity = int(base_lot * lot_multiplier)

    open_series = data["Open"]
    high_series = data["High"]
    low_series = data["Low"]
    close_series = data["Close"]

    donchian_high = high_series.rolling(channel_period).max()
    donchian_low = low_series.rolling(channel_period).min()
    atr = calculate_atr(data, period=14)
    adx = calculate_adx(data, period=14) if adx_threshold is not None else None

    valid_start = max(channel_period, 14) + 1
    last_bar_idx = len(data) - 1

    def close_position(exit_idx: int, raw_exit_price: float, reason: str) -> None:
        nonlocal capital, position

        if position is None:
            return

        if position.side == "BUY":
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=False)
            gross_pnl = (actual_exit - position.entry) * quantity
        else:
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=True)
            gross_pnl = (position.entry - actual_exit) * quantity

        # STT: 0.05% of exit premium on the sell side (options)
        stt = actual_exit * quantity * stt_rate
        pnl = gross_pnl - brokerage_per_order - stt
        capital += pnl
        equity_curve.append(capital)

        trade = {
            "side": position.side,
            "entry_idx": position.entry_idx,
            "exit_idx": exit_idx,
            "entry": position.entry,
            "exit": actual_exit,
            "qty": quantity,
            "gross_pnl": gross_pnl,
            "pnl": pnl,
            "pnl_pct_capital": pnl / initial_capital,
            "bars": exit_idx - position.entry_idx,
            "reason": reason,
        }
        trades.append(trade)

        if verbose:
            logger.info(
                "EXIT | side=%s entry=%.2f exit=%.2f pnl=%.2f bars=%d reason=%s",
                position.side,
                position.entry,
                actual_exit,
                pnl,
                trade["bars"],
                reason,
            )

        position = None

    for i in range(valid_start, last_bar_idx):
        price = float(close_series.iloc[i])
        current_atr = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else np.nan
        prev_high = float(donchian_high.iloc[i - 1]) if pd.notna(donchian_high.iloc[i - 1]) else np.nan
        prev_low = float(donchian_low.iloc[i - 1]) if pd.notna(donchian_low.iloc[i - 1]) else np.nan

        if np.isnan(price) or np.isnan(current_atr) or np.isnan(prev_high) or np.isnan(prev_low):
            continue
        if current_atr <= 0 or price <= 0:
            continue

        atr_ratio = current_atr / price
        pass_vol_filter = min_atr_ratio <= atr_ratio <= max_atr_ratio

        if adx is not None:
            current_adx = float(adx.iloc[i]) if pd.notna(adx.iloc[i]) else np.nan
            pass_adx_filter = (not np.isnan(current_adx)) and (current_adx > adx_threshold)
        else:
            current_adx = np.nan
            pass_adx_filter = True

        bullish_breakout = price > prev_high
        bearish_breakout = price < prev_low

        # Manage open position
        if position is not None:
            if position.side == "BUY":
                position.highest_price = max(position.highest_price, price)

                stop_price = position.initial_stop

                if trail_atr_mult > 0:
                    profit_move = position.highest_price - position.entry
                    activate_level = trail_activate * position.entry_atr
                    if profit_move >= activate_level:
                        new_trail = position.highest_price - trail_atr_mult * current_atr
                        if position.trail_stop is None:
                            position.trail_stop = new_trail
                        else:
                            position.trail_stop = max(position.trail_stop, new_trail)
                        stop_price = max(stop_price, position.trail_stop)

                target_price = None
                if profit_target_atr_mult is not None:
                    target_price = position.entry + profit_target_atr_mult * position.entry_atr

                if price <= stop_price:
                    close_position(i, stop_price, "stop_loss")
                elif target_price is not None and price >= target_price:
                    close_position(i, target_price, "profit_target")
                elif exit_on_opposite_breakout and bearish_breakout:
                    close_position(i, price, "opposite_breakout")

            else:
                position.lowest_price = min(position.lowest_price, price)

                stop_price = position.initial_stop

                if trail_atr_mult > 0:
                    profit_move = position.entry - position.lowest_price
                    activate_level = trail_activate * position.entry_atr
                    if profit_move >= activate_level:
                        new_trail = position.lowest_price + trail_atr_mult * current_atr
                        if position.trail_stop is None:
                            position.trail_stop = new_trail
                        else:
                            position.trail_stop = min(position.trail_stop, new_trail)
                        stop_price = min(stop_price, position.trail_stop)

                target_price = None
                if profit_target_atr_mult is not None:
                    target_price = position.entry - profit_target_atr_mult * position.entry_atr

                if price >= stop_price:
                    close_position(i, stop_price, "stop_loss")
                elif target_price is not None and price <= target_price:
                    close_position(i, target_price, "profit_target")
                elif exit_on_opposite_breakout and bullish_breakout:
                    close_position(i, price, "opposite_breakout")

        if position is not None:
            continue

        if bullish_breakout or bearish_breakout:
            total_signals += 1
            if bullish_breakout:
                buy_signals += 1
            if bearish_breakout:
                sell_signals += 1

        if not (bullish_breakout or bearish_breakout):
            continue

        if not (pass_vol_filter and pass_adx_filter):
            skipped_due_to_filters += 1
            continue

        entry_idx = i + 1
        if entry_idx > last_bar_idx:
            continue

        raw_entry = float(open_series.iloc[entry_idx])

        if bullish_breakout:
            actual_entry = slippage_model.apply_slippage(raw_entry, is_buy=True)
            initial_stop = actual_entry - stop_atr_mult * current_atr
            side = "BUY"
        else:
            actual_entry = slippage_model.apply_slippage(raw_entry, is_buy=False)
            initial_stop = actual_entry + stop_atr_mult * current_atr
            side = "SELL"

        capital -= brokerage_per_order
        equity_curve.append(capital)

        position = Position(
            side=side,
            entry=actual_entry,
            entry_idx=entry_idx,
            entry_atr=current_atr,
            initial_stop=initial_stop,
        )

        if verbose:
            logger.info(
                "ENTRY | idx=%d side=%s price=%.2f breakout_high=%.2f breakout_low=%.2f atr=%.2f adx=%s",
                entry_idx,
                side,
                actual_entry,
                prev_high,
                prev_low,
                current_atr,
                f"{current_adx:.2f}" if not np.isnan(current_adx) else "nan",
            )

    if position is not None and close_at_end:
        final_price = float(close_series.iloc[-1])
        close_position(last_bar_idx, final_price, "market_close")

    total_pnl = float(sum(t["pnl"] for t in trades))
    num_trades = len(trades)
    win_rate = float(sum(1 for t in trades if t["pnl"] > 0) / num_trades) if num_trades else 0.0
    max_drawdown = _compute_max_drawdown(equity_curve)
    sharpe = _compute_annualised_sharpe(trades, data, initial_capital, interval_minutes)
    csv_file = _save_trades_csv(trades, symbol)

    return {
        "symbol": symbol,
        "total_pnl": total_pnl,
        "num_trades": num_trades,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "final_capital": capital,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "total_signals": total_signals,
        "skipped_due_to_filters": skipped_due_to_filters,
        "csv_file": csv_file,
        "trades": trades,
        "equity_curve": equity_curve,
        "params": {
            "channel_period": channel_period,
            "adx_threshold": adx_threshold,
            "min_atr_ratio": min_atr_ratio,
            "max_atr_ratio": max_atr_ratio,
            "stop_atr_mult": stop_atr_mult,
            "trail_atr_mult": trail_atr_mult,
            "trail_activate": trail_activate,
            "profit_target_atr_mult": profit_target_atr_mult,
            "exit_on_opposite_breakout": exit_on_opposite_breakout,
            "close_at_end": close_at_end,
            "lot_multiplier": lot_multiplier,
            "initial_capital": initial_capital,
            "brokerage_per_order": brokerage_per_order,
            "stt_rate":          stt_rate,
            "interval_minutes":  interval_minutes,
        },
    }


if __name__ == "__main__":
    from angel import AngelOne
    from data_fetcher import DataFetcher

    dummy_angel = _get_angel_data_fetcher().angel  # use real Angel for data
    fetcher = _get_angel_data_fetcher()

    symbol = "NIFTY"
    data = fetcher.get_market_data(symbol, interval="5m", days=30)

    if data is None or len(data) == 0:
        print("❌ No data fetched.")
    else:
        result = backtest_breakout(
            symbol=symbol,
            data=data,
            channel_period=20,
            adx_threshold=20,
            min_atr_ratio=0.0,
            max_atr_ratio=1.0,
            stop_atr_mult=2.0,
            trail_atr_mult=1.5,
            trail_activate=0.5,
            profit_target_atr_mult=None,
            exit_on_opposite_breakout=True,
            close_at_end=True,
            lot_multiplier=1,
            initial_capital=100000.0,
            brokerage_per_order=45.0,   # all-in: brokerage+STT+exchange charges
            verbose=True,
        )

        print("\n" + "=" * 60)
        print(f"BREAKOUT BACKTEST ({symbol})")
        print("=" * 60)
        print(f"Total P&L         : ₹{result['total_pnl']:.2f}")
        print(f"Trades            : {result['num_trades']}")
        print(f"Win Rate          : {result['win_rate'] * 100:.2f}%")
        print(f"Sharpe            : {result['sharpe']:.2f}")
        print(f"Max Drawdown      : ₹{result['max_drawdown']:.2f}")
        print(f"Final Capital     : ₹{result['final_capital']:.2f}")
        print(f"Buy Signals       : {result['buy_signals']}")
        print(f"Sell Signals      : {result['sell_signals']}")
        print(f"Total Signals     : {result['total_signals']}")
        print(f"Skipped By Filter : {result['skipped_due_to_filters']}")
        print(f"CSV File          : {result['csv_file']}")
