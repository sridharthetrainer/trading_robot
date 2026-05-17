"""
backtest_scalping.py

Production-ready intraday scalping backtester.

Strategy
--------
- Fast EMA trend alignment
- VWAP bias filter
- RSI micro-momentum filter
- Optional ADX filter
- Optional ATR/price volatility filter
- Tight ATR stop
- Fast ATR trailing stop with delayed activation
- Optional profit target
- Optional time-based exit
- Optional end-of-day close
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
from indicators import calculate_ema, calculate_rsi, calculate_atr, calculate_adx

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

    if "Volume" not in clean.columns:
        clean["Volume"] = 1.0
    else:
        clean["Volume"] = pd.to_numeric(clean["Volume"], errors="coerce").fillna(1.0)

    clean = clean.dropna(subset=["Open", "High", "Low", "Close"]).reset_index(drop=True)
    if clean.empty:
        raise ValueError("No valid OHLC data after cleaning.")

    return clean


def _compute_vwap(data: pd.DataFrame) -> pd.Series:
    typical_price = (data["High"] + data["Low"] + data["Close"]) / 3.0
    vol = data["Volume"].replace(0, np.nan).fillna(1.0)
    cum_pv = (typical_price * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_pv / cum_vol


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
    filename = f"backtest_scalping_{symbol}.csv"
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


def backtest_scalping(
    symbol: str,
    data: pd.DataFrame,
    fast_ema: int = 9,
    slow_ema: int = 20,
    rsi_period: int = 7,
    rsi_long_threshold: float = 55.0,
    rsi_short_threshold: float = 45.0,
    use_vwap_filter: bool = True,
    adx_threshold: Optional[float] = None,
    min_atr_ratio: float = 0.0,
    max_atr_ratio: float = 1.0,
    stop_atr_mult: float = 1.0,
    trail_atr_mult: float = 0.8,
    trail_activate: float = 0.0,
    profit_target_atr_mult: Optional[float] = 1.2,
    exit_on_opposite_signal: bool = True,
    max_hold_bars: Optional[int] = 12,
    close_at_end: bool = True,
    lot_multiplier: int = 1,
    initial_capital: float = 100000.0,
    brokerage_per_order: float = 20.0,
    stt_rate: float = 0.0005,          # 0.05% NSE sell-side options STT
    interval_minutes: int = 5,
    verbose: bool = True,
) -> Dict:
    data = _validate_input(data)

    if fast_ema <= 1 or slow_ema <= 1:
        raise ValueError("fast_ema and slow_ema must be > 1")
    if fast_ema >= slow_ema:
        raise ValueError("fast_ema must be less than slow_ema")
    if rsi_period <= 1:
        raise ValueError("rsi_period must be > 1")
    if rsi_long_threshold <= rsi_short_threshold:
        raise ValueError("rsi_long_threshold must be greater than rsi_short_threshold")
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
        raise ValueError("Invalid ATR ratio bounds")
    if max_hold_bars is not None and max_hold_bars <= 0:
        raise ValueError("max_hold_bars must be positive or None")

    min_required = max(fast_ema, slow_ema, rsi_period, 14) + 10
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

    qty = int(LOT_SIZES.get(symbol.upper(), DEFAULT_LOT) * lot_multiplier)

    open_series = data["Open"]
    close_series = data["Close"]

    ema_fast = calculate_ema(data, fast_ema)
    ema_slow = calculate_ema(data, slow_ema)
    rsi = calculate_rsi(data, rsi_period)
    atr = calculate_atr(data, period=14)
    vwap = _compute_vwap(data)
    adx = calculate_adx(data, period=14) if adx_threshold is not None else None

    valid_start = max(fast_ema, slow_ema, rsi_period, 14) + 1
    last_bar_idx = len(data) - 1

    def close_position(exit_idx: int, raw_exit_price: float, reason: str) -> None:
        nonlocal capital, position

        if position is None:
            return

        if position.side == "BUY":
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=False)
            gross_pnl = (actual_exit - position.entry) * qty
        else:
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=True)
            gross_pnl = (position.entry - actual_exit) * qty

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
            "qty": qty,
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
        fast_now = float(ema_fast.iloc[i]) if pd.notna(ema_fast.iloc[i]) else np.nan
        slow_now = float(ema_slow.iloc[i]) if pd.notna(ema_slow.iloc[i]) else np.nan
        current_rsi = float(rsi.iloc[i]) if pd.notna(rsi.iloc[i]) else np.nan
        current_vwap = float(vwap.iloc[i]) if pd.notna(vwap.iloc[i]) else np.nan

        if any(np.isnan(x) for x in [price, current_atr, fast_now, slow_now, current_rsi]):
            continue
        if current_atr <= 0 or price <= 0:
            continue

        atr_ratio = current_atr / price
        pass_vol_filter = min_atr_ratio <= atr_ratio <= max_atr_ratio

        if adx is not None:
            current_adx = float(adx.iloc[i]) if pd.notna(adx.iloc[i]) else np.nan
            pass_adx_filter = (not np.isnan(current_adx)) and (current_adx >= adx_threshold)
        else:
            current_adx = np.nan
            pass_adx_filter = True

        bullish_signal = fast_now > slow_now and current_rsi >= rsi_long_threshold
        bearish_signal = fast_now < slow_now and current_rsi <= rsi_short_threshold

        if use_vwap_filter and not np.isnan(current_vwap):
            bullish_signal = bullish_signal and price >= current_vwap
            bearish_signal = bearish_signal and price <= current_vwap

        # Manage open position
        if position is not None:
            bars_held = i - position.entry_idx

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
                elif max_hold_bars is not None and bars_held >= max_hold_bars:
                    close_position(i, price, "time_exit")
                elif exit_on_opposite_signal and bearish_signal:
                    close_position(i, price, "opposite_signal")

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
                elif max_hold_bars is not None and bars_held >= max_hold_bars:
                    close_position(i, price, "time_exit")
                elif exit_on_opposite_signal and bullish_signal:
                    close_position(i, price, "opposite_signal")

        if position is not None:
            continue

        if bullish_signal or bearish_signal:
            total_signals += 1
            if bullish_signal:
                buy_signals += 1
            if bearish_signal:
                sell_signals += 1

        if not (bullish_signal or bearish_signal):
            continue

        if not (pass_adx_filter and pass_vol_filter):
            skipped_due_to_filters += 1
            continue

        entry_idx = i + 1
        if entry_idx > last_bar_idx:
            continue

        raw_entry = float(open_series.iloc[entry_idx])

        if bullish_signal:
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
                "ENTRY | idx=%d side=%s price=%.2f fast=%.2f slow=%.2f rsi=%.2f vwap=%.2f atr=%.2f adx=%s",
                entry_idx,
                side,
                actual_entry,
                fast_now,
                slow_now,
                current_rsi,
                current_vwap,
                current_atr,
                f"{current_adx:.2f}" if not np.isnan(current_adx) else "nan",
            )

    if position is not None and close_at_end:
        final_price = float(close_series.iloc[-1])
        close_position(last_bar_idx, final_price, "market_close")

    total_pnl = float(sum(t["pnl"] for t in trades))
    num_trades = len(trades)
    win_rate = float(sum(1 for t in trades if t["pnl"] > 0) / num_trades) if num_trades else 0.0
    sharpe = _compute_annualised_sharpe(trades, data, initial_capital, interval_minutes)
    max_dd = _compute_max_drawdown(equity_curve)
    csv_file = _save_trades_csv(trades, symbol)

    return {
        "symbol": symbol,
        "total_pnl": total_pnl,
        "num_trades": num_trades,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "final_capital": capital,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "total_signals": total_signals,
        "skipped_due_to_filters": skipped_due_to_filters,
        "csv_file": csv_file,
        "trades": trades,
        "equity_curve": equity_curve,
        "params": {
            "fast_ema": fast_ema,
            "slow_ema": slow_ema,
            "rsi_period": rsi_period,
            "rsi_long_threshold": rsi_long_threshold,
            "rsi_short_threshold": rsi_short_threshold,
            "use_vwap_filter": use_vwap_filter,
            "adx_threshold": adx_threshold,
            "min_atr_ratio": min_atr_ratio,
            "max_atr_ratio": max_atr_ratio,
            "stop_atr_mult": stop_atr_mult,
            "trail_atr_mult": trail_atr_mult,
            "trail_activate": trail_activate,
            "profit_target_atr_mult": profit_target_atr_mult,
            "exit_on_opposite_signal": exit_on_opposite_signal,
            "max_hold_bars": max_hold_bars,
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
    data = fetcher.get_market_data(symbol, interval="5m", days=20)

    if data is None or len(data) == 0:
        print("❌ No data fetched.")
    else:
        result = backtest_scalping(
            symbol=symbol,
            data=data,
            fast_ema=9,
            slow_ema=20,
            rsi_period=7,
            rsi_long_threshold=55,
            rsi_short_threshold=45,
            use_vwap_filter=True,
            adx_threshold=18,
            min_atr_ratio=0.0,
            max_atr_ratio=1.0,
            stop_atr_mult=1.0,
            trail_atr_mult=0.8,
            trail_activate=0.3,
            profit_target_atr_mult=1.2,
            exit_on_opposite_signal=True,
            max_hold_bars=12,
            close_at_end=True,
            lot_multiplier=1,
            initial_capital=100000.0,
            brokerage_per_order=45.0,   # all-in: brokerage+STT+exchange charges
            verbose=True,
        )

        print("\n" + "=" * 60)
        print(f"SCALPING BACKTEST ({symbol})")
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
