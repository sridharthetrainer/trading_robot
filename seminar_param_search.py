"""
seminar_param_search.py — grid search + locked holdout for the seminar-sourced
NIFTY option strategies, using the SAME statistical bar as validation_harness.py
(deflated Sharpe >= 0.95, positive holdout, beats buy-and-hold benchmark).

Why a separate runner instead of wiring into autonomous_param_trainer.py /
validation_harness.py directly: those are built around
backtest_fn(symbol, data, **params) -> dict, where the CALLER slices one
underlying price series per grid point / walk-forward window. The seminar
strategies (backtest_bollinger_otm_reversal.py etc.) have a different shape:
each one internally loads its own NIFTY underlying candles AND queries real
option premiums from options_nifty.db per trade, and already accepts its own
start_date/end_date. Forcing them through the (symbol, data) interface would
mean either faking an unused data argument or silently breaking their
internal per-day walk. This reuses the actual statistical machinery
(deflated_sharpe_ratio, buy_hold_sharpe from validation_harness.py) so the
PASS bar is identical -- just the harness plumbing differs.

Design (deliberately simpler than validation_harness.py's nested walk-forward
grid, because these strategies trade at most once/day and 15 months of
history doesn't support shrinking that further into per-window sub-grids):
  1. Chronological 80/20 dev/holdout split by calendar date (HOLDOUT_RATIO,
     same constant as validation_harness.py) -- holdout is LOCKED, never
     touched by the grid search.
  2. Run every grid point once on the dev period, pick the one with the best
     dev Sharpe among points with a real minimum trade count.
  3. Evaluate ONLY that single best-dev point on the holdout period.
  4. deflated_sharpe_ratio() on the dev result, penalized by the REAL number
     of grid points searched (n_trials) -- same multiple-testing correction
     every other strategy's grid search pays.
  5. PASS requires: DSR >= 0.95, holdout net P&L > 0, holdout trades >=
     MIN_TRADES, and the strategy's holdout Sharpe beats a buy-and-hold
     Sharpe on the same underlying over the same holdout window.

Grids are small and grounded in what each seminar description called
"configurable" -- not brute-forced (spec: "avoid combinatorial explosion,
reject economically meaningless configurations").
"""
from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List

from validation_harness import (
    HOLDOUT_RATIO, MIN_TRADES, deflated_sharpe_ratio, buy_hold_sharpe,
)
from single_leg_intraday_option_backtest import load_nifty_candles
from backtest_bollinger_otm_reversal import backtest_bollinger_otm_reversal
from backtest_bollinger_otm_momentum import backtest_bollinger_otm_momentum
from backtest_sma20_atm_option import backtest_sma20_atm_option
from backtest_di_momentum_call import backtest_di_momentum_call
from backtest_adx_long_straddle import backtest_adx_long_straddle
from backtest_rolling_short_straddle import backtest_rolling_short_straddle

logger = logging.getLogger(__name__)

RESULTS_FILE = "seminar_param_search_results.json"
MIN_DEV_TRADES = 10   # a dev-period point needs at least this many trades to be scoreable at all

STRATEGY_SPECS: Dict[str, Dict[str, Any]] = {
    "bollinger_otm_reversal": {
        "fn": backtest_bollinger_otm_reversal,
        "grid": {"period": [14, 20, 26], "std_mult": [1.5, 2.0, 2.5]},
        "fixed": {"lots": 10},
        "interval": "5m",
    },
    "bollinger_otm_momentum": {
        "fn": backtest_bollinger_otm_momentum,
        "grid": {"period": [14, 20, 26], "std_mult": [1.5, 2.0, 2.5]},
        "fixed": {"lots": 10},
        "interval": "5m",
    },
    "sma20_atm_option": {
        "fn": backtest_sma20_atm_option,
        "grid": {"period": [10, 20, 30, 50]},
        "fixed": {"lots": 10},
        "interval": "5m",
    },
    "di_momentum_call": {
        "fn": backtest_di_momentum_call,
        "grid": {"di_period": [10, 14, 21], "mom_period": [5, 10, 15], "di_threshold": [20, 25, 30]},
        "fixed": {"lots": 10},
        "interval": "5m",
    },
    "adx_long_straddle": {
        "fn": backtest_adx_long_straddle,
        "grid": {"period": [10, 14, 21], "threshold": [35, 50, 65]},
        "fixed": {"lots": 10},
        "interval": "1m",
    },
    "rolling_short_straddle": {
        "fn": backtest_rolling_short_straddle,
        # cycle_profit/cycle_loss are literal numbers from the seminar spec,
        # not described as configurable -- only the leg-level SL % was.
        "grid": {"leg_sl_pct": [0.15, 0.20, 0.25]},
        "fixed": {"lots": 10},
        "interval": "5m",
    },
}


def _split_date(interval: str, holdout_ratio: float = HOLDOUT_RATIO) -> str:
    """First holdout-period calendar date for this candle interval's history."""
    df = load_nifty_candles(interval=interval)
    days = sorted(set(df.index.date))
    if len(days) < 10:
        raise ValueError(f"insufficient {interval} history ({len(days)} days) for a dev/holdout split")
    split_idx = max(1, int(len(days) * (1 - holdout_ratio)))
    return str(days[split_idx])


def _grid_combos(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    return [dict(zip(keys, combo)) for combo in itertools.product(*[grid[k] for k in keys])]


def search_strategy(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    fn: Callable = spec["fn"]
    combos = _grid_combos(spec["grid"])
    n_trials = len(combos)
    try:
        split_date = _split_date(spec["interval"])
    except ValueError as exc:
        return {"strategy": name, "verdict": "INSUFFICIENT_DATA", "reason": str(exc)}

    logger.info("[%s] searching %d grid points, dev/holdout split at %s", name, n_trials, split_date)

    dev_runs = []
    for combo in combos:
        params = {**spec["fixed"], **combo}
        try:
            result = fn(**params, end_date=split_date, verbose=False)
        except Exception as exc:
            logger.warning("[%s] dev run failed for %s: %s", name, combo, exc)
            continue
        dev_runs.append({"params": combo, "result": result})

    scoreable = [r for r in dev_runs if r["result"].get("num_trades", 0) >= MIN_DEV_TRADES]
    if not scoreable:
        return {"strategy": name, "verdict": "INSUFFICIENT_DATA", "n_trials": n_trials,
                "reason": "no grid point reached MIN_DEV_TRADES on the dev period"}

    best = max(scoreable, key=lambda r: r["result"].get("sharpe", 0.0))
    best_params = {**spec["fixed"], **best["params"]}
    dev_result = best["result"]

    try:
        holdout_result = fn(**best_params, start_date=split_date, verbose=False)
    except Exception as exc:
        return {"strategy": name, "verdict": "ERROR", "n_trials": n_trials,
                "best_params": best_params, "reason": f"holdout run failed: {exc}"}

    n_holdout = holdout_result.get("num_trades", 0)
    if n_holdout < MIN_TRADES:
        return {
            "strategy": name, "verdict": "INSUFFICIENT_HOLDOUT_DATA", "n_trials": n_trials,
            "best_params": best_params, "dev_sharpe": dev_result.get("sharpe"),
            "dev_trades": dev_result.get("num_trades"), "holdout_trades": n_holdout,
            "min_required": MIN_TRADES,
        }

    dsr = deflated_sharpe_ratio(
        sr=dev_result.get("sharpe", 0.0),
        n_trades=dev_result.get("num_trades", 0),
        n_trials=n_trials,
    )

    underlying = load_nifty_candles(interval=spec["interval"])
    holdout_underlying = underlying[underlying.index.date.astype(str) >= split_date]
    bench_sharpe = buy_hold_sharpe(holdout_underlying, interval_minutes=1 if spec["interval"] == "1m" else 5)
    holdout_sharpe = holdout_result.get("sharpe", 0.0)
    beats_benchmark = (bench_sharpe is None) or (holdout_sharpe > bench_sharpe)

    holdout_positive = holdout_result.get("total_pnl", 0.0) > 0
    dsr_ok = dsr >= 0.95
    verdict = "PASS" if (dsr_ok and holdout_positive and beats_benchmark) else "FAIL"

    return {
        "strategy": name, "verdict": verdict, "n_trials": n_trials,
        "split_date": split_date,
        "best_params": best_params,
        "dev_sharpe": round(dev_result.get("sharpe", 0.0), 4),
        "dev_trades": dev_result.get("num_trades"),
        "deflated_sharpe": round(dsr, 4),
        "dsr_ok": dsr_ok,
        "holdout_sharpe": round(holdout_sharpe, 4),
        "holdout_trades": n_holdout,
        "holdout_total_pnl": holdout_result.get("total_pnl"),
        "holdout_win_rate": holdout_result.get("win_rate"),
        "holdout_positive": holdout_positive,
        "benchmark_buyhold_sharpe": round(bench_sharpe, 4) if bench_sharpe is not None else None,
        "beats_benchmark": beats_benchmark,
    }


def _write_partial(results: Dict[str, Any]) -> None:
    """Write whatever's done so far -- so a timeout/kill mid-run doesn't lose
    already-completed strategies (each one takes real, non-trivial time)."""
    report = {"n_strategies": len(results), "results": results,
               "n_pass": sum(1 for r in results.values() if r["verdict"] == "PASS")}
    Path(RESULTS_FILE).write_text(json.dumps(report, indent=2, default=str))


def run_all(verbose: bool = True) -> Dict[str, Any]:
    results = {}
    for name, spec in STRATEGY_SPECS.items():
        results[name] = search_strategy(name, spec)
        _write_partial(results)   # incremental -- survives a kill mid-run
        if verbose:
            r = results[name]
            print(f"{name:26s} → {r['verdict']:24s} "
                  f"{'DSR=' + str(r.get('deflated_sharpe')) if 'deflated_sharpe' in r else ''}",
                  flush=True)
    report = {"n_strategies": len(results), "results": results,
              "n_pass": sum(1 for r in results.values() if r["verdict"] == "PASS")}
    Path(RESULTS_FILE).write_text(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = run_all()
    print(f"\n{rep['n_pass']} of {rep['n_strategies']} PASS")
