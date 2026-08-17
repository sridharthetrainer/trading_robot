"""
minimum_detectable_edge_original11.py — extends minimum_detectable_edge.py's
per-strategy power analysis to the original 10 rule-based strategies (the
ones validated via validation_harness.py, stored in validation_results.json).
fibonacci (tested separately via run_extended_validation.py) is not included
here -- different reporting shape, would need its own pass.

Why a separate script rather than folding these into minimum_detectable_edge.py
directly: these strategies store an already-ANNUALIZED Sharpe
(mean/std * sqrt(252)) in validation_results.json, not raw per-trade returns.
Reverse-engineering per-trade std from that annualized number was exactly the
unit-conflation mistake caught in external review this session (confusing
sqrt(252) with sqrt(n_trades)). To avoid repeating it, this script does NOT
touch the stored Sharpe figures at all -- it re-runs each strategy's actual
backtest_fn on the SAME holdout split validation_harness.py uses
(split_holdout(), last 20% of bars), with the best_params validation_results.json
already recorded, and computes per-trade stats directly from the raw trades
list each backtest_fn returns. Same data source (candle_cache), same split
function, same classify() logic as the seminar-strategy analysis -- only the
strategy population differs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from candle_cache import get_cached_candles
from validation_harness import split_holdout, HOLDOUT_RATIO
from minimum_detectable_edge import _per_trade_stats, classify

PARAM_TRAIN_DAYS = 210
SYMBOL = "NIFTY"
INTERVAL = "5m"

# (module, function) -- matches autonomous_param_trainer.py's STRATEGY_BACKTESTS
STRATEGY_MODULES = {
    "trend": ("backtest_trend", "backtest_trend"),
    "mean_reversion": ("backtest_mr_enhanced", "backtest_mr"),
    "breakout": ("backtest_breakout", "backtest_breakout"),
    "ma_cross": ("backtest_ma_cross", "backtest_ma_cross"),
    "scalping": ("backtest_scalping", "backtest_scalping"),
    "ema_5min": ("backtest_5min_ema", "backtest_5min_ema"),
    "cpr": ("backtest_cpr", "backtest_cpr"),
    "orb": ("backtest_orb", "backtest_orb"),
    "vwap_reversion": ("backtest_vwap_reversion", "backtest_vwap_reversion"),
    "supertrend_mtf": ("backtest_supertrend_mtf", "backtest_supertrend_mtf"),
}


def _normalise(df):
    if df is None or getattr(df, "empty", True):
        return df
    canon = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
             "volume": "Volume", "adj close": "Close", "adj_close": "Close"}
    out = df.copy()
    out.columns = [canon.get(str(c).lower(), c) for c in out.columns]
    return out


def _import_backtest_fn(module_name: str, fn_name: str):
    import importlib
    mod = importlib.import_module(module_name)
    return getattr(mod, fn_name)


def run() -> Dict[str, Any]:
    best_params_by_strategy = json.load(open("validation_results.json"))["results"]

    raw = get_cached_candles(SYMBOL, interval=INTERVAL, days=PARAM_TRAIN_DAYS)
    full_data = _normalise(raw)
    if full_data is None or len(full_data) < 100:
        return {"error": f"insufficient {SYMBOL} {INTERVAL} data ({0 if full_data is None else len(full_data)} bars)"}
    _, holdout_df = split_holdout(full_data, HOLDOUT_RATIO)

    results = {}
    for name, (module_name, fn_name) in STRATEGY_MODULES.items():
        best_params = best_params_by_strategy.get(name, {}).get("best_params", {}) or {}
        try:
            fn = _import_backtest_fn(module_name, fn_name)
            r = fn(symbol=SYMBOL, data=holdout_df, initial_capital=100_000.0,
                   interval_minutes=5, verbose=False, **best_params)
        except Exception as exc:
            results[name] = {"error": f"backtest_fn failed: {exc}"}
            continue
        trades = r.get("trades", [])
        stats = _per_trade_stats(trades)
        results[name] = classify(stats)

    Path("minimum_detectable_edge_original11_report.json").write_text(
        json.dumps(results, indent=2, default=str))
    return results


if __name__ == "__main__":
    rep = run()
    if "error" in rep:
        print(rep["error"])
    else:
        for name, r in rep.items():
            if "error" in r:
                print(f"{name:18s} ERROR: {r['error']}")
            else:
                print(f"{name:18s} n={r['n']:4d}  verdict={r['verdict']:18s}  {r.get('reason','')}")
