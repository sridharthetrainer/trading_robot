#!/usr/bin/env python3
"""
test_eod_setup_edge_analyzer.py

Run:
    .venv/bin/python test_eod_setup_edge_analyzer.py
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from eod_signal_miner import ensure_miner_schema
from eod_setup_edge_analyzer import run


def _seed(db_path: str, *, setup: str, days: list[str], return_pct: float,
          per_day: int = 20, symbol: str = "TEST", factors: str = "above_vwap"):
    """Seed candidates with small alternating jitter around return_pct — a
    constant return gives zero variance, which makes the t-test degenerate
    (t=0 for every group, never significant regardless of the true mean)."""
    with sqlite3.connect(db_path) as conn:
        ensure_miner_schema(conn)
        i = 0
        for day in days:
            for j in range(per_day):
                i += 1
                jitter = 0.3 if j % 2 == 0 else -0.3
                ret = return_pct + jitter
                conn.execute(
                    """INSERT OR IGNORE INTO eod_mined_candidates
                       (symbol, candidate_time, candidate_date, side, setup,
                        score, opposition, factors, entry_price, label,
                        return_pct, run_date)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (symbol, f"{day} {9+j%6:02d}:{j%60:02d}:00+05:30", day, "BUY",
                     setup, 5, 2, factors, 100.0, 1 if ret > 0 else -1,
                     ret, day),
                )
        conn.commit()


_TRAIN_DAYS = ["2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"]
_HOLDOUT_DAYS = ["2026-06-29", "2026-06-30"]


def test_gates_when_too_few_mined_days():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eod_signal_miner_test.db")
        _seed(db_path, setup="mtf_momentum", days=["2026-06-25", "2026-06-26"],
              return_pct=1.0)
        rep = run(db_path=db_path, min_days=6)
        assert "error" in rep and rep["days_available"] == 2


def test_candidate_confirmed_in_both_train_and_holdout():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eod_signal_miner_test.db")
        _seed(db_path, setup="mtf_momentum", days=_TRAIN_DAYS, return_pct=1.2, per_day=20)
        _seed(db_path, setup="mtf_momentum", days=_HOLDOUT_DAYS, return_pct=1.2, per_day=20)
        _seed(db_path, setup="range_break", days=_TRAIN_DAYS + _HOLDOUT_DAYS, return_pct=0.0, per_day=20)
        rep = run(db_path=db_path, min_days=6)
        names = {(c["kind"], c["name"]) for c in rep["candidates"]}
        assert ("setup", "mtf_momentum") in names


def test_train_only_positive_flipping_negative_does_not_survive():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eod_signal_miner_test.db")
        _seed(db_path, setup="volume_breakout", days=_TRAIN_DAYS, return_pct=2.0, per_day=25)
        _seed(db_path, setup="volume_breakout", days=_HOLDOUT_DAYS, return_pct=-2.0, per_day=25)
        rep = run(db_path=db_path, min_days=6)
        names = {(c["kind"], c["name"]) for c in rep["candidates"]}
        assert ("setup", "volume_breakout") not in names


def main() -> int:
    tests = [
        ("gates when too few mined days", test_gates_when_too_few_mined_days),
        ("candidate confirmed in train and holdout", test_candidate_confirmed_in_both_train_and_holdout),
        ("train-only positive flipping negative doesn't survive", test_train_only_positive_flipping_negative_does_not_survive),
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
    raise SystemExit(main())
