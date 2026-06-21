"""
param_sensitivity.py — Parameter sensitivity analysis for backtested strategies.

Answers: "Is this strategy's edge real, or does it only work for one specific
parameter value?" (AlgoTest / QuantConnect approach to overfitting detection)

Methodology
-----------
For each parameter, vary it ±10%, ±20%, ±30% from the optimised value and
record the Profit Factor, Sharpe, and Win Rate. A robust strategy shows
smooth degradation; an overfit strategy collapses immediately when the
parameter changes by even 5%.

Usage
-----
    python3 param_sensitivity.py --strategy trend --symbol NIFTY
    python3 param_sensitivity.py --strategy breakout --symbol BANKNIFTY

Output:
    param_sensitivity_<strategy>.json   (machine-readable)
    Console table with stability flags (✓ robust / ⚠ borderline / ✗ brittle)

Rules
-----
- Does NOT modify any live strategy parameters.
- Does NOT write to trades.db or signal_log.db.
- Read-only analysis tool.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Supported strategy → backtest module mapping ─────────────────────────────
_STRATEGY_MAP = {
    "trend":    ("backtest_trend",    "run_backtest"),
    "breakout": ("backtest_breakout", "run_backtest"),
    "ma":       ("backtest_ma_cross", "run_backtest"),
    "mr":       ("backtest_mr_enhanced", "run_backtest"),
}

# Parameters to scan per strategy (name, default, step_pct, min, max)
_PARAM_RANGES: Dict[str, List[Tuple[str, Any, float, Any, Any]]] = {
    "trend": [
        ("atr_multiplier",   1.5,  0.1,  0.5,  3.5),
        ("ema_fast",         9,    0.2,  5,    20),
        ("ema_slow",         21,   0.2,  10,   50),
        ("adx_threshold",    20,   0.15, 10,   35),
    ],
    "breakout": [
        ("lookback",         20,   0.2,  10,   40),
        ("atr_multiplier",   1.5,  0.15, 0.5,  3.0),
        ("volume_factor",    1.2,  0.15, 1.0,  2.0),
    ],
    "ma": [
        ("fast_period",      9,    0.2,  5,    20),
        ("slow_period",      21,   0.2,  10,   50),
        ("signal_period",    5,    0.2,  3,    15),
    ],
    "mr": [
        ("rsi_period",       14,   0.2,  7,    28),
        ("rsi_oversold",     30,   0.15, 20,   40),
        ("rsi_overbought",   70,   0.15, 60,   80),
        ("bb_period",        20,   0.2,  10,   40),
    ],
}

_PERTURBATIONS = [-0.30, -0.20, -0.10, 0.0, +0.10, +0.20, +0.30]


def _perturb(value: Any, pct: float) -> Any:
    """Return value * (1 + pct), rounded to same type."""
    if isinstance(value, int):
        return max(1, round(value * (1 + pct)))
    return round(float(value) * (1 + pct), 4)


def _run_backtest_with_params(
    strategy: str,
    symbol: str,
    params: Dict[str, Any],
    days: int = 90,
) -> Optional[Dict[str, Any]]:
    """Run the backtest for this strategy with given params. Returns metrics dict or None."""
    if strategy not in _STRATEGY_MAP:
        logger.error("Unknown strategy: %s", strategy)
        return None
    mod_name, fn_name = _STRATEGY_MAP[strategy]
    try:
        import importlib
        mod = importlib.import_module(mod_name)
        run_fn = getattr(mod, fn_name)
        result = run_fn(symbol=symbol, days=days, **params)
        if result is None:
            return None
        return {
            "profit_factor": float(result.get("profit_factor", 0) or 0),
            "sharpe":        float(result.get("sharpe", 0) or 0),
            "win_rate":      float(result.get("win_rate", 0) or 0),
            "total_pnl":     float(result.get("total_pnl", 0) or 0),
            "n_trades":      int(result.get("n_trades", 0) or 0),
        }
    except Exception as e:
        logger.debug("backtest error (%s %s): %s", strategy, params, e)
        return None


def run_sensitivity(
    strategy: str,
    symbol:   str = "NIFTY",
    days:     int = 90,
) -> Dict[str, Any]:
    """
    Run full parameter sensitivity scan for a strategy.
    Returns a dict with per-parameter degradation tables.
    """
    if strategy not in _STRATEGY_MAP:
        return {"error": f"Unknown strategy. Choose from: {list(_STRATEGY_MAP)}"}

    param_specs = _PARAM_RANGES.get(strategy, [])
    if not param_specs:
        return {"error": f"No parameter specs for strategy '{strategy}'"}

    # Load the best params as the baseline
    best_params_file = Path(f"best_params_{strategy}.json")
    base_params: Dict[str, Any] = {}
    if best_params_file.exists():
        try:
            base_params = json.loads(best_params_file.read_text())
            logger.info("Loaded base params from %s", best_params_file)
        except Exception:
            pass

    # Override with param_specs defaults for any missing key
    for name, default, *_ in param_specs:
        if name not in base_params:
            base_params[name] = default

    # Baseline run
    baseline = _run_backtest_with_params(strategy, symbol, base_params, days)
    if baseline is None:
        return {"error": "Baseline backtest failed — check data feed or strategy module"}

    logger.info("Baseline: PF=%.2f  Sharpe=%.2f  WR=%.0f%%  n=%d",
                baseline["profit_factor"], baseline["sharpe"],
                baseline["win_rate"] * 100, baseline["n_trades"])

    # Per-parameter scan
    sensitivity: Dict[str, List[Dict]] = {}
    for param_name, _default, _step, _min, _max in param_specs:
        base_val = base_params.get(param_name, _default)
        rows = []
        for pct in _PERTURBATIONS:
            perturbed_val = _perturb(base_val, pct)
            perturbed_val = max(_min, min(_max, perturbed_val))
            test_params = {**base_params, param_name: perturbed_val}
            result = _run_backtest_with_params(strategy, symbol, test_params, days)
            if result is None:
                continue
            pf_delta = result["profit_factor"] - baseline["profit_factor"]
            row = {
                "perturbation_pct": pct,
                "param_value":      perturbed_val,
                "profit_factor":    round(result["profit_factor"], 3),
                "pf_delta":         round(pf_delta, 3),
                "sharpe":           round(result["sharpe"], 3),
                "win_rate":         round(result["win_rate"], 3),
                "n_trades":         result["n_trades"],
                "is_baseline":      pct == 0.0,
            }
            rows.append(row)
        sensitivity[param_name] = rows

    # Stability score per parameter: std of PF across perturbations (lower = more stable)
    stability: Dict[str, Dict] = {}
    for param_name, rows in sensitivity.items():
        pfs = [r["profit_factor"] for r in rows if not r["is_baseline"]]
        if not pfs:
            continue
        mean_pf = sum(pfs) / len(pfs)
        std_pf  = (sum((p - mean_pf) ** 2 for p in pfs) / max(len(pfs) - 1, 1)) ** 0.5
        min_pf  = min(pfs)
        # Collapse = PF drops below 1.0 for any perturbation
        n_collapse = sum(1 for p in pfs if p < 1.0)
        stability[param_name] = {
            "mean_pf":      round(mean_pf, 3),
            "std_pf":       round(std_pf, 3),
            "min_pf":       round(min_pf, 3),
            "n_collapse":   n_collapse,
            "robust":       (std_pf < 0.3 and n_collapse == 0),
            "flag":         ("✓ robust" if std_pf < 0.3 and n_collapse == 0
                             else "⚠ borderline" if std_pf < 0.5 and n_collapse <= 2
                             else "✗ brittle"),
        }

    return {
        "strategy":   strategy,
        "symbol":     symbol,
        "days":       days,
        "baseline":   baseline,
        "base_params": base_params,
        "sensitivity": sensitivity,
        "stability":   stability,
    }


def render(result: Dict[str, Any]) -> str:
    if "error" in result:
        return f"ERROR: {result['error']}"
    b = result["baseline"]
    lines = [
        "═" * 60,
        f"PARAMETER SENSITIVITY — {result['strategy'].upper()} on {result['symbol']} ({result['days']}d)",
        "═" * 60,
        f"Baseline: PF={b['profit_factor']:.2f}  Sharpe={b['sharpe']:.2f}  "
        f"WR={b['win_rate']:.0%}  n={b['n_trades']}",
        "",
        "Interpretation: ✓ robust = PF stays ≥1.0 across ±30% param change",
        "                ✗ brittle = strategy likely overfit to this parameter value",
        "",
    ]
    for param_name, stab in result["stability"].items():
        rows = result["sensitivity"].get(param_name, [])
        flag = stab["flag"]
        lines.append(f"── {param_name}  {flag}  (std_PF={stab['std_pf']:.3f}  min_PF={stab['min_pf']:.2f})")
        lines.append(f"   {'Perturbation':>12s}  {'Value':>8s}  {'PF':>6s}  {'ΔPF':>7s}  {'WR':>6s}")
        for r in rows:
            b_mark = " ◄" if r["is_baseline"] else ""
            lines.append(
                f"   {r['perturbation_pct']:>+12.0%}  {r['param_value']:>8}  "
                f"{r['profit_factor']:>6.2f}  {r['pf_delta']:>+7.2f}  "
                f"{r['win_rate']:>5.0%}{b_mark}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Parameter sensitivity for backtested strategies")
    ap.add_argument("--strategy", choices=list(_STRATEGY_MAP), required=True)
    ap.add_argument("--symbol",   default="NIFTY")
    ap.add_argument("--days",     type=int, default=90)
    ap.add_argument("--out",      default=None, help="Output JSON path (default: param_sensitivity_STRATEGY.json)")
    args = ap.parse_args()

    result  = run_sensitivity(args.strategy, args.symbol, args.days)
    text    = render(result)
    print(text)

    out_path = Path(args.out or f"param_sensitivity_{args.strategy}.json")
    out_path.write_text(json.dumps(result, indent=2))
    logger.info("Saved → %s", out_path)
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
