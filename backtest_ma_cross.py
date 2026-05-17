"""
backtest_ma_cross.py

Production-ready moving-average / supertrend backtester.

Supported modes
---------------
- mode="ma_cross"    : fast/slow SMA crossover
- mode="supertrend"  : supertrend direction flip

Features
--------
- Optional ADX trend-strength filter
- Optional ATR/price volatility filter
- ATR initial stop loss
- ATR trailing stop with delayed activation
- Optional ATR profit target
- Optional exit on opposite signal
- Fixed-lot or risk-based position sizing
- Sharpe, max drawdown, CSV trade export
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
from indicators import (
    calculate_adx,
    calculate_atr,
    calculate_sma,
    calculate_supertrend,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

slippage_model = SlippageModel(percent_slippage=0.05, min_slippage_amount=20.0)

LOT_SIZES: Dict[str, int] = {
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
    quantity: int
    initial_stop: float
    trail_stop: Optional[float] = None
    highest_price: float = field(init=False)
    lowest_price: float = field(init=False)

    def __post_init__(self) -> None:
        self.highest_price = self.entry
        self.lowest_price = self.entry


def _validate_input(data: pd.DataFrame) -> pd.DataFrame:
    required = {"Open", "High", "Low", "Close"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Data missing required columns: {sorted(missing)}")

    clean = data.copy()
    for col in ["Open", "High", "Low", "Close"]:
        clean[col] = pd.to_numeric(clean[col], errors="coerce")

    clean = clean.dropna(subset=["Open", "High", "Low", "Close"])
    if clean.empty:
        raise ValueError("No valid OHLC rows after cleaning.")

    return clean.reset_index(drop=False) if not isinstance(clean.index, pd.RangeIndex) else clean.reset_index(drop=True)


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



def _resolve_quantity(
    symbol: str,
    capital: float,
    current_atr: float,
    stop_atr_mult: float,
    risk_per_trade: float,
    use_fixed_lot: bool,
    lot_multiplier: int,
) -> int:
    base_lot = LOT_SIZES.get(symbol.upper(), DEFAULT_LOT)
    fixed_quantity = max(1, int(base_lot * lot_multiplier))

    if use_fixed_lot:
        return fixed_quantity

    stop_distance = stop_atr_mult * current_atr
    if stop_distance <= 0:
        return fixed_quantity

    risk_amount = capital * risk_per_trade
    qty = int(risk_amount / stop_distance)

    # Normalize to lot multiple where possible
    lots = max(1, qty // base_lot)
    return max(base_lot, lots * base_lot)


def _save_trades_csv(trades: List[Dict], symbol: str) -> str:
    filename = f"backtest_ma_{symbol}.csv"
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


def backtest_ma_cross(
    symbol: str,
    data: pd.DataFrame,
    # Strategy mode
    mode: str = "ma_cross",  # "ma_cross" | "supertrend"
    # MA parameters
    fast_ma: int = 20,
    slow_ma: int = 100,
    # Supertrend parameters
    st_period: int = 10,
    st_multiplier: float = 3.0,
    # Filters
    use_adx_filter: bool = False,
    adx_period: int = 14,
    adx_threshold: float = 25.0,
    min_atr_ratio: float = 0.0,
    max_atr_ratio: float = 1.0,
    # Exits
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 1.5,
    trail_activate: float = 0.0,
    profit_target_atr_mult: Optional[float] = None,
    exit_on_opposite_signal: bool = True,
    close_at_end: bool = True,
    # Risk
    risk_per_trade: float = 0.01,
    use_fixed_lot: bool = False,
    lot_multiplier: int = 1,
    # Misc
    initial_capital: float = 100000.0,
    brokerage_per_order: float = 20.0,
    stt_rate: float = 0.0005,          # 0.05% NSE sell-side options STT
    interval_minutes: int = 5,
    verbose: bool = True,
) -> Dict:
    """
    Unified MA cross / Supertrend backtest.

    Entry signals
    -------------
    mode="ma_cross":
        BUY  when fast SMA crosses above slow SMA
        SELL when fast SMA crosses below slow SMA

    mode="supertrend":
        BUY  when supertrend flips from -1 to +1
        SELL when supertrend flips from +1 to -1

    Filters
    -------
    - Optional ADX filter
    - ATR/Close ratio filter

    Exits
    -----
    - Initial ATR stop
    - ATR trailing stop
    - Optional ATR profit target
    - Optional opposite signal exit
    - Optional close on final bar
    """
    data = _validate_input(data)

    if mode not in {"ma_cross", "supertrend"}:
        raise ValueError("mode must be 'ma_cross' or 'supertrend'")
    if mode == "ma_cross" and fast_ma >= slow_ma:
        raise ValueError("fast_ma must be less than slow_ma")
    if fast_ma <= 1 or slow_ma <= 1:
        raise ValueError("fast_ma and slow_ma must be > 1")
    if st_period <= 1:
        raise ValueError("st_period must be > 1")
    if st_multiplier <= 0:
        raise ValueError("st_multiplier must be > 0")
    if stop_atr_mult <= 0:
        raise ValueError("stop_atr_mult must be > 0")
    if trail_atr_mult < 0:
        raise ValueError("trail_atr_mult must be >= 0")
    if trail_activate < 0:
        raise ValueError("trail_activate must be >= 0")
    if risk_per_trade <= 0:
        raise ValueError("risk_per_trade must be > 0")
    if lot_multiplier <= 0:
        raise ValueError("lot_multiplier must be > 0")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")
    if min_atr_ratio < 0 or max_atr_ratio <= 0 or min_atr_ratio > max_atr_ratio:
        raise ValueError("Invalid ATR ratio bounds")

    signal_period = slow_ma if mode == "ma_cross" else st_period
    min_required = max(signal_period, 14, adx_period if use_adx_filter else 0) + 10
    if len(data) < min_required:
        raise ValueError(f"Need at least {min_required} bars, got {len(data)}")

    capital = float(initial_capital)
    equity_curve: List[float] = [capital]
    trades: List[Dict] = []
    position: Optional[Position] = None

    buy_signals = 0
    sell_signals = 0
    total_signals = 0
    skipped_due_to_filters = 0

    open_series = data["Open"]
    close_series = data["Close"]

    atr = calculate_atr(data, period=14)
    adx_series = calculate_adx(data, period=adx_period) if use_adx_filter else None

    ma_fast = None
    ma_slow = None
    st_dir = None

    if mode == "ma_cross":
        ma_fast = calculate_sma(close_series, fast_ma)
        ma_slow = calculate_sma(close_series, slow_ma)
    else:
        _, st_dir = calculate_supertrend(data, period=st_period, multiplier=st_multiplier)

    valid_start = min_required
    last_bar_idx = len(data) - 1

    def close_position(exit_idx: int, raw_exit_price: float, reason: str) -> None:
        nonlocal capital, position

        if position is None:
            return

        if position.side == "BUY":
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=False)
            gross_pnl = (actual_exit - position.entry) * position.quantity
        else:
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=True)
            gross_pnl = (position.entry - actual_exit) * position.quantity

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
            "qty": position.quantity,
            "gross_pnl": gross_pnl,
            "pnl": pnl,
            "pnl_pct_capital": pnl / initial_capital,
            "bars": exit_idx - position.entry_idx,
            "reason": reason,
        }
        trades.append(trade)

        if verbose:
            logger.info(
                "EXIT | side=%s entry=%.2f exit=%.2f qty=%d pnl=%.2f bars=%d reason=%s",
                position.side,
                position.entry,
                actual_exit,
                position.quantity,
                pnl,
                trade["bars"],
                reason,
            )

        position = None

    for i in range(valid_start, last_bar_idx):
        price = float(close_series.iloc[i])
        current_atr = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else np.nan

        if np.isnan(current_atr) or current_atr <= 0 or price <= 0:
            continue

        atr_ratio = current_atr / price
        pass_vol_filter = min_atr_ratio <= atr_ratio <= max_atr_ratio

        if use_adx_filter:
            current_adx = float(adx_series.iloc[i]) if pd.notna(adx_series.iloc[i]) else np.nan
            pass_adx_filter = (not np.isnan(current_adx)) and (current_adx >= adx_threshold)
        else:
            current_adx = np.nan
            pass_adx_filter = True

        bullish_signal = False
        bearish_signal = False

        if mode == "ma_cross":
            fast_now = float(ma_fast.iloc[i]) if pd.notna(ma_fast.iloc[i]) else np.nan
            slow_now = float(ma_slow.iloc[i]) if pd.notna(ma_slow.iloc[i]) else np.nan
            fast_prev = float(ma_fast.iloc[i - 1]) if pd.notna(ma_fast.iloc[i - 1]) else np.nan
            slow_prev = float(ma_slow.iloc[i - 1]) if pd.notna(ma_slow.iloc[i - 1]) else np.nan

            if not any(np.isnan(x) for x in [fast_now, slow_now, fast_prev, slow_prev]):
                bullish_signal = fast_now > slow_now and fast_prev <= slow_prev
                bearish_signal = fast_now < slow_now and fast_prev >= slow_prev
        else:
            dir_now = float(st_dir.iloc[i]) if pd.notna(st_dir.iloc[i]) else np.nan
            dir_prev = float(st_dir.iloc[i - 1]) if pd.notna(st_dir.iloc[i - 1]) else np.nan

            if not any(np.isnan(x) for x in [dir_now, dir_prev]):
                bullish_signal = dir_now == 1 and dir_prev == -1
                bearish_signal = dir_now == -1 and dir_prev == 1

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
        side = "BUY" if bullish_signal else "SELL"
        actual_entry = slippage_model.apply_slippage(raw_entry, is_buy=(side == "BUY"))
        quantity = _resolve_quantity(
            symbol=symbol,
            capital=capital,
            current_atr=current_atr,
            stop_atr_mult=stop_atr_mult,
            risk_per_trade=risk_per_trade,
            use_fixed_lot=use_fixed_lot,
            lot_multiplier=lot_multiplier,
        )

        if side == "BUY":
            initial_stop = actual_entry - stop_atr_mult * current_atr
        else:
            initial_stop = actual_entry + stop_atr_mult * current_atr

        capital -= brokerage_per_order
        equity_curve.append(capital)

        position = Position(
            side=side,
            entry=actual_entry,
            entry_idx=entry_idx,
            entry_atr=current_atr,
            quantity=quantity,
            initial_stop=initial_stop,
        )

        if verbose:
            logger.info(
                "ENTRY | mode=%s idx=%d side=%s price=%.2f qty=%d atr=%.2f adx=%s",
                mode,
                entry_idx,
                side,
                actual_entry,
                quantity,
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
        "mode": mode,
        "total_pnl": total_pnl,
        "num_trades": num_trades,
        "win_rate": win_rate,
        "final_capital": capital,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "total_signals": total_signals,
        "skipped_due_to_filters": skipped_due_to_filters,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "csv_file": csv_file,
        "trades": trades,
        "equity_curve": equity_curve,
        "params": {
            "mode": mode,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "st_period": st_period,
            "st_multiplier": st_multiplier,
            "use_adx_filter": use_adx_filter,
            "adx_period": adx_period,
            "adx_threshold": adx_threshold,
            "min_atr_ratio": min_atr_ratio,
            "max_atr_ratio": max_atr_ratio,
            "stop_atr_mult": stop_atr_mult,
            "trail_atr_mult": trail_atr_mult,
            "trail_activate": trail_activate,
            "profit_target_atr_mult": profit_target_atr_mult,
            "exit_on_opposite_signal": exit_on_opposite_signal,
            "close_at_end": close_at_end,
            "risk_per_trade": risk_per_trade,
            "use_fixed_lot": use_fixed_lot,
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
    data = fetcher.get_market_data(symbol, interval="1d", days=1200)

    if data is None or len(data) == 0:
        print("❌ No data fetched.")
    else:
        test_configs = [
            {"mode": "ma_cross", "fast_ma": 20, "slow_ma": 100},
            {"mode": "ma_cross", "fast_ma": 20, "slow_ma": 100, "use_adx_filter": True, "adx_threshold": 25.0},
            {"mode": "supertrend", "st_period": 10, "st_multiplier": 3.0},
            {"mode": "supertrend", "st_period": 10, "st_multiplier": 3.0, "use_adx_filter": True, "adx_threshold": 25.0},
        ]

        print(f"\n{'Label':<28} {'P&L':>12} {'Trades':>8} {'WR%':>8} {'Sharpe':>10} {'MaxDD':>12}")
        print("-" * 86)

        for cfg in test_configs:
            label = (
                "MA Cross"
                if cfg["mode"] == "ma_cross" and not cfg.get("use_adx_filter")
                else "MA Cross + ADX"
                if cfg["mode"] == "ma_cross"
                else "Supertrend"
                if not cfg.get("use_adx_filter")
                else "Supertrend + ADX"
            )

            result = backtest_ma_cross(
                symbol=symbol,
                data=data,
                verbose=False,
                **cfg,
            )

            print(
                f"{label:<28} "
                f"₹{result['total_pnl']:>11.2f} "
                f"{result['num_trades']:>8} "
                f"{result['win_rate'] * 100:>7.2f}% "
                f"{result['sharpe']:>10.2f} "
                f"₹{result['max_drawdown']:>11.2f}"
            )
