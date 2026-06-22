#!/usr/bin/env python3
"""
test_live_eligibility.py

Focused tests for the conservative live eligibility manifest.

Run:
    python test_live_eligibility.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from live_eligibility import build_manifest, strategy_status


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_blocks_failed_and_missing_validation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        validation = root / "validation_results.json"
        edge = root / "strategy_validation_report.json"
        output = root / "live_eligibility.json"

        _write(validation, {
            "results": {
                "trend": {"verdict": "FAIL", "dev_avg_sharpe": 0.2},
                "breakout": {"verdict": "PASS", "dev_avg_sharpe": 1.4},
                "orb": {"verdict": "INSUFFICIENT_DATA"},
            }
        })
        _write(edge, {
            "edge_confirmed": ["mean_reversion"],
            "negative_edge": ["fallback"],
            "strategies": {
                "mean_reversion": {"status": "EDGE_CONFIRMED"},
                "fallback": {"status": "NEGATIVE_EDGE"},
            },
        })

        manifest = build_manifest(
            validation_file=str(validation),
            edge_file=str(edge),
            output_file=str(output),
        )

        checks = [
            manifest["live_ready_count"] == 1,
            manifest["strategies"]["breakout"]["live_ready"] is True,
            manifest["strategies"]["trend"]["live_ready"] is False,
            manifest["strategies"]["orb"]["live_ready"] is False,
            manifest["strategies"]["mean_reversion"]["live_ready"] is False,
            manifest["strategies"]["fallback"]["block_reason"] == "edge_negative_edge",
            output.exists(),
        ]
        assert all(checks)


def test_strategy_status_unknown_blocks():
    status = strategy_status("unknown", {"strategies": {}})
    assert (
        status["live_ready"] is False
        and status["paper_training_only"] is True
        and status["block_reason"] == "strategy_missing_from_live_eligibility_manifest"
    )


def test_stale_validation_blocks_pass():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        validation = root / "validation_results.json"
        edge = root / "strategy_validation_report.json"
        output = root / "live_eligibility.json"

        _write(validation, {
            "results": {
                "breakout": {"verdict": "PASS", "dev_avg_sharpe": 1.4},
            }
        })
        _write(edge, {"strategies": {}})

        old = time.time() - (20 * 86400)
        os.utime(validation, (old, old))

        manifest = build_manifest(
            validation_file=str(validation),
            edge_file=str(edge),
            output_file=str(output),
            max_validation_age_days=14,
        )

        assert (
            manifest["live_ready_count"] == 0
            and "validation_file_stale" in manifest["warnings"]
            and manifest["strategies"]["breakout"]["block_reason"] == "stale_validation_results"
            and len(manifest["source_files"]["validation"]["sha256"]) == 64
        )


def main() -> int:
    tests = [
        ("blocks failed/missing validation", test_manifest_blocks_failed_and_missing_validation),
        ("unknown strategy blocks", test_strategy_status_unknown_blocks),
        ("stale validation blocks pass", test_stale_validation_blocks_pass),
    ]
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok is None:
            ok = True
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
