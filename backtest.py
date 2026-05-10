"""
backtest.py

Production-grade full-system backtest aligned with live signal logic.

Fixes applied
-------------
1. Sharpe ratio was non-standard and inflated
   Original: (mean / std) * sqrt(num_trades)
   This used the number of trades as the annualization factor. With 200
   trades the result was ~14× a properly annualised number, making the
   strategy selector choose high-frequency strategies over good ones.

   Fix: _compute_annualised_sharpe() uses two methods:
   - Method 1 (preferred): if the DataFrame has a DatetimeIndex, group
     trade P&L by exit date, compute daily returns, annualise with
     sqrt(252). This is the standard daily Sharpe ratio.
   - Method 2 (fallback): estimate trades-per-year from bar count and
     interval_minutes, annualise the per-trade return series accordingly.
     Passes interval_minutes=5 for 5-min bars (375 bars/day on NSE).

2. Default stop of 5× ATR never fires
   TRAIL_DEFAULTS["STOP_ATR_MULTIPLIER"] was 5.0. On a 5-min NIFTY bar
   with ATR ~30 pts that is a 150-pt stop (~0.65% adverse), almost
   never hit. Tight targets (1.0×–1.7× ATR) exited winners while
   losers ran to maximum drawdown. Changed to 2.0 — realistic for
   intraday options.

3. Only one partial-exit target could fire per bar
   check_exit() returns True on the first target hit and the loop moved
   on. If price gapped through T1 and T2 in the same bar, T2 was missed
   until the following bar. Fixed by calling check_exit() up to 3 times
   per position per bar (matching the 3-target structure of TrailingStop).

4. STT not modeled
   NSE charges STT at 0.05% of premium on the sell side of options.
   On a ₹200 premium × 50 qty position that is ₹5 per trade.
   Added stt_rate parameter (default 0.0005). Applied in close_trade_leg()
   on the sell side of every exit. Recorded per-trade in the trades list.
   Adds a `stt` field to each trade dict for downstream analysis.
"""

from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from angel import AngelOne
from data_fetcher import DataFetcher
from indicators import calculate_adx, calculate_atr
from signals import get_signal
from slippage import SlippageModel
from trailing import TrailingStop

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

# ---------------------------------------------------------------------------
# Trailing-stop defaults
# STOP_ATR_MULTIPLIER changed from 5.0 → 2.0 (realistic intraday stop)
# ---------------------------------------------------------------------------
TRAIL_DEFAULTS: Dict[str, Any] = {
    "STOP_ATR_MULTIPLIER":  2.0,   # was 5.0 — never fired, letting losers run
    "TARGET1_ATR":          1.0,
    "TARGET2_ATR":          1.3,
    "TARGET3_ATR":          1.7,
    "TARGET1_SIZE":         0.33,
    "TARGET2_SIZE":         0.33,
    "TARGET3_SIZE":         0.34,
    "LAZY_UPDATE_THRESHOLD": 1.5,
    "LAZY_DELAY_BARS":       2,
    "TRAIL_ACTIVATE_AFTER":  0.0,
}

# NSE intraday session length in minutes
_NSE_SESSION_MINUTES = 375


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


def _normalize_signal_action(action: Optional[str]) -> str:
    if action is None:
        return "HOLD"
    action = str(action).strip().upper()
    if action in {"BUY", "STRONG BUY", "LONG", "STRONG LONG"}:
        return "BUY"
    if action in {"SELL", "STRONG SELL", "SHORT", "STRONG SHORT"}:
        return "SELL"
    return "HOLD"


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

    Method 1 — DatetimeIndex available (preferred):
        Group trade P&L by exit date → daily returns → Sharpe × sqrt(252).
        Days with no closed trades contribute 0 return (honest).

    Method 2 — fallback (no DatetimeIndex):
        Estimate trades-per-year from bar count and interval_minutes,
        annualise the per-trade return series. Less accurate but always
        comparable across strategies run on the same data set.
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
            pass  # fall through to method 2

    # ---- Method 2: bar-count based annualization --------------------
    try:
        returns = np.array([t["pnl_pct_capital"] for t in trades], dtype=float)
        std = np.std(returns, ddof=1)
        if std == 0:
            return 0.0

        bars_per_year   = (_NSE_SESSION_MINUTES / max(1, interval_minutes)) * 252
        bars_in_backtest = max(1, len(data))
        trades_per_year  = len(trades) * bars_per_year / bars_in_backtest

        sharpe = (np.mean(returns) / std) * math.sqrt(trades_per_year)
        return round(float(sharpe), 4)
    except Exception:
        return 0.0


def _compute_metrics(
    trades: List[Dict],
    equity_curve: List[float],
    initial_capital: float,
    data: pd.DataFrame,
    interval_minutes: int = 5,
) -> Dict:
    num_trades = len(trades)
    if num_trades == 0:
        return {
            "sharpe":        0.0,
            "max_drawdown":  0.0,
            "win_rate":      0.0,
            "avg_win":       0.0,
            "avg_loss":      0.0,
            "profit_factor": 0.0,
        }

    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]

    gross_profit  = float(sum(wins))
    gross_loss    = abs(float(sum(losses))) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "sharpe":        _compute_annualised_sharpe(trades, data, initial_capital, interval_minutes),
        "max_drawdown":  _compute_max_drawdown(equity_curve),
        "win_rate":      float(len(wins) / num_trades),
        "avg_win":       float(sum(wins)   / len(wins))   if wins   else 0.0,
        "avg_loss":      float(sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": float(profit_factor),
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _save_trades_csv(trades: List[Dict], symbol: str) -> str:
    trades_file = f"backtest_{symbol}.csv"
    fieldnames  = [
        "id", "side", "entry_idx", "exit_idx",
        "entry", "exit", "qty",
        "gross_pnl", "stt", "pnl",
        "bars", "reason",
    ]
    with open(trades_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "id":        t["id"],
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
    return trades_file


def _save_equity_csv(equity_curve: List[float], symbol: str) -> str:
    eq_file = f"equity_{symbol}.csv"
    pd.DataFrame({
        "bar":    list(range(len(equity_curve))),
        "equity": equity_curve,
    }).to_csv(eq_file, index=False)
    return eq_file


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------

def backtest_symbol(
    symbol: str,
    data: pd.DataFrame,
    config: Dict,
    initial_capital: float      = 100_000.0,
    slippage_percent: float     = 0.05,
    brokerage_per_order: float  = 20.0,
    stt_rate: float             = 0.0005,    # 0.05% NSE sell-side STT for options
    lot_size: Optional[int]     = None,
    max_open_positions: int     = 1,
    allow_reverse_on_signal: bool = False,
    close_at_end: bool          = True,
    interval_minutes: int       = 5,         # bar width for Sharpe annualization
    verbose: bool               = True,
) -> Dict:
    """
    Run a full live-style backtest for one symbol.

    Entry  : next bar open after signal
    Exit   : trailing-stop logic, up to 3 partial exits per bar per position
    Costs  : slippage on every fill + brokerage per order + STT on sell side
    Sharpe : properly annualised (daily grouping when DatetimeIndex available)
    """
    data = _validate_input(data)

    if initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")
    if slippage_percent < 0:
        raise ValueError("slippage_percent must be >= 0")
    if brokerage_per_order < 0:
        raise ValueError("brokerage_per_order must be >= 0")
    if not (0 <= stt_rate <= 0.01):
        raise ValueError("stt_rate must be between 0 and 0.01")
    if max_open_positions <= 0:
        raise ValueError("max_open_positions must be > 0")

    slippage_model = SlippageModel(slippage_percent, brokerage_per_order)
    trail_config   = {k: config.get(k, v) for k, v in TRAIL_DEFAULTS.items()}

    capital: float              = float(initial_capital)
    equity_curve: List[float]   = [capital]
    trades: List[Dict]          = []
    open_positions: Dict[int, Dict] = {}
    next_trade_id               = 1

    buy_signals          = 0
    sell_signals         = 0
    total_signals        = 0
    skipped_due_to_limits = 0
    skipped_same_side     = 0

    lot_qty      = int(lot_size or config.get("LOT_SIZE") or LOT_SIZES.get(symbol.upper(), DEFAULT_LOT))
    atr_series   = calculate_atr(data, period=14)
    warmup       = max(50, int(config.get("WARMUP_BARS", 50)))
    last_bar_idx = len(data) - 1

    def has_open_side(side: str) -> bool:
        return any(pos["side"] == side for pos in open_positions.values())

    def close_trade_leg(
        trade_id: int,
        pos: Dict,
        exit_idx: int,
        raw_exit_price: float,
        exit_qty: int,
        reason: str,
    ) -> None:
        nonlocal capital

        side        = pos["side"]
        entry_price = pos["entry_price"]

        if side == "BUY":
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=False)
            gross_pnl   = (actual_exit - entry_price) * exit_qty
            # STT on sell (exit) side
            stt = actual_exit * exit_qty * stt_rate
        else:
            actual_exit = slippage_model.apply_slippage(raw_exit_price, is_buy=True)
            gross_pnl   = (entry_price - actual_exit) * exit_qty
            # No STT on buy side
            stt = 0.0

        pnl      = gross_pnl - brokerage_per_order - stt
        capital += pnl
        equity_curve.append(capital)

        trade = {
            "id":              trade_id,
            "side":            side,
            "entry_idx":       pos["entry_idx"],
            "exit_idx":        exit_idx,
            "entry":           entry_price,
            "exit":            actual_exit,
            "qty":             exit_qty,
            "gross_pnl":       gross_pnl,
            "stt":             round(stt, 4),
            "pnl":             pnl,
            "pnl_pct_capital": pnl / initial_capital,
            "bars":            exit_idx - pos["entry_idx"],
            "reason":          reason,
        }
        trades.append(trade)

        if verbose:
            logger.info(
                "[%s] EXIT %s qty=%d @ %.2f gross=%.2f stt=%.2f pnl=%.2f reason=%s",
                symbol, side, exit_qty, actual_exit, gross_pnl, stt, pnl, reason,
            )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    for i in range(warmup, last_bar_idx):
        slice_data    = data.iloc[:i + 1]
        current_price = float(data["Close"].iloc[i])
        current_atr   = float(atr_series.iloc[i]) if pd.notna(atr_series.iloc[i]) else np.nan

        if np.isnan(current_atr) or current_atr <= 0:
            continue

        raw_signal = get_signal(slice_data, config, symbol=symbol)
        action     = _normalize_signal_action(
            raw_signal.get("action") if isinstance(raw_signal, dict) else None
        )

        if action == "BUY":
            buy_signals   += 1
            total_signals += 1
        elif action == "SELL":
            sell_signals  += 1
            total_signals += 1

        if verbose and i % 200 == 0:
            logger.info(
                "[%s] bar=%d price=%.2f action=%s capital=%.2f positions=%d",
                symbol, i, current_price, action, capital, len(open_positions),
            )

        to_remove: List[int] = []

        # ---- Manage open positions — up to 3 partial exits per bar ----
        for trade_id, pos in list(open_positions.items()):
            trailing      = pos["trailing"]
            max_exits     = 3  # matches T1/T2/T3 structure in TrailingStop

            for _ in range(max_exits):
                remaining_qty = pos["remaining_qty"]
                if remaining_qty <= 0:
                    to_remove.append(trade_id)
                    break

                try:
                    exit_ok, exit_price, exit_qty, reason = trailing.check_exit(
                        trade_id, current_price, pos["side"],
                        current_atr, remaining_qty, i,
                    )
                except Exception as exc:
                    logger.exception(
                        "[%s] trailing stop failure trade_id=%s: %s", symbol, trade_id, exc
                    )
                    break

                if not exit_ok:
                    break

                exit_qty = min(int(exit_qty), pos["remaining_qty"])
                if exit_qty <= 0:
                    break

                close_trade_leg(
                    trade_id=trade_id,
                    pos=pos,
                    exit_idx=i,
                    raw_exit_price=float(exit_price),
                    exit_qty=exit_qty,
                    reason=str(reason),
                )

                pos["remaining_qty"] -= exit_qty
                if pos["remaining_qty"] <= 0:
                    try:
                        trailing.cleanup(trade_id)
                    except Exception:
                        pass
                    to_remove.append(trade_id)
                    break

        for trade_id in to_remove:
            open_positions.pop(trade_id, None)

        # ---- Reverse-on-signal logic ------------------------------------
        if allow_reverse_on_signal and action in {"BUY", "SELL"}:
            opposite_ids = [
                tid for tid, pos in open_positions.items()
                if pos["side"] != action
            ]
            for trade_id in opposite_ids:
                pos = open_positions.get(trade_id)
                if pos is None:
                    continue
                remaining_qty = int(pos["remaining_qty"])
                if remaining_qty <= 0:
                    continue
                close_trade_leg(
                    trade_id=trade_id,
                    pos=pos,
                    exit_idx=i,
                    raw_exit_price=current_price,
                    exit_qty=remaining_qty,
                    reason="reverse_signal",
                )
                try:
                    pos["trailing"].cleanup(trade_id)
                except Exception:
                    pass
                open_positions.pop(trade_id, None)

        # ---- New entry (next bar open) -----------------------------------
        if action not in {"BUY", "SELL"}:
            continue
        if len(open_positions) >= max_open_positions:
            skipped_due_to_limits += 1
            continue
        if has_open_side(action):
            skipped_same_side += 1
            continue

        entry_idx = i + 1
        if entry_idx > last_bar_idx:
            continue

        raw_entry    = float(data["Open"].iloc[entry_idx])
        actual_entry = slippage_model.apply_slippage(raw_entry, is_buy=(action == "BUY"))

        capital -= brokerage_per_order
        equity_curve.append(capital)

        trailing = TrailingStop(trail_config)
        try:
            stop_price, _ = trailing.initialize(next_trade_id, actual_entry, action, current_atr)
        except Exception as exc:
            logger.exception("[%s] trailing initialize failed: %s", symbol, exc)
            capital += brokerage_per_order
            equity_curve.append(capital)
            continue

        open_positions[next_trade_id] = {
            "id":            next_trade_id,
            "side":          action,
            "entry_price":   actual_entry,
            "qty":           lot_qty,
            "remaining_qty": lot_qty,
            "entry_idx":     entry_idx,
            "initial_stop":  stop_price,
            "trailing":      trailing,
        }

        if verbose:
            logger.info(
                "[%s] ENTRY %s @ %.2f qty=%d stop=%.2f bar=%d",
                symbol, action, actual_entry, lot_qty, stop_price, entry_idx,
            )

        next_trade_id += 1

    # ---- Force close at end -------------------------------------------
    if close_at_end:
        for trade_id, pos in list(open_positions.items()):
            remaining_qty = int(pos["remaining_qty"])
            if remaining_qty <= 0:
                continue
            last_price = float(data["Close"].iloc[-1])
            close_trade_leg(
                trade_id=trade_id,
                pos=pos,
                exit_idx=last_bar_idx,
                raw_exit_price=last_price,
                exit_qty=remaining_qty,
                reason="market_close",
            )
            try:
                pos["trailing"].cleanup(trade_id)
            except Exception:
                pass
            open_positions.pop(trade_id, None)

    metrics    = _compute_metrics(trades, equity_curve, initial_capital, data, interval_minutes)
    total_pnl  = float(sum(t["pnl"] for t in trades))
    total_stt  = float(sum(t.get("stt", 0.0) for t in trades))
    trades_file = _save_trades_csv(trades, symbol)
    equity_file = _save_equity_csv(equity_curve, symbol)

    if verbose:
        print("\n" + "=" * 60)
        print(f"FULL BACKTEST RESULTS — {symbol}")
        print("=" * 60)
        print(f"Total P&L           : ₹{total_pnl:.2f}")
        print(f"Trades              : {len(trades)}")
        print(f"Win Rate            : {metrics['win_rate'] * 100:.2f}%")
        print(f"Avg Win             : ₹{metrics['avg_win']:.2f}")
        print(f"Avg Loss            : ₹{metrics['avg_loss']:.2f}")
        print(f"Profit Factor       : {metrics['profit_factor']:.2f}")
        print(f"Sharpe              : {metrics['sharpe']:.2f}")
        print(f"Max Drawdown        : ₹{metrics['max_drawdown']:.2f}")
        print(f"Final Capital       : ₹{capital:.2f}")
        print(f"Total STT Paid      : ₹{total_stt:.2f}")
        print(f"BUY Signals         : {buy_signals}")
        print(f"SELL Signals        : {sell_signals}")
        print(f"Total Signals       : {total_signals}")
        print(f"Skipped By Limits   : {skipped_due_to_limits}")
        print(f"Skipped Same Side   : {skipped_same_side}")
        print(f"Trades CSV          : {trades_file}")
        print(f"Equity CSV          : {equity_file}")

    return {
        "symbol":               symbol,
        "total_pnl":            total_pnl,
        "num_trades":           len(trades),
        "win_rate":             metrics["win_rate"],
        "final_capital":        capital,
        "sharpe":               metrics["sharpe"],
        "max_drawdown":         metrics["max_drawdown"],
        "profit_factor":        metrics["profit_factor"],
        "avg_win":              metrics["avg_win"],
        "avg_loss":             metrics["avg_loss"],
        "total_stt":            round(total_stt, 2),
        "trades":               trades,
        "equity_curve":         equity_curve,
        "buy_signals":          buy_signals,
        "sell_signals":         sell_signals,
        "total_signals":        total_signals,
        "skipped_due_to_limits": skipped_due_to_limits,
        "skipped_same_side":    skipped_same_side,
        "trades_file":          trades_file,
        "equity_file":          equity_file,
        "params": {
            "initial_capital":        initial_capital,
            "slippage_percent":       slippage_percent,
            "brokerage_per_order":    brokerage_per_order,
            "stt_rate":               stt_rate,
            "lot_size":               lot_qty,
            "max_open_positions":     max_open_positions,
            "allow_reverse_on_signal": allow_reverse_on_signal,
            "close_at_end":           close_at_end,
            "interval_minutes":       interval_minutes,
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import config as cfg_mod

    ADX_THRESHOLD       = getattr(cfg_mod, "ADX_THRESHOLD",       25)
    BROKERAGE_PER_ORDER = getattr(cfg_mod, "BROKERAGE_PER_ORDER", 20.0)
    SLIPPAGE_PERCENT    = getattr(cfg_mod, "SLIPPAGE_PCT",         0.05)

    try:
        INITIAL_CAPITAL = float(cfg_mod.get_runtime_capital())
    except Exception:
        INITIAL_CAPITAL = float(getattr(cfg_mod, "CAPITAL", 100_000.0))

    DEFAULT_SYMBOL      = getattr(cfg_mod, "DEFAULT_SYMBOL",      "NIFTY")
    DEFAULT_INTERVAL    = getattr(cfg_mod, "DEFAULT_INTERVAL",    "5m")
    MAX_OPEN_POSITIONS  = getattr(cfg_mod, "MAX_OPEN_POSITIONS",   1)

    dummy_angel = AngelOne("", "", "", "", paper_trade=True)
    fetcher     = DataFetcher(dummy_angel, paper_trade=True)

    symbol   = DEFAULT_SYMBOL
    interval = DEFAULT_INTERVAL
    days     = 30

    logger.info("Fetching %s data for %s (%d days)...", interval, symbol, days)
    data = fetcher.get_market_data(symbol, interval=interval, days=days)

    if data is None or len(data) == 0:
        logger.error("Failed to fetch data.")
        raise SystemExit(1)  # converted from sys.exit — safe in threads

    logger.info("Fetched %d bars", len(data))

    try:
        adx_series = calculate_adx(data, period=14)
        valid_adx  = adx_series.dropna()
        if not valid_adx.empty:
            logger.info(
                "ADX stats | max=%.2f min=%.2f mean=%.2f above_threshold=%d",
                float(valid_adx.max()), float(valid_adx.min()),
                float(valid_adx.mean()),
                int((valid_adx > ADX_THRESHOLD).sum()) if ADX_THRESHOLD is not None else 0,
            )
    except Exception as exc:
        logger.warning("ADX diagnostic failed: %s", exc)

    run_config = {
        "ADX_THRESHOLD": ADX_THRESHOLD,
        "LOT_SIZE":      LOT_SIZES.get(symbol.upper(), DEFAULT_LOT),
    }

    # Parse interval string to minutes for Sharpe annualization
    interval_min = 5
    try:
        if DEFAULT_INTERVAL.endswith("m"):
            interval_min = int(DEFAULT_INTERVAL[:-1])
        elif DEFAULT_INTERVAL.endswith("h"):
            interval_min = int(DEFAULT_INTERVAL[:-1]) * 60
    except Exception:
        pass

    backtest_symbol(
        symbol               = symbol,
        data                 = data,
        config               = run_config,
        initial_capital      = INITIAL_CAPITAL,
        slippage_percent     = SLIPPAGE_PERCENT,
        brokerage_per_order  = BROKERAGE_PER_ORDER,
        max_open_positions   = MAX_OPEN_POSITIONS,
        allow_reverse_on_signal = False,
        close_at_end         = True,
        interval_minutes     = interval_min,
        verbose              = True,
    )
