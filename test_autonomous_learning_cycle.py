#!/usr/bin/env python3
"""
test_autonomous_learning_cycle.py

Run:
    python test_autonomous_learning_cycle.py
"""

from __future__ import annotations

from autonomous_learning_cycle import render_summary, run_autonomous_learning_cycle


def test_cycle_dry_run_has_required_steps() -> bool:
    report = run_autonomous_learning_cycle(dry_run=True)
    steps = report.get("steps", {})
    summary = render_summary(report)
    return (
        report.get("dry_run") is True
        and "option_backfill" in steps
        and "signal_trade_reconcile" in steps
        and "signal_reverse_engineering" in steps
        and "eod_signal_mining" in steps
        and "confluence_feature_store" in steps
        and "data_quality_watchdog" in steps
        and "shadow_portfolio" in steps
        and "market_snapshot" in steps
        and "option_chain_snapshot" in steps
        and "option_autotune" in steps
        and "live_eligibility" in steps
        and "live_probation" in steps
        and "universe" in steps
        and "data_pipeline" in steps
        and "learning_coverage" in steps
        and "quality_gate" in steps
        and "learning_delta" in report
        and "AUTONOMOUS LEARNING CYCLE" in summary
        and "reconcile trades=" in summary
        and "reverse-engineer ready=" in summary
        and "eod miner skipped=" in summary
        and "feature stores confluence_updated=" in summary
        and "snapshots market_skipped=" in summary
        and "delta selected=" in summary
        and "promoted" in summary
        and "demoted" in summary
        and "probation enabled=" in summary
        and "universe learning=" in summary
        and "data pipeline ok=" in summary
        and "learning coverage rows=" in summary
    )


def main() -> int:
    tests = [("cycle dry-run has required steps", test_cycle_dry_run_has_required_steps)]
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
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
