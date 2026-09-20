import json
import sqlite3

import prospective_execution_ledger as pel


def _source(path):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE signal_log (
            id INTEGER PRIMARY KEY, log_time REAL, signal_date TEXT, signal_time TEXT,
            symbol TEXT, side TEXT, strategy TEXT, score REAL, entry_price REAL,
            signal_spread_pct REAL, tb_label INTEGER, tb_r_multiple_net REAL,
            outcome_price REAL, outcome_time REAL)""")


def test_ledger_excludes_pre_freeze_and_is_idempotent(tmp_path):
    source = tmp_path / "signals.db"
    ledger = tmp_path / "ledger.db"
    manifest = tmp_path / "manifest.json"
    _source(source)
    pel.initialise_experiment(
        manifest_path=str(manifest), start_after_date="2026-09-20",
        strategies=["breakout"],
    )
    with sqlite3.connect(source) as conn:
        conn.executemany("INSERT INTO signal_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1, 1.0, "2026-09-20", "10:00", "NIFTY", "BUY", "breakout", 6, 100, .1, 1, .5, 101, 2),
            (2, 2.0, "2026-09-21", "10:00", "NIFTY", "BUY", "breakout", 6, 100, .1, 1, .5, 101, 3),
        ])
    first = pel.sync_from_signal_log(signal_db=str(source), ledger_db=str(ledger), manifest_path=str(manifest))
    second = pel.sync_from_signal_log(signal_db=str(source), ledger_db=str(ledger), manifest_path=str(manifest))
    assert first["captured_added"] == first["outcomes_added"] == 1
    assert second["captured_added"] == second["outcomes_added"] == 0


def test_manifest_tampering_is_detected(tmp_path):
    path = tmp_path / "manifest.json"
    pel.initialise_experiment(manifest_path=str(path), start_after_date="2026-09-20", strategies=["breakout"])
    data = json.loads(path.read_text())
    data["minimum_outcomes"] = 1
    path.write_text(json.dumps(data))
    try:
        pel.initialise_experiment(manifest_path=str(path))
        assert False, "tampered manifest should fail"
    except ValueError as exc:
        assert "integrity" in str(exc)


def test_report_requires_all_pre_registered_gates(tmp_path):
    manifest = tmp_path / "manifest.json"
    ledger = tmp_path / "ledger.db"
    pel.initialise_experiment(manifest_path=str(manifest), start_after_date="2026-09-20", strategies=["breakout"])
    report = pel.build_report(ledger_db=str(ledger), manifest_path=str(manifest), write=False)
    assert report["passed"] is False
    assert report["research_only"] is True
