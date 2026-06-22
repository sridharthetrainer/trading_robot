#!/usr/bin/env python3
"""
test_autonomous_learning_cycle.py

Run:
    python test_autonomous_learning_cycle.py
"""

from __future__ import annotations

from autonomous_learning_cycle import render_summary, run_autonomous_learning_cycle


def test_cycle_dry_run_has_required_steps() -> None:
    report = run_autonomous_learning_cycle(dry_run=True)
    steps = report.get("steps", {})
    summary = render_summary(report)
    assert report.get("dry_run") is True
    for required in (
        "option_backfill",
        "signal_trade_reconcile",
        "signal_reverse_engineering",
        "eod_signal_mining",
        "confluence_feature_store",
        "candle_coverage_backfill",
        "derive_daily_candles",
        "data_quality_watchdog",
        "shadow_portfolio",
        "market_snapshot",
        "option_chain_snapshot",
        "option_structure_mining",
        "option_autotune",
        "live_eligibility",
        "live_probation",
        "universe",
        "data_pipeline",
        "learning_coverage",
        "quality_gate",
    ):
        assert required in steps
    assert "learning_delta" in report
    for expected in (
        "AUTONOMOUS LEARNING CYCLE",
        "reconcile trades=",
        "reverse-engineer ready=",
        "eod miner skipped=",
        "feature stores confluence_updated=",
        "candle_backfill_skipped=",
        "derived_daily=",
        "snapshots market_skipped=",
        "option structure skipped=",
        "delta selected=",
        "promoted",
        "demoted",
        "probation enabled=",
        "universe learning=",
        "data pipeline ok=",
        "learning coverage rows=",
    ):
        assert expected in summary


def main() -> int:
    tests = [("cycle dry-run has required steps", test_cycle_dry_run_has_required_steps)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            ok = True
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
