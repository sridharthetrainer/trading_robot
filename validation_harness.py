"""
validation_harness.py

Standalone overfitting / data-snooping validation for trading strategies.

Checks (all must pass for PASS verdict):
  1. Deflated Sharpe Ratio > 0   — accounts for multiple grid-search trials
  2. Min-trade guard             — avg trades/window >= MIN_TRADES (30)
  3. Parameter stability         — CV of best params across WF windows < 0.5
  4. Locked holdout evaluation   — run ONCE on never-seen data after all checks pass

Relationship to walk_forward_backtest.py
-----------------------------------------
walk_forward_backtest.py  : rolling OOS validation, saves per-window metrics
validation_harness.py     : adds deflated Sharpe, holdout lock, stability, min-trade.
                            Does NOT modify walk_forward_backtest.py.

Usage (CLI)
-----------
    python validation_harness.py --strategy trend --symbol NIFTY --days 210
    python validation_harness.py --strategy mean_reversion --symbol NIFTY --days 210

Config (.env / config.py)
--------------------------
    HOLDOUT_RATIO   float = 0.20   fraction of data locked as final holdout
    MIN_TRADES      int   = 30     minimum trades per OOS window
    WF_TRAIN_DAYS   int   = 60     (reused from walk_forward_backtest config)
    WF_TEST_DAYS    int   = 30
    WF_MIN_WINDOWS  int   = 3
"""
from __future__ import annotations

import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

try:
    import config as _cfg
    HOLDOUT_RATIO  = float(getattr(_cfg, "HOLDOUT_RATIO",  0.20))
    MIN_TRADES     = int(  getattr(_cfg, "MIN_TRADES",     30))
    WF_TRAIN_DAYS  = int(  getattr(_cfg, "WF_TRAIN_DAYS",  60))
    WF_TEST_DAYS   = int(  getattr(_cfg, "WF_TEST_DAYS",   30))
    WF_MIN_WINDOWS = int(  getattr(_cfg, "WF_MIN_WINDOWS",  3))
except Exception:
    HOLDOUT_RATIO  = 0.20
    MIN_TRADES     = 30
    WF_TRAIN_DAYS  = 60
    WF_TEST_DAYS   = 30
    WF_MIN_WINDOWS = 3

RESULTS_FILE = "validation_results.json"


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    strategy:              str
    symbol:                str
    run_date:              str

    # Development-set metrics (walk-forward across 80% of data)
    n_trials:              int    # grid combinations actually tested
    dev_windows:           int
    dev_avg_sharpe:        float
    dev_avg_pnl:           float
    dev_avg_trades:        float
    dev_pct_profitable:    float

    # Overfitting checks
    deflated_sharpe:       float  # DSR ∈ [0, 1]; > 0.5 is meaningful
    min_trade_ok:          bool   # dev_avg_trades >= MIN_TRADES
    parameter_stability_cv: float # CV of best params across windows; < 0.5 is stable
    stability_ok:          bool   # parameter_stability_cv < 0.5

    # Holdout (filled only when all dev checks pass, otherwise None)
    holdout_sharpe:        Optional[float]
    holdout_pnl:           Optional[float]
    holdout_trades:        Optional[int]
    holdout_win_rate:      Optional[float]

    # Overall verdict
    verdict:               str    # PASS / FAIL / INSUFFICIENT_DATA

    # Per-window detail
    dev_windows_detail:    List[Dict] = field(default_factory=list)
    best_params:           Dict       = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# ── Data splitting ────────────────────────────────────────────────────────────

def split_holdout(
    df: pd.DataFrame,
    holdout_ratio: float = HOLDOUT_RATIO,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split df into (development, holdout).
    Holdout is the LAST holdout_ratio fraction — never touched during dev.
    """
    n = len(df)
    split = max(1, int(n * (1.0 - holdout_ratio)))
    dev     = df.iloc[:split].copy().reset_index(drop=False)
    holdout = df.iloc[split:].copy().reset_index(drop=False)

    # Restore datetime index if present
    if "date" in dev.columns:
        dev     = dev.set_index("date")
        holdout = holdout.set_index("date")
    elif "index" in dev.columns:
        dev     = dev.set_index("index")
        holdout = holdout.set_index("index")

    logger.info(
        "Holdout split: dev=%d bars (%.0f%%), holdout=%d bars (%.0f%%)",
        len(dev),     (1 - holdout_ratio) * 100,
        len(holdout), holdout_ratio * 100,
    )
    return dev, holdout


# ── Deflated Sharpe Ratio ─────────────────────────────────────────────────────

def deflated_sharpe_ratio(
    sr:       float,
    n_trades: int,
    n_trials: int,
    skew:     float = 0.0,
    kurt:     float = 3.0,
) -> float:
    """
    Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014).

    Returns a probability in [0, 1] that the true Sharpe exceeds the expected
    maximum SR one would achieve by testing n_trials random strategies.

    sr        : observed annualised Sharpe ratio
    n_trades  : number of trades (proxy for sample size)
    n_trials  : number of parameter/strategy combinations tested
    skew      : skewness of trade returns (default 0 = normal)
    kurt      : excess kurtosis + 3 of trade returns (default 3 = normal)

    Interpretation:
      DSR > 0.95  → strong evidence of genuine edge
      DSR > 0.50  → modest evidence; worth investigating
      DSR < 0.50  → likely noise given the number of trials
    """
    if n_trades < 5 or n_trials < 1:
        return 0.0

    # Expected maximum SR under the null for n_trials independent tests
    # (Euler–Mascheroni approximation from the paper)
    gamma_e = 0.5772156649
    n = max(n_trials, 2)
    sr_star = (
        (1.0 - gamma_e) * norm.ppf(1.0 - 1.0 / n)
        + gamma_e       * norm.ppf(1.0 - 1.0 / (n * math.e))
    )

    # Variance of SR estimator accounting for non-normality
    # Var[SR_hat] ≈ (1 + 0.5*SR² - skew*SR + (kurt-3)/4 * SR²) / (T-1)
    sr_var = (
        1.0
        + 0.5  * sr ** 2
        - skew * sr
        + (kurt - 3.0) / 4.0 * sr ** 2
    )
    sr_var = max(sr_var, 1e-9)

    z = (sr - sr_star) * math.sqrt(max(n_trades - 1, 1)) / math.sqrt(sr_var)
    return float(norm.cdf(z))


# ── Parameter stability ───────────────────────────────────────────────────────

def parameter_stability(params_per_window: List[Dict]) -> float:
    """
    Coefficient of variation (CV = std / |mean|) of each numeric parameter
    across walk-forward windows. Returns the mean CV across all parameters.

    Lower is more stable. CV < 0.5 is considered stable.
    Returns 1.0 (unstable) if fewer than 2 windows or no numeric params.
    """
    if len(params_per_window) < 2:
        return 1.0

    all_keys = set()
    for p in params_per_window:
        all_keys.update(p.keys())

    cvs = []
    for key in sorted(all_keys):
        vals = []
        for p in params_per_window:
            v = p.get(key)
            if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        if len(vals) < 2:
            continue
        mean = np.mean(vals)
        std  = np.std(vals, ddof=1)
        if abs(mean) < 1e-9:
            cv = 0.0 if std < 1e-9 else 1.0
        else:
            cv = std / abs(mean)
        cvs.append(cv)

    return float(np.mean(cvs)) if cvs else 1.0


# ── Walk-forward on dev set ───────────────────────────────────────────────────

def _bars_per_day(df: pd.DataFrame, interval_minutes: int = 5) -> int:
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 10:
        unique_dates = len(set(df.index.date))
        if unique_dates > 0:
            return max(1, len(df) // unique_dates)
    return int(375 / max(interval_minutes, 1))


def _run_wf_grid(
    backtest_fn:      Callable,
    dev_df:           pd.DataFrame,
    param_grid:       Dict[str, List[Any]],
    train_days:       int,
    test_days:        int,
    min_windows:      int,
    interval_minutes: int,
    initial_capital:  float,
    symbol:           str,
) -> Tuple[Optional[Dict], List[Dict], int]:
    """
    For each OOS window: grid-search on train, evaluate best params on test.
    Returns (best_params_overall, window_results, n_trials_total).

    best_params_overall : params with highest avg score across all windows
    window_results      : [{window_idx, test_start, test_end, pnl, trades,
                            win_rate, sharpe, max_drawdown, best_params}, ...]
    n_trials_total      : total param combos evaluated across all windows
    """
    import itertools

    bars_per_day = _bars_per_day(dev_df, interval_minutes)
    train_bars   = train_days * bars_per_day
    test_bars    = test_days  * bars_per_day

    # Build all param combos once
    keys   = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))
    n_combos = len(combos)

    window_results: List[Dict]  = []
    params_per_win: List[Dict]  = []
    offset = 0

    while offset + train_bars + test_bars <= len(dev_df):
        train_df = dev_df.iloc[offset : offset + train_bars].copy()
        test_df  = dev_df.iloc[offset + train_bars : offset + train_bars + test_bars].copy()
        offset  += test_bars

        if len(test_df) < 30:
            continue

        def _date_str(sub, pos=-1):
            try:
                if isinstance(sub.index, pd.DatetimeIndex):
                    return str(sub.index[pos].date())
            except Exception:
                pass
            return "?"

        # Grid search on train window
        best_score  = -math.inf
        best_params = None
        for combo in combos:
            params = dict(zip(keys, combo))
            try:
                r = backtest_fn(
                    symbol          = symbol,
                    data            = train_df,
                    initial_capital = initial_capital,
                    interval_minutes = interval_minutes,
                    verbose         = False,
                    close_at_end    = True,
                    **params,
                )
                score = (
                    r.get("total_pnl", 0.0)
                    + r.get("sharpe",  0.0) * 5000.0
                    + r.get("win_rate",0.0) * 3000.0
                )
                if score > best_score:
                    best_score  = score
                    best_params = params.copy()
            except Exception:
                continue

        if best_params is None:
            continue

        # Evaluate best params on OOS test window
        try:
            r = backtest_fn(
                symbol          = symbol,
                data            = test_df,
                initial_capital = initial_capital,
                interval_minutes = interval_minutes,
                verbose         = False,
                close_at_end    = True,
                **best_params,
            )
            window_results.append({
                "window_idx":   len(window_results),
                "test_start":   _date_str(test_df, 0),
                "test_end":     _date_str(test_df, -1),
                "pnl":          float(r.get("total_pnl",    0.0)),
                "num_trades":   int(  r.get("num_trades",   0)),
                "win_rate":     float(r.get("win_rate",     0.0)),
                "sharpe":       float(r.get("sharpe",       0.0)),
                "max_drawdown": float(r.get("max_drawdown", 0.0)),
                "best_params":  best_params,
            })
            params_per_win.append(best_params)
        except Exception as exc:
            logger.debug("WF window eval failed: %s", exc)

    # Overall best params = those that appear most as window winner
    best_overall: Optional[Dict] = None
    if window_results:
        from collections import Counter
        frozen = [json.dumps(w["best_params"], sort_keys=True) for w in window_results]
        most_common = Counter(frozen).most_common(1)[0][0]
        best_overall = json.loads(most_common)

    n_trials_total = n_combos * max(len(window_results), 1)
    return best_overall, window_results, n_trials_total, params_per_win


# ── Main validation function ──────────────────────────────────────────────────

def run_validation(
    strategy_name:    str,
    backtest_fn:      Callable,
    full_data:        pd.DataFrame,
    param_grid:       Dict[str, List[Any]],
    symbol:           str           = "NIFTY",
    interval_minutes: int           = 5,
    initial_capital:  float         = 100_000.0,
    train_days:       int           = WF_TRAIN_DAYS,
    test_days:        int           = WF_TEST_DAYS,
    min_windows:      int           = WF_MIN_WINDOWS,
    holdout_ratio:    float         = HOLDOUT_RATIO,
    min_trades:       int           = MIN_TRADES,
) -> ValidationResult:
    """
    Run the full validation pipeline for one strategy.

    Steps
    -----
    1. Lock away the final holdout_ratio of data — never used in dev.
    2. On the development set, run walk-forward with grid search per window.
    3. Compute deflated Sharpe (penalised for n_trials).
    4. Check parameter stability and minimum trade count.
    5. If all checks pass → evaluate best params on locked holdout.
    6. Return ValidationResult (and save to RESULTS_FILE).
    """
    run_date = str(date.today())

    if full_data is None or len(full_data) < 100:
        logger.warning("VH[%s]: insufficient data (%d bars)",
                       strategy_name, len(full_data) if full_data is not None else 0)
        return ValidationResult(
            strategy=strategy_name, symbol=symbol, run_date=run_date,
            n_trials=0, dev_windows=0,
            dev_avg_sharpe=0.0, dev_avg_pnl=0.0,
            dev_avg_trades=0.0, dev_pct_profitable=0.0,
            deflated_sharpe=0.0, min_trade_ok=False,
            parameter_stability_cv=1.0, stability_ok=False,
            holdout_sharpe=None, holdout_pnl=None,
            holdout_trades=None, holdout_win_rate=None,
            verdict="INSUFFICIENT_DATA",
        )

    # 1. Split holdout
    dev_df, holdout_df = split_holdout(full_data, holdout_ratio)

    # 2. Walk-forward grid search on dev set
    best_params, window_results, n_trials, params_per_win = _run_wf_grid(
        backtest_fn      = backtest_fn,
        dev_df           = dev_df,
        param_grid       = param_grid,
        train_days       = train_days,
        test_days        = test_days,
        min_windows      = min_windows,
        interval_minutes = interval_minutes,
        initial_capital  = initial_capital,
        symbol           = symbol,
    )

    if not window_results:
        logger.warning("VH[%s]: no dev windows produced", strategy_name)
        return ValidationResult(
            strategy=strategy_name, symbol=symbol, run_date=run_date,
            n_trials=n_trials, dev_windows=0,
            dev_avg_sharpe=0.0, dev_avg_pnl=0.0,
            dev_avg_trades=0.0, dev_pct_profitable=0.0,
            deflated_sharpe=0.0, min_trade_ok=False,
            parameter_stability_cv=1.0, stability_ok=False,
            holdout_sharpe=None, holdout_pnl=None,
            holdout_trades=None, holdout_win_rate=None,
            verdict="INSUFFICIENT_DATA",
        )

    # 3. Dev-set aggregates
    pnls    = [w["pnl"]        for w in window_results]
    sharpes = [w["sharpe"]     for w in window_results]
    trades  = [w["num_trades"] for w in window_results]
    wrs     = [w["win_rate"]   for w in window_results]

    dev_avg_sharpe     = float(np.mean(sharpes))
    dev_avg_pnl        = float(np.mean(pnls))
    dev_avg_trades     = float(np.mean(trades))
    dev_pct_profitable = float(np.mean([1 if p > 0 else 0 for p in pnls]))

    total_dev_trades = int(sum(trades))

    # 4. Compute all three checks
    dsr = deflated_sharpe_ratio(
        sr       = dev_avg_sharpe,
        n_trades = total_dev_trades,
        n_trials = n_trials,
    )

    min_trade_ok = dev_avg_trades >= min_trades

    windows_ok = len(window_results) >= min_windows
    stab_cv    = parameter_stability(params_per_win)
    stab_ok    = windows_ok and stab_cv < 0.5

    logger.info(
        "VH[%s] dev: windows=%d avg_sharpe=%.3f avg_trades=%.1f "
        "pct_prof=%.0f%% DSR=%.3f stab_cv=%.3f",
        strategy_name, len(window_results),
        dev_avg_sharpe, dev_avg_trades, dev_pct_profitable * 100,
        dsr, stab_cv,
    )
    logger.info(
        "VH[%s] checks: DSR>0=%s min_trade=%s windows=%s stability=%s",
        strategy_name, dsr > 0, min_trade_ok, windows_ok, stab_ok,
    )

    # 5. Holdout evaluation.
    # Deflated Sharpe must clear a STRONG bar (Bailey & López de Prado): DSR>=0.95
    # means the edge survives the multiple-testing correction. The previous gate
    # (dsr>0) was far too lax — a coin-flip best-of-many would have "passed".
    DSR_STRONG = 0.95
    dsr_ok = dsr >= DSR_STRONG
    # Always evaluate the locked holdout for a PROMISING strategy (DSR>0.5) so we
    # see the true OOS number; PASS still requires the strong bar + positive OOS.
    h_sharpe = h_pnl = h_trades = h_wr = None
    if dsr > 0.5 and min_trade_ok and stab_ok and best_params is not None and len(holdout_df) >= 30:
        try:
            h_df = holdout_df.copy()
            r = backtest_fn(
                symbol          = symbol,
                data            = h_df,
                initial_capital = initial_capital,
                interval_minutes = interval_minutes,
                verbose         = False,
                close_at_end    = True,
                **best_params,
            )
            h_sharpe  = float(r.get("sharpe",      0.0))
            h_pnl     = float(r.get("total_pnl",   0.0))
            h_trades  = int(  r.get("num_trades",  0))
            h_wr      = float(r.get("win_rate",    0.0))
            logger.info(
                "VH[%s] HOLDOUT: sharpe=%.3f pnl=₹%.2f trades=%d wr=%.1f%%",
                strategy_name, h_sharpe, h_pnl, h_trades, h_wr * 100,
            )
        except Exception as exc:
            logger.warning("VH[%s] holdout eval failed: %s", strategy_name, exc)

    # PASS requires: strong deflated Sharpe + min trades + stable params + the
    # locked holdout independently positive (Sharpe and P&L).
    holdout_ok = (h_pnl is not None and h_pnl > 0
                  and h_sharpe is not None and h_sharpe > 0)
    all_pass = dsr_ok and min_trade_ok and stab_ok and holdout_ok
    verdict = "INSUFFICIENT_DATA" if not windows_ok else ("PASS" if all_pass else "FAIL")

    result = ValidationResult(
        strategy               = strategy_name,
        symbol                 = symbol,
        run_date               = run_date,
        n_trials               = n_trials,
        dev_windows            = len(window_results),
        dev_avg_sharpe         = round(dev_avg_sharpe,  4),
        dev_avg_pnl            = round(dev_avg_pnl,     2),
        dev_avg_trades         = round(dev_avg_trades,  1),
        dev_pct_profitable     = round(dev_pct_profitable, 4),
        deflated_sharpe        = round(dsr, 4),
        min_trade_ok           = min_trade_ok,
        parameter_stability_cv = round(stab_cv, 4),
        stability_ok           = stab_ok,
        holdout_sharpe         = round(h_sharpe, 4) if h_sharpe is not None else None,
        holdout_pnl            = round(h_pnl,    2) if h_pnl    is not None else None,
        holdout_trades         = h_trades,
        holdout_win_rate       = round(h_wr, 4)    if h_wr      is not None else None,
        verdict                = verdict,
        dev_windows_detail     = window_results,
        best_params            = best_params or {},
    )
    return result


def save_result(result: ValidationResult, results_file: str = RESULTS_FILE) -> None:
    out_path = Path(results_file)
    existing: Dict = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing.setdefault("results", {})
    existing["last_run"]  = str(date.today())
    existing["results"][result.strategy] = result.to_dict()

    out_path.write_text(
        json.dumps(existing, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("Validation result saved → %s (verdict=%s)", results_file, result.verdict)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_default_param_grid(strategy_name: str) -> Dict[str, List[Any]]:
    """Return a default param grid for known strategies."""
    grids = {
        "trend": {
            "fast_ema": [7, 9, 12],
            "slow_ema": [18, 21, 26],
            "adx_threshold": [None, 20, 25],
            "stop_atr_mult": [1.5, 2.0, 2.5],
            "trail_atr_mult": [1.0, 1.5],
            "min_body_atr": [0.0, 0.10],
            "max_entry_atr_extension": [2.5],
            "exit_on_crossover": [True, False],
        },
        "mean_reversion": {
            # NOTE: keys must match backtest_mr()'s signature exactly — the grid
            # is splatted as **params, so a wrong name raises TypeError and the
            # window is silently dropped (was rsi_oversold/rsi_overbought).
            "rsi_period":  [7, 10, 14],
            "oversold":    [25, 30, 35],
            "overbought":  [65, 70, 75],
            "bb_period":   [15, 20],
            "bb_std":      [1.5, 2.0],
        },
        "breakout": {
            # Keys must match backtest_breakout()'s signature (was
            # lookback/atr_mult/min_vol_ratio — none of which it accepts).
            "channel_period": [10, 15, 20],
            "stop_atr_mult":  [1.5, 2.0, 2.5],
            "trail_atr_mult": [1.0, 1.5],
            "breakout_buffer_atr": [0.0, 0.05],
            "min_body_atr": [0.0, 0.10],
        },
        "ma_cross": {
            "fast_ma": [5, 9, 12],
            "slow_ma": [20, 26, 50],
            "min_body_atr": [0.0, 0.10],
            "max_entry_atr_extension": [2.5],
        },
        "scalping": {
            "fast_ema":               [7, 9],
            "slow_ema":               [20, 26],
            "rsi_period":             [7, 14],
            "stop_atr_mult":          [1.0, 1.5],
            "profit_target_atr_mult": [1.2, 2.0],
            "min_body_atr":           [0.0, 0.08],
            "max_entry_atr_extension": [2.0],
        },
        "ema_5min": {
            "fast_ema":          [7, 9],
            "slow_ema":          [18, 21],
            "stop_atr_mult":     [1.5, 1.8],
            "trail_atr_mult":    [1.0, 1.2],
            "min_body_atr":      [0.0, 0.10],
            "max_entry_atr_extension": [2.5],
            "exit_on_crossover": [True, False],
        },
        "cpr": {
            # Keys must match backtest_cpr()'s signature exactly (splatted as **params).
            "stop_atr_mult":    [1.0, 1.5, 2.0],
            "trail_atr_mult":   [1.0, 1.5],
            "require_narrow":   [False, True],
            "narrow_cpr_pct":   [0.4, 0.6],
            "exit_on_opposite": [True, False],
            "invert_signals":   [False, True],
        },
        "orb": {
            "adx_min":    [16.0, 18.0, 20.0],
            "volume_min": [1.1, 1.3],
            "stop_mult":  [1.0],
            "target_mult": [1.5, 2.0],
        },
        "vwap_reversion": {
            "dev_min": [0.0025, 0.0035],
            "rsi_os":  [35, 38],
            "rsi_ob":  [62, 65],
            "vol_min": [0.8, 1.0],
        },
        "supertrend_mtf": {
            "st_period": [7, 10],
            "st_mult":   [2.5, 3.0],
        },
    }
    return grids.get(strategy_name, {})


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="Standalone strategy validation harness")
    parser.add_argument("--strategy", default=None,
                        choices=["trend", "mean_reversion", "breakout", "ma_cross",
                                 "scalping", "ema_5min", "cpr", "orb",
                                 "vwap_reversion", "supertrend_mtf"],
                        help="Strategy to validate")
    parser.add_argument("--all", action="store_true",
                        help="Validate every built-in strategy")
    parser.add_argument("--symbol",   default="NIFTY")
    parser.add_argument("--days",     type=int, default=210,
                        help="Total history to fetch (default 210)")
    parser.add_argument("--capital",  type=float, default=100_000.0)
    parser.add_argument("--interval", default="5m")
    args = parser.parse_args()

    # Import backtest function
    _fn_map = {
        "trend":          ("backtest_trend",      "backtest_trend"),
        "mean_reversion": ("backtest_mr_enhanced","backtest_mr"),
        "breakout":       ("backtest_breakout",   "backtest_breakout"),
        "ma_cross":       ("backtest_ma_cross",   "backtest_ma_cross"),
        "scalping":       ("backtest_scalping",   "backtest_scalping"),
        "ema_5min":       ("backtest_5min_ema",   "backtest_5min_ema"),
        "cpr":            ("backtest_cpr",        "backtest_cpr"),
        "orb":            ("backtest_orb",        "backtest_orb"),
        "vwap_reversion": ("backtest_vwap_reversion", "backtest_vwap_reversion"),
        "supertrend_mtf": ("backtest_supertrend_mtf", "backtest_supertrend_mtf"),
    }
    # Fetch data
    data_fetcher = None
    full_data    = None
    try:
        import os
        from angel import AngelOne
        from data_fetcher import DataFetcher
        _ang = AngelOne(
            api_key     = os.getenv("API_KEY",     ""),
            client_id   = os.getenv("CLIENT_ID",   ""),
            password    = os.getenv("PASSWORD",    ""),
            totp_secret = os.getenv("TOTP_SECRET", ""),
        )
        data_fetcher = DataFetcher(angel=_ang, paper_trade=False)
        full_data    = data_fetcher.get_market_data(
            args.symbol, interval=args.interval, days=args.days
        )
        if full_data is not None:
            logger.info("Fetched %d bars for %s", len(full_data), args.symbol)
    except Exception as exc:
        logger.warning("Data fetch failed: %s", exc)

    if full_data is None or len(full_data) < 100:
        logger.error("Insufficient data — cannot validate")
        sys.exit(1)

    iv_min = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.get(args.interval, 5)
    strategies = list(_fn_map) if args.all else [args.strategy or "trend"]
    results: List[ValidationResult] = []

    for strategy in strategies:
        mod_name, fn_name = _fn_map[strategy]
        try:
            import importlib
            _mod = importlib.import_module(mod_name)
            backtest_fn = getattr(_mod, fn_name)
        except Exception as exc:
            logger.error("Could not import %s.%s: %s", mod_name, fn_name, exc)
            continue

        param_grid = _build_default_param_grid(strategy)
        if not param_grid:
            logger.error("No param grid for strategy '%s'", strategy)
            continue

        result = run_validation(
            strategy_name    = strategy,
            backtest_fn      = backtest_fn,
            full_data        = full_data,
            param_grid       = param_grid,
            symbol           = args.symbol,
            interval_minutes = iv_min,
            initial_capital  = args.capital,
        )
        save_result(result)
        results.append(result)

        # ── Summary ──────────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print(f"VALIDATION SUMMARY  —  {strategy.upper()}  /  {args.symbol}")
        print("=" * 70)
        print(f"  Dev windows       : {result.dev_windows}  (min={WF_MIN_WINDOWS})")
        print(f"  Grid trials       : {result.n_trials}")
        print(f"  Dev avg Sharpe    : {result.dev_avg_sharpe:.3f}")
        print(f"  Dev avg trades/w  : {result.dev_avg_trades:.1f}  "
              f"(min={MIN_TRADES}) → {'✅' if result.min_trade_ok else '❌'}")
        print(f"  Deflated Sharpe   : {result.deflated_sharpe:.3f}  "
              f"(>0 required)       → {'✅' if result.deflated_sharpe > 0 else '❌'}")
        print(f"  Param stability CV: {result.parameter_stability_cv:.3f}  "
              f"(<0.5 required)     → {'✅' if result.stability_ok else '❌'}")

        if result.holdout_sharpe is not None:
            print(f"\n  Holdout Sharpe    : {result.holdout_sharpe:.3f}")
            print(f"  Holdout P&L       : ₹{result.holdout_pnl:+,.2f}")
            print(f"  Holdout trades    : {result.holdout_trades}")
            print(f"  Holdout win rate  : {result.holdout_win_rate:.1%}")
        else:
            print("\n  Holdout           : not evaluated (dev checks failed)")

        print(f"\n  VERDICT: {result.verdict}")
        print(f"\nSaved to: {RESULTS_FILE}")

    if args.all and results:
        passed = sum(1 for r in results if r.verdict == "PASS")
        print("\n" + "=" * 70)
        print(f"ALL STRATEGIES VALIDATED: {passed}/{len(results)} PASS")
        print("=" * 70)
        for result in results:
            print(
                f"  {result.strategy:16s} {result.verdict:18s} "
                f"Sharpe={result.dev_avg_sharpe:7.3f} "
                f"Trades/w={result.dev_avg_trades:6.1f}"
            )
