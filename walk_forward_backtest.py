"""
walk_forward_backtest.py

Walk-forward validation for all trading strategies.

Problem with single-period backtesting
---------------------------------------
Grid search on 30 days picks parameters that look best on THAT 30-day window.
Those parameters are overfit — they may perform poorly on the next month.

Walk-forward solves this by repeatedly:
  1. Training on a rolling window (e.g. 60 days)
  2. Testing on the next out-of-sample window (e.g. 30 days)
  3. Moving the window forward and repeating
  4. Averaging results across all OOS windows

Only strategies and parameters that are consistently good across
multiple non-overlapping periods are truly robust.

Architecture
-----------
- Each strategy provides a run() callable accepting (data, **params)
- Data is fetched once for the full period (120+ days) and sliced
- Results per window are aggregated into a WalkForwardResult
- strategy_selector.py calls run_walk_forward_all() in after-hours mode
- Results saved to walk_forward_results.json

Configuration (config.py or .env)
----------------------------------
WF_TRAIN_DAYS    : int  = 60    rolling training window
WF_TEST_DAYS     : int  = 30    OOS test window
WF_MIN_WINDOWS   : int  = 3     minimum windows required for valid result
WF_TOTAL_DAYS    : int  = 210   total history to fetch (train+test × windows)
"""
from __future__ import annotations


# Auto-fix: get DataFetcher with Angel singleton
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


import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Config defaults ──────────────────────────────────────────────────────────
try:
    import config as _cfg
    WF_TRAIN_DAYS  = int(getattr(_cfg, "WF_TRAIN_DAYS",  60))
    WF_TEST_DAYS   = int(getattr(_cfg, "WF_TEST_DAYS",   30))
    WF_MIN_WINDOWS = int(getattr(_cfg, "WF_MIN_WINDOWS",  3))
    WF_TOTAL_DAYS  = int(getattr(_cfg, "WF_TOTAL_DAYS",  210))
except Exception:
    WF_TRAIN_DAYS  = 60
    WF_TEST_DAYS   = 30
    WF_MIN_WINDOWS = 3
    WF_TOTAL_DAYS  = 210

WF_RESULTS_FILE = "walk_forward_results.json"


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class WindowResult:
    window_idx:    int
    train_start:   str
    train_end:     str
    test_start:    str
    test_end:      str
    total_pnl:     float
    num_trades:    int
    win_rate:      float
    sharpe:        float
    max_drawdown:  float
    final_capital: float


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward result for one strategy."""
    strategy:           str
    windows_run:        int
    windows_profitable: int

    # Averages across all OOS windows
    avg_pnl:         float
    avg_sharpe:      float
    avg_win_rate:    float
    avg_drawdown:    float
    avg_trades:      float

    # Consistency metrics
    pct_profitable:  float   # % of windows with positive P&L
    sharpe_std:      float   # lower = more consistent Sharpe
    consistency_score: float  # composite: pct_profitable × avg_sharpe / (1 + sharpe_std)

    # Raw window data
    windows: List[Dict]

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


# ── Slicing helpers ──────────────────────────────────────────────────────────

def _slice_by_index(df: pd.DataFrame, n_bars: int, offset: int = 0) -> pd.DataFrame:
    """Return df[offset : offset + n_bars] safely."""
    start = max(0, offset)
    end   = min(len(df), offset + n_bars)
    return df.iloc[start:end].copy().reset_index(drop=True)


def _bars_per_day(df: pd.DataFrame, interval_minutes: int = 5) -> int:
    """Estimate trading bars per calendar day from actual data."""
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 10:
        dates = df.index.date
        unique_dates = len(set(dates))
        if unique_dates > 0:
            return max(1, len(df) // unique_dates)
    return int(375 / max(interval_minutes, 1))   # 375 = NSE session minutes


def _make_windows(
    df: pd.DataFrame,
    train_days: int,
    test_days:  int,
    bars_per_day: int,
) -> List[Tuple[pd.DataFrame, pd.DataFrame, Dict]]:
    """
    Generate (train_df, test_df, meta) tuples by rolling the window forward.
    Each step advances by test_days worth of bars.
    """
    train_bars = train_days * bars_per_day
    test_bars  = test_days  * bars_per_day
    step_bars  = test_bars

    windows = []
    offset  = 0

    while offset + train_bars + test_bars <= len(df):
        train_df = _slice_by_index(df, train_bars, offset)
        test_df  = _slice_by_index(df, test_bars, offset + train_bars)

        def _date_str(sub_df, pos=-1):
            try:
                if isinstance(sub_df.index, pd.DatetimeIndex):
                    return str(sub_df.index[pos].date())
            except Exception:
                pass
            return "?"

        meta = {
            "train_start": _date_str(train_df,  0),
            "train_end":   _date_str(train_df, -1),
            "test_start":  _date_str(test_df,   0),
            "test_end":    _date_str(test_df,  -1),
        }
        windows.append((train_df, test_df, meta))
        offset += step_bars

    return windows


# ── Walk-forward runner ──────────────────────────────────────────────────────

def run_walk_forward(
    strategy_name: str,
    backtest_fn,
    full_data:    pd.DataFrame,
    best_params:  Dict[str, Any],
    train_days:   int = WF_TRAIN_DAYS,
    test_days:    int = WF_TEST_DAYS,
    min_windows:  int = WF_MIN_WINDOWS,
    interval_minutes: int = 5,
    initial_capital:  float = 100_000.0,
) -> Optional[WalkForwardResult]:
    """
    Run walk-forward validation for a single strategy.

    Parameters
    ----------
    strategy_name  : human-readable name
    backtest_fn    : callable(symbol, data, **params) → dict with keys:
                     total_pnl, num_trades, win_rate, sharpe, max_drawdown, final_capital
    full_data      : complete historical OHLCV DataFrame (120+ days recommended)
    best_params    : parameter dict from grid search (passed to backtest_fn as **kwargs)
    train_days     : bars in training window (used for sizing only — params fixed)
    test_days      : bars in OOS test window
    min_windows    : minimum windows required to return a result
    interval_minutes: bar frequency for Sharpe scaling

    Returns None if insufficient data or too few windows.
    """
    if full_data is None or len(full_data) < 50:
        logger.warning("WF[%s]: insufficient data (%d bars)", strategy_name,
                       len(full_data) if full_data is not None else 0)
        return None

    bars_per_day = _bars_per_day(full_data, interval_minutes)
    windows      = _make_windows(full_data, train_days, test_days, bars_per_day)

    if len(windows) < min_windows:
        logger.warning(
            "WF[%s]: only %d windows available (need %d). "
            "Increase WF_TOTAL_DAYS or reduce train/test window sizes.",
            strategy_name, len(windows), min_windows,
        )
        return None

    window_results: List[WindowResult] = []

    for idx, (train_df, test_df, meta) in enumerate(windows):
        if len(test_df) < 30:
            logger.debug("WF[%s] window %d: test_df too small (%d bars), skipping",
                         strategy_name, idx, len(test_df))
            continue

        try:
            # Run backtest on the OOS test window using FIXED params from grid search
            # (We are not re-optimising on train_df — that is intentional.
            #  Walk-forward validates how grid-search params hold up out-of-sample.)
            result = backtest_fn(
                symbol          = "NIFTY",
                data            = test_df,
                initial_capital = initial_capital,
                interval_minutes = interval_minutes,
                verbose         = False,
                **{k: v for k, v in best_params.items()
                   if k not in ("symbol", "initial_capital", "verbose",
                                "interval_minutes", "close_at_end")},
                close_at_end = True,
            )

            wr = WindowResult(
                window_idx    = idx,
                train_start   = meta["train_start"],
                train_end     = meta["train_end"],
                test_start    = meta["test_start"],
                test_end      = meta["test_end"],
                total_pnl     = float(result.get("total_pnl",    0.0)),
                num_trades    = int(  result.get("num_trades",    0)),
                win_rate      = float(result.get("win_rate",      0.0)),
                sharpe        = float(result.get("sharpe",        0.0)),
                max_drawdown  = float(result.get("max_drawdown",  0.0)),
                final_capital = float(result.get("final_capital", initial_capital)),
            )
            window_results.append(wr)

            logger.info(
                "WF[%s] window %d | test=%s→%s | pnl=%.2f trades=%d "
                "wr=%.1f%% sharpe=%.2f",
                strategy_name, idx,
                meta["test_start"], meta["test_end"],
                wr.total_pnl, wr.num_trades,
                wr.win_rate * 100, wr.sharpe,
            )

        except Exception as exc:
            logger.warning("WF[%s] window %d failed: %s", strategy_name, idx, exc)

    if len(window_results) < min_windows:
        logger.warning("WF[%s]: only %d windows succeeded (need %d)",
                       strategy_name, len(window_results), min_windows)
        return None

    # ── Aggregate ────────────────────────────────────────────────────────────
    pnls     = [w.total_pnl  for w in window_results]
    sharpes  = [w.sharpe     for w in window_results]
    wrs      = [w.win_rate   for w in window_results]
    dds      = [w.max_drawdown for w in window_results]
    trades   = [w.num_trades for w in window_results]

    n_profitable   = sum(1 for p in pnls if p > 0)
    pct_profitable = n_profitable / len(pnls)
    avg_sharpe     = float(np.mean(sharpes))
    sharpe_std     = float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0

    # Consistency score: rewards high average Sharpe that is consistent across windows
    # pct_profitable × avg_sharpe / (1 + sharpe_std)
    consistency = (
        pct_profitable * max(0.0, avg_sharpe) / (1.0 + sharpe_std)
        if avg_sharpe > 0 else 0.0
    )

    result = WalkForwardResult(
        strategy           = strategy_name,
        windows_run        = len(window_results),
        windows_profitable = n_profitable,
        avg_pnl            = round(float(np.mean(pnls)), 2),
        avg_sharpe         = round(avg_sharpe, 4),
        avg_win_rate       = round(float(np.mean(wrs)), 4),
        avg_drawdown       = round(float(np.mean(dds)), 2),
        avg_trades         = round(float(np.mean(trades)), 1),
        pct_profitable     = round(pct_profitable, 4),
        sharpe_std         = round(sharpe_std, 4),
        consistency_score  = round(consistency, 4),
        windows            = [asdict(w) for w in window_results],
    )

    logger.info(
        "WF[%s] COMPLETE | windows=%d profitable=%d(%.0f%%) "
        "avg_pnl=%.2f avg_sharpe=%.2f consistency=%.3f",
        strategy_name,
        result.windows_run, result.windows_profitable,
        result.pct_profitable * 100,
        result.avg_pnl, result.avg_sharpe, result.consistency_score,
    )

    return result


# ── Convenience: run all strategies ─────────────────────────────────────────

def _get_wf_symbols() -> list:
    """Get symbols for walk-forward validation."""
    symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    try:
        import pandas as pd
        from pathlib import Path
        csv = Path("nifty200.csv")
        if csv.exists():
            df = pd.read_csv(str(csv))
            for col in df.columns:
                if "symbol" in col.lower():
                    symbols += [str(s).strip().upper() for s in df[col].dropna().tolist()[:20]]
                    break
    except Exception: pass
    return list(dict.fromkeys(symbols))[:25]  # max 25 for speed


def run_walk_forward_all(
    data_fetcher        = None,
    best_params_dir:    str   = ".",
    results_file:       str   = WF_RESULTS_FILE,
    initial_capital:    float = 100_000.0,
    interval_minutes:   int   = 5,
) -> Dict[str, Any]:
    """
    Run walk-forward validation for all strategies that have a best_params file.

    Expects best_params files:
        best_params_trend.json, best_params_mr.json,
        best_params_breakout.json, best_params_scalping.json,
        best_params_ma.json

    Returns dict mapping strategy name → WalkForwardResult.to_dict()

    Usage (from after-hours learning cycle):
        from walk_forward_backtest import run_walk_forward_all
        results = run_walk_forward_all(data_fetcher=self.data_fetcher)
    """
    # Lazy imports — keep top-level import fast
    strategy_map = {}
    try:
        from backtest_trend     import backtest_trend
        strategy_map["trend"]         = backtest_trend
    except ImportError:
        pass
    try:
        from backtest_mr_enhanced import backtest_mr
        strategy_map["mean_reversion"] = backtest_mr
    except ImportError:
        pass
    try:
        from backtest_breakout  import backtest_breakout
        strategy_map["breakout"]      = backtest_breakout
    except ImportError:
        pass
    try:
        from backtest_scalping  import backtest_scalping
        strategy_map["scalping"]      = backtest_scalping
    except ImportError:
        pass
    try:
        from backtest_ma_cross  import backtest_ma_cross
        strategy_map["ma_cross"]      = backtest_ma_cross
    except ImportError:
        pass

    if not strategy_map:
        logger.warning("No backtest modules found — walk-forward skipped")
        return {}

    # Fetch full data once
    full_data = None
    if data_fetcher is not None:
        try:
            full_data = data_fetcher.get_market_data(
                "NIFTY",
                interval = "5m",
                days     = WF_TOTAL_DAYS,
            )
            if full_data is not None:
                logger.info("WF: fetched %d bars for NIFTY (%d days)",
                            len(full_data), WF_TOTAL_DAYS)
        except Exception as exc:
            logger.warning("WF: data fetch failed: %s", exc)

    if full_data is None or len(full_data) < 500:
        logger.warning("WF: insufficient data — skipping walk-forward")
        return {}

    results     = {}
    params_dir  = Path(best_params_dir)

    params_files = {
        "trend":         "best_params_trend.json",
        "mean_reversion": "best_params_mr.json",
        "breakout":      "best_params_breakout.json",
        "scalping":      "best_params_scalping.json",
        "ma_cross":      "best_params_ma.json",
    }

    for strategy_name, backtest_fn in strategy_map.items():
        pf = params_dir / params_files.get(strategy_name, "")
        if not pf.exists():
            logger.info("WF[%s]: no best_params file found (%s) — skipping", strategy_name, pf)
            continue

        try:
            payload     = json.loads(pf.read_text(encoding="utf-8"))
            best_params = payload.get("params", {})
        except Exception as exc:
            logger.warning("WF[%s]: failed to load params from %s: %s", strategy_name, pf, exc)
            continue

        wf_result = run_walk_forward(
            strategy_name    = strategy_name,
            backtest_fn      = backtest_fn,
            full_data        = full_data,
            best_params      = best_params,
            initial_capital  = initial_capital,
            interval_minutes = interval_minutes,
        )

        if wf_result is not None:
            results[strategy_name] = wf_result.to_dict()

    # Save results
    if results:
        try:
            out = Path(results_file)
            out.write_text(
                json.dumps(
                    {"timestamp": str(date.today()), "results": results},
                    indent=2, default=str,
                ),
                encoding="utf-8",
            )
            logger.info("Walk-forward results saved → %s", results_file)
        except Exception as exc:
            logger.warning("Failed to save walk-forward results: %s", exc)

    return results


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    import sys

    logger.info("Walk-forward backtest — standalone run")
    logger.info("Config: train=%dd test=%dd min_windows=%d total=%dd",
                WF_TRAIN_DAYS, WF_TEST_DAYS, WF_MIN_WINDOWS, WF_TOTAL_DAYS)

    # Try to fetch data via DataFetcher
    data_fetcher = None
    try:
        from data_fetcher import DataFetcher
        data_fetcher = _get_angel_data_fetcher()
        logger.info("DataFetcher initialised")
    except Exception as exc:
        logger.warning("DataFetcher unavailable: %s", exc)

    results = run_walk_forward_all(data_fetcher=data_fetcher)

    if not results:
        logger.error("No walk-forward results produced")
        raise SystemExit(1)  # safe in threads

    print("\n" + "=" * 70)
    print("WALK-FORWARD RESULTS SUMMARY")
    print("=" * 70)
    for name, r in sorted(results.items(), key=lambda x: -x[1]["consistency_score"]):
        print(f"\n{name.upper()}")
        print(f"  Windows    : {r['windows_run']} run, {r['windows_profitable']} profitable "
              f"({r['pct_profitable']:.0%})")
        print(f"  Avg P&L    : ₹{r['avg_pnl']:+.2f}")
        print(f"  Avg Sharpe : {r['avg_sharpe']:.3f}  (std={r['sharpe_std']:.3f})")
        print(f"  Avg WR     : {r['avg_win_rate']:.1%}")
        print(f"  Avg DD     : ₹{r['avg_drawdown']:.2f}")
        print(f"  Consistency: {r['consistency_score']:.3f}")

    print(f"\nSaved to: {WF_RESULTS_FILE}")
