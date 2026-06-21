#!/usr/bin/env python3
"""
quality_gate.py

Preflight checks for deployment/live startup.

Run:
    python quality_gate.py

The script is intentionally lightweight: it avoids broker logins and network
calls. It checks syntax for critical modules, refreshes live_eligibility.json,
and blocks real-trading startup when no strategy is live eligible.
"""

from __future__ import annotations

import os
import py_compile
import sys
import importlib
from pathlib import Path
from typing import List, Tuple

from live_eligibility import build_manifest
from live_probation import probation_preflight


CRITICAL_FILES = [
    "config.py",
    "live_eligibility.py",
    "live_probation.py",
    "live_probation_check.py",
    "universe_manager.py",
    "universe_check.py",
    "data_pipeline_audit.py",
    "learning_coverage_report.py",
    "signal_trade_reconciler.py",
    "signal_reverse_engineer.py",
    "eod_signal_miner.py",
    "intraday_candle_recorder.py",
    "option_chain_recorder.py",
    "market_snapshot_recorder.py",
    "confluence_feature_store.py",
    "data_quality_watchdog.py",
    "shadow_portfolio_simulator.py",
    "autonomous_learning_cycle.py",
    "live_signal_engine.py",
    "strategy_selector.py",
    "signal_engine.py",
    "hero_zero_strategy.py",
    "option_decision_journal.py",
    "option_quality.py",
    "option_strike_autotune.py",
    "option_autotune_backfill.py",
    "option_chain_engine.py",
    "option_selector.py",
    "nifty_options_engine.py",
    "trade_manager.py",
    "angel.py",
    "data_fetcher.py",
]


def _env_true(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def compile_critical() -> List[Tuple[str, str]]:
    failures: List[Tuple[str, str]] = []
    for file_name in CRITICAL_FILES:
        path = Path(file_name)
        if not path.exists():
            failures.append((file_name, "missing"))
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            failures.append((file_name, str(exc)))
    return failures


def import_runtime_modules() -> List[Tuple[str, str]]:
    modules = [
        "data_fetcher",
        "live_signal_engine",
        "option_chain_fetcher",
        "universe_manager",
        "data_pipeline_audit",
    ]
    failures: List[Tuple[str, str]] = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append((module, str(exc)))
    return failures


def main() -> int:
    print("QUALITY GATE")
    print("=" * 48)

    failures = compile_critical()
    if failures:
        print("Syntax/critical-file failures:")
        for file_name, reason in failures:
            print(f"  FAIL {file_name}: {reason}")
        return 1
    print(f"PASS critical syntax ({len(CRITICAL_FILES)} files)")

    import_failures = import_runtime_modules()
    if import_failures:
        print("Runtime import failures:")
        for module, reason in import_failures:
            print(f"  FAIL {module}: {reason}")
        print("Hint: run with the project virtualenv, e.g. .venv/bin/python quality_gate.py")
        return 4
    print("PASS runtime imports (data pipeline modules)")

    manifest = build_manifest()
    live_ready_count = int(manifest.get("live_ready_count", 0) or 0)
    total = int(manifest.get("total_strategies", 0) or 0)
    warnings = manifest.get("warnings", []) or []

    print(f"PASS live eligibility manifest refreshed ({live_ready_count}/{total} live-ready)")
    if warnings:
        print(f"FAIL manifest warnings: {', '.join(str(w) for w in warnings)}")
        return 2

    real_trading_requested = (
        _env_true("ENABLE_REAL_TRADING")
        and not _env_true("PAPER_TRADING")
        and not _env_true("PAPER_ORDERS_ONLY")
    )
    if real_trading_requested and live_ready_count <= 0:
        probation = probation_preflight()
        if probation.get("ok"):
            print(
                "WARN real trading requested with no live-ready strategy; "
                "probation-only live mode enabled"
            )
            print(
                "PASS probation preflight "
                f"max_capital=₹{probation.get('max_capital', 0):.0f} "
                f"max_lots={probation.get('max_lots', 0)}"
            )
        else:
            print("FAIL real trading requested but no strategy is live eligible")
            print(f"Reason: {manifest.get('live_block_reason', 'unknown')}")
            if _env_true("LIVE_PROBATION_ENABLED"):
                print(
                    "Probation preflight warnings: "
                    + ", ".join(str(w) for w in probation.get("warnings", []) or ["unknown"])
                )
            return 3

    if live_ready_count <= 0:
        print(f"WARN live orders blocked: {manifest.get('live_block_reason', 'unknown')}")
    print("QUALITY GATE COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
