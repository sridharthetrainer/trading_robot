"""
run_extended_validation.py — re-run the EXISTING validation_harness.py's
walk-forward + deflated-Sharpe + parameter-stability + locked-holdout gate
against the newly available 9-year NIFTY/BANKNIFTY 5-minute history
(external_backtest_data.db), instead of the ~7-month window Angel's API
retention previously limited it to.

This deliberately does NOT build a new backtesting system. validation_harness.py
already does exactly what was asked (parameter grid search across strategies,
deflated Sharpe correcting for the multiple-trial search, locked holdout never
touched during search, buy-and-hold benchmark gate) -- it was just starved of
history. Nothing here auto-promotes a result to live; it only re-runs the
existing measurement with more data and reports.

Data note: external_backtest_data.db covers 2015-01-09..2024-01-25 (unverified
third-party source, structurally validated -- see external_data_loader.py).
Live-collected candle_cache.db covers 2025-05-19..present (broker-sourced).
There is a ~16-month gap between the two (2024-01-25..2025-05-19) with no
data from either source; concatenating across it is disclosed here, not
hidden -- one walk-forward window may span that gap, which is a data
limitation to note, not a hidden correctness bug.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import pandas as pd

import validation_harness as vh

logger = logging.getLogger(__name__)

EXTERNAL_DB = "external_backtest_data.db"
LIVE_DB = "candle_cache.db"

_FN_MAP = {
    "trend":          ("backtest_trend", "backtest_trend"),
    "mean_reversion": ("backtest_mr_enhanced", "backtest_mr"),
    "breakout":       ("backtest_breakout", "backtest_breakout"),
    "ma_cross":       ("backtest_ma_cross", "backtest_ma_cross"),
    "scalping":       ("backtest_scalping", "backtest_scalping"),
    "ema_5min":       ("backtest_5min_ema", "backtest_5min_ema"),
    "cpr":            ("backtest_cpr", "backtest_cpr"),
    "orb":            ("backtest_orb", "backtest_orb"),
    "vwap_reversion": ("backtest_vwap_reversion", "backtest_vwap_reversion"),
    "supertrend_mtf": ("backtest_supertrend_mtf", "backtest_supertrend_mtf"),
}


def load_extended_history(symbol: str = "NIFTY", interval: str = "5m",
                           include_live: bool = True) -> pd.DataFrame:
    """Concatenate external (2015-2024) + live-collected (2025-present) 5m
    history into the DatetimeIndex + capitalized-OHLCV shape backtest_*()
    functions require."""
    frames = []

    ext_conn = sqlite3.connect(EXTERNAL_DB)
    ext_df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp",
        ext_conn, params=(symbol.upper(), interval),
    )
    ext_conn.close()
    if not ext_df.empty:
        ext_df["timestamp"] = pd.to_datetime(ext_df["timestamp"])
        frames.append(ext_df.set_index("timestamp"))

    if include_live and Path(LIVE_DB).exists():
        live_conn = sqlite3.connect(LIVE_DB)
        live_df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND interval=? ORDER BY timestamp",
            live_conn, params=(symbol.upper(), interval),
        )
        live_conn.close()
        if not live_df.empty:
            live_df["timestamp"] = pd.to_datetime(live_df["timestamp"], utc=False)
            live_df["timestamp"] = live_df["timestamp"].dt.tz_localize(None)
            frames.append(live_df.set_index("timestamp"))

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
    })
    return combined


def time_one_backtest_call(full_data: pd.DataFrame, train_bars: int = 4500) -> float:
    """Measure real per-call latency of a single backtest_trend() call on a
    representative train-window-sized slice, to estimate total grid-search
    runtime before committing to the full multi-hour run."""
    import backtest_trend
    slice_df = full_data.iloc[:train_bars].reset_index(drop=True)
    start = time.time()
    backtest_trend.backtest_trend(
        symbol="NIFTY", data=slice_df, initial_capital=100_000.0,
        interval_minutes=5, verbose=False, close_at_end=True,
    )
    return time.time() - start


def run_all(symbol: str = "NIFTY", strategies: Optional[list] = None) -> dict:
    full_data = load_extended_history(symbol)
    logger.info("Loaded %d bars for %s: %s .. %s", len(full_data), symbol,
                full_data.index.min() if len(full_data) else None,
                full_data.index.max() if len(full_data) else None)

    strategies = strategies or list(_FN_MAP)
    results = {}
    for strategy in strategies:
        mod_name, fn_name = _FN_MAP[strategy]
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            backtest_fn = getattr(mod, fn_name)
        except Exception as exc:
            logger.error("Could not import %s.%s: %s", mod_name, fn_name, exc)
            continue

        param_grid = vh._build_default_param_grid(strategy)
        if not param_grid:
            logger.warning("No param grid for %s, skipping", strategy)
            continue

        t0 = time.time()
        result = vh.run_validation(
            strategy_name=strategy, backtest_fn=backtest_fn, full_data=full_data,
            param_grid=param_grid, symbol=symbol, interval_minutes=5,
        )
        vh.save_result(result)
        elapsed = time.time() - t0
        logger.info("%s done in %.1fs: verdict=%s dev_sharpe=%.3f dsr=%.3f holdout_pnl=%s",
                     strategy, elapsed, result.verdict, result.dev_avg_sharpe,
                     result.deflated_sharpe, result.holdout_pnl)
        results[strategy] = result.to_dict()

    Path("extended_validation_report.json").write_text(json.dumps(results, indent=2, default=str))
    return results


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--strategies", default=None, help="comma-separated subset")
    parser.add_argument("--time-only", action="store_true",
                        help="just measure per-call latency and estimate total runtime")
    args = parser.parse_args()

    if args.time_only:
        df = load_extended_history(args.symbol)
        print(f"Loaded {len(df)} bars: {df.index.min()} .. {df.index.max()}")
        per_call = time_one_backtest_call(df)
        print(f"One backtest_trend() call on 4500 bars: {per_call:.4f}s")
    else:
        strategies = args.strategies.split(",") if args.strategies else None
        run_all(args.symbol, strategies)
