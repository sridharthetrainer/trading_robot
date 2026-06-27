#!/usr/bin/env python3
"""
autonomous_param_trainer.py

Nightly guarded strategy-parameter training.

This connects validation_harness.py (walk-forward grid search + locked holdout)
to param_bridge.py (live parameter consumption). It never promotes parameters
unless the validation harness returns PASS, which requires DSR >= 0.95,
stable parameters, positive holdout and beating buy-and-hold.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


REPORT_JSON = "autonomous_param_training_report.json"


def _normalise_backtest_data(df):
    """Title-case OHLCV columns so cached sources that return lowercase
    (open/high/low/close/volume) match the backtesters' Open/High/Low/Close/Volume
    lookups. Returns df unchanged if empty/None."""
    if df is None or getattr(df, "empty", True):
        return df
    canon = {
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "volume": "Volume", "adj close": "Close", "adj_close": "Close",
    }
    out = df.copy()
    out.columns = [canon.get(str(c).lower(), c) for c in out.columns]
    return out


STRATEGY_BACKTESTS: Dict[str, tuple[str, str]] = {
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

PARAM_FILE_BY_STRATEGY: Dict[str, str] = {
    "trend": "best_params_trend.json",
    "mean_reversion": "best_params_mr.json",
    "breakout": "best_params_breakout.json",
    "ma_cross": "best_params_ma.json",
    "scalping": "best_params_scalping.json",
    "ema_5min": "best_params_ema_5min.json",
    "cpr": "best_params_cpr.json",
    "orb": "best_params_orb.json",
    "vwap_reversion": "best_params_vwap_reversion.json",
    "supertrend_mtf": "best_params_supertrend_mtf.json",
}


def _csv_env(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _interval_minutes(interval: str) -> int:
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.get(
        str(interval or "5m").lower(),
        5,
    )


def _load_cached_data(symbol: str, interval: str, days: int):
    try:
        from candle_cache import get_cached_candles

        return get_cached_candles(symbol, interval=interval, days=days)
    except Exception:
        return None


def _load_live_data(symbol: str, interval: str, days: int):
    if os.getenv("PARAM_TRAIN_ALLOW_LIVE_FETCH", "false").lower() != "true":
        return None
    try:
        from data_fetcher import DataFetcher

        fetcher = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
        return fetcher.get_market_data(symbol, interval=interval, days=days)
    except Exception:
        return None


def _load_training_data(symbol: str, interval: str, days: int):
    # Normalise OHLCV column case so cached sources that return lowercase columns
    # match the backtesters' Open/High/Low/Close/Volume lookups (else a strategy
    # silently sees no data and the param search is meaningless).
    df = _load_cached_data(symbol, interval, days)
    if df is not None and len(df) >= 100:
        return _normalise_backtest_data(df), "candle_cache"
    df = _load_live_data(symbol, interval, days)
    if df is not None and len(df) >= 100:
        return _normalise_backtest_data(df), "live_fetch"
    return None, "insufficient_data"


def _validation_metrics(result: Any, *, paper_training_only: bool = False) -> Dict[str, Any]:
    holdout_wr = getattr(result, "holdout_win_rate", None)
    dev_details = getattr(result, "dev_windows_detail", []) or []
    win_rate = holdout_wr
    if win_rate is None and dev_details:
        vals = [float(row.get("win_rate", 0.0) or 0.0) for row in dev_details]
        win_rate = sum(vals) / max(len(vals), 1)
    return {
        "validation_verdict": getattr(result, "verdict", ""),
        "paper_training_only": bool(paper_training_only),
        "total_trades": int(sum(int(row.get("num_trades", 0) or 0) for row in dev_details)),
        "num_trades": int(getattr(result, "holdout_trades", 0) or 0),
        "sharpe": float(getattr(result, "holdout_sharpe", 0.0) or getattr(result, "dev_avg_sharpe", 0.0) or 0.0),
        "win_rate": float(win_rate or 0.0),
        "deflated_sharpe": float(getattr(result, "deflated_sharpe", 0.0) or 0.0),
        "parameter_stability_cv": float(getattr(result, "parameter_stability_cv", 1.0) or 1.0),
        "holdout_pnl": getattr(result, "holdout_pnl", None),
        "beats_benchmark": bool(getattr(result, "beats_benchmark", False)),
    }


def _write_param_file(
    *,
    strategy: str,
    symbol: Optional[str],
    params: Dict[str, Any],
    metrics: Dict[str, Any],
    result: Any,
    output_dir: str = ".",
) -> str:
    payload = {
        "strategy": strategy,
        "symbol": symbol or "GLOBAL",
        "params": params,
        "metrics": metrics,
        "validation_result": result.to_dict() if hasattr(result, "to_dict") else {},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "autonomous_param_trainer",
    }
    if symbol:
        target_dir = Path(output_dir) / "symbol_params"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{symbol.upper()}_{strategy}.json"
    else:
        path = Path(output_dir) / PARAM_FILE_BY_STRATEGY.get(strategy, f"best_params_{strategy}.json")
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


def _log_experiment_row(
    *,
    strategy: str,
    symbol: str,
    timeframe: str,
    params: Optional[Dict[str, Any]] = None,
    verdict: str = "UNKNOWN",
    n_trials: int = 0,
) -> None:
    try:
        from experiment_registry import log_experiment

        log_experiment(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            params=params or {},
            n_trials=n_trials,
            verdict=verdict,
        )
    except Exception:
        pass


def run_autonomous_param_training(
    *,
    strategies: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    interval: str | None = None,
    days: int | None = None,
    capital: float | None = None,
    max_runs: int | None = None,
    dry_run: bool = False,
    write: bool = True,
) -> Dict[str, Any]:
    from validation_harness import _build_default_param_grid, run_validation, save_result

    selected_strategies = [
        s.strip().lower()
        for s in (strategies or _csv_env("PARAM_TRAIN_STRATEGIES", "trend,breakout,mean_reversion"))
        if s.strip().lower() in STRATEGY_BACKTESTS
    ]
    selected_symbols = [
        s.strip().upper()
        for s in (symbols or _csv_env("PARAM_TRAIN_SYMBOLS", "NIFTY,BANKNIFTY"))
        if s.strip()
    ]
    selected_interval = (interval or os.getenv("PARAM_TRAIN_INTERVAL", "5m")).lower()
    selected_days = int(days or os.getenv("PARAM_TRAIN_DAYS", "210") or 210)
    selected_capital = float(capital or os.getenv("PARAM_TRAIN_CAPITAL", "100000") or 100000)
    run_limit = int(max_runs or os.getenv("PARAM_TRAIN_MAX_RUNS", "3") or 3)

    planned = [
        {"symbol": symbol, "strategy": strategy}
        for symbol in selected_symbols
        for strategy in selected_strategies
    ][: max(0, run_limit)]

    report: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": bool(dry_run),
        "interval": selected_interval,
        "days": selected_days,
        "capital": selected_capital,
        "planned": planned,
        "results": [],
        "promoted": [],
        "paper_only": [],
        "skipped": [],
    }

    if dry_run:
        if write:
            Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report

    for item in planned:
        symbol = item["symbol"]
        strategy = item["strategy"]
        grid = _build_default_param_grid(strategy)
        if not grid:
            row = {**item, "status": "skipped", "reason": "no_param_grid"}
            _log_experiment_row(
                strategy=strategy,
                symbol=symbol,
                timeframe=selected_interval,
                verdict="SKIPPED_NO_PARAM_GRID",
            )
            report["skipped"].append(row)
            report["results"].append(row)
            continue
        df, data_source = _load_training_data(symbol, selected_interval, selected_days)
        if df is None or len(df) < 100:
            row = {**item, "status": "skipped", "reason": data_source, "bars": 0}
            _log_experiment_row(
                strategy=strategy,
                symbol=symbol,
                timeframe=selected_interval,
                verdict="INSUFFICIENT_DATA",
            )
            report["skipped"].append(row)
            report["results"].append(row)
            continue
        mod_name, fn_name = STRATEGY_BACKTESTS[strategy]
        try:
            backtest_fn = getattr(importlib.import_module(mod_name), fn_name)
            result = run_validation(
                strategy_name=strategy,
                backtest_fn=backtest_fn,
                full_data=df,
                param_grid=grid,
                symbol=symbol,
                interval_minutes=_interval_minutes(selected_interval),
                initial_capital=selected_capital,
            )
            try:
                save_result(result)
            except Exception:
                pass
            params = dict(getattr(result, "best_params", {}) or {})
            metrics = _validation_metrics(result, paper_training_only=(getattr(result, "verdict", "") != "PASS"))
            row = {
                **item,
                "status": "validated",
                "data_source": data_source,
                "bars": int(len(df)),
                "verdict": getattr(result, "verdict", ""),
                "best_params": params,
                "metrics": metrics,
            }
            if params and getattr(result, "verdict", "") == "PASS":
                global_path = _write_param_file(
                    strategy=strategy,
                    symbol=None,
                    params=params,
                    metrics=metrics,
                    result=result,
                )
                symbol_path = _write_param_file(
                    strategy=strategy,
                    symbol=symbol,
                    params=params,
                    metrics=metrics,
                    result=result,
                )
                row["promoted_paths"] = [global_path, symbol_path]
                report["promoted"].append(row)
            elif params:
                path = _write_param_file(
                    strategy=strategy,
                    symbol=symbol,
                    params=params,
                    metrics=metrics,
                    result=result,
                )
                row["paper_only_path"] = path
                report["paper_only"].append(row)
            else:
                report["skipped"].append({**row, "reason": "no_best_params"})
            report["results"].append(row)
        except Exception as exc:
            row = {**item, "status": "error", "reason": str(exc)}
            _log_experiment_row(
                strategy=strategy,
                symbol=symbol,
                timeframe=selected_interval,
                verdict="ERROR",
            )
            report["skipped"].append(row)
            report["results"].append(row)

    if write:
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def render_summary(report: Dict[str, Any]) -> str:
    lines = [
        "AUTONOMOUS PARAM TRAINING",
        (
            f"planned={len(report.get('planned', []) or [])} "
            f"promoted={len(report.get('promoted', []) or [])} "
            f"paper_only={len(report.get('paper_only', []) or [])} "
            f"skipped={len(report.get('skipped', []) or [])}"
        ),
    ]
    for row in (report.get("results", []) or [])[:20]:
        lines.append(
            f"{row.get('symbol')} {row.get('strategy')} status={row.get('status')} "
            f"verdict={row.get('verdict', '')} reason={row.get('reason', '')}"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default="")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--interval", default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()] or None
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    report = run_autonomous_param_training(
        strategies=strategies,
        symbols=symbols,
        interval=args.interval,
        days=args.days,
        max_runs=args.max_runs,
        dry_run=args.dry_run,
        write=not args.no_write,
    )
    print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
