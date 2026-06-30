#!/usr/bin/env python3
"""
test_signal_reverse_engineer.py

Run:
    .venv/bin/python test_signal_reverse_engineer.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from signal_reverse_engineer import (
    _chronological_reverse_validation,
    build_reverse_engineering_report,
    render_markdown,
)


def _make_db(tmp: str) -> str:
    path = str(Path(tmp) / "signal_log.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE signal_log (
            id INTEGER PRIMARY KEY,
            log_time REAL,
            signal_date TEXT,
            signal_time TEXT,
            symbol TEXT,
            side TEXT,
            strategy TEXT,
            confluence TEXT,
            score REAL,
            regime TEXT,
            htf_bias TEXT,
            entry_price REAL,
            rejection_reason TEXT,
            tb_label INTEGER,
            outcome_price REAL,
            bhav_delivery REAL,
            ai_score REAL,
            volume_ratio REAL,
            india_vix REAL
        )
        """
    )
    rows = [
        (1, 1000, "2026-06-17", "10:00:00", "AAA", "BUY", "TREND", "STRONG", 8.1, "TREND", "BULLISH", 100, "", 1, 103, 0.2, 0.4, 1.1, 15),
        (2, 1010, "2026-06-17", "10:05:00", "AAA", "BUY", "TREND", "STRONG", 8.3, "TREND", "BULLISH", 100, "", 1, 102, 0.2, 0.3, 1.0, 16),
        (3, 1020, "2026-06-17", "10:10:00", "BBB", "SELL", "REVERSAL", "WEAK", 3.2, "RANGING", "NEUTRAL", 100, "score_below_live_min", -1, 103, 0.0, 0.0, 0.2, 21),
        (4, 1030, "2026-06-17", "10:15:00", "CCC", "BUY", "REVERSAL", "WEAK", 3.1, "RANGING", "NEUTRAL", 100, "ai_prob_below_live_min", -99, 0, 0.0, 0.0, 0.2, 21),
    ]
    conn.executemany("INSERT INTO signal_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def test_report_builds_edges_and_pending_profile():
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        report = build_reverse_engineering_report(db_path=db, days=3650, min_samples=2)
        text = render_markdown(report)
        top = report["top_context_edges"][0]
        assert (
            report["ready"] is True
            and report["totals"]["labelled_rows"] == 3
            and report["totals"]["pending_rows"] == 1
            and top["dimension"] == "strategy"
            and top["key"] == "TREND"
            and report["pending_profile"]["top_rejection_reasons"][0]["key"] == "ai_prob_below_live_min"
            and "Signal Reverse Engineering Report" in text
        )


def test_reverse_shadow_uses_all_signals_and_never_enables_live():
    rows = []
    for i in range(100):
        rows.append({
            "signal_date": f"2026-06-{1 + (i // 10):02d}",
            "strategy": "ANTI_EDGE", "side": "BUY",
            "entry_price": 100.0, "outcome_price": 99.0,
            "tb_label": -1, "executed": 0,
        })
    result = _chronological_reverse_validation(rows, min_samples=100)

    assert result["scope"] == "all_generated_labelled_signals"
    assert result["all_signals"] == 100
    assert result["candidates"][0]["strategy"] == "ANTI_EDGE"
    assert result["candidates"][0]["reverse_oos_avg_return_pct"] == 1.0
    assert result["live_reversal_allowed"] is False


def main() -> int:
    tests = [("report builds edges and pending profile", test_report_builds_edges_and_pending_profile)]
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
    raise SystemExit(main())
