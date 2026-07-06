"""Canonical strict day-count: single source of truth for the edge-review gate."""
import sqlite3
import signal_log


def _mkdb(tmp_path, rows):
    db = tmp_path / "sl.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE signal_log (signal_date TEXT, tb_label INT,
        training_eligible INT, stop_loss REAL, target REAL, rr REAL, tb_stop REAL)""")
    conn.executemany("INSERT INTO signal_log VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()
    return str(db)


def test_strict_count_excludes_pre_risk_level_rows(tmp_path):
    rows = [
        # 2 strict-valid days
        ("2026-07-01", 1, 1, 99.0, 105.0, 2.0, 99.0),
        ("2026-07-02", -1, 1, 99.0, 105.0, 2.0, 99.0),
        # loose-only rows (tb_stop>0 but no risk levels / not eligible) — 2 extra days
        ("2026-06-24", 1, 0, 0.0, 0.0, 0.0, 99.0),
        ("2026-06-25", 0, 1, 0.0, 0.0, 0.0, 99.0),
        # unlabelled — never counts
        ("2026-07-03", -99, 1, 99.0, 105.0, 2.0, 99.0),
    ]
    db = _mkdb(tmp_path, rows)
    assert signal_log.usable_edge_days(db) == 2   # strict, not 4 (loose)


def test_monitor_uses_canonical_gate():
    import nightly_edge_monitor as m
    assert m.MIN_DAYS == signal_log.EDGE_GATE_DAYS == 8
