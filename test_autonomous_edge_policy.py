import sqlite3
from autonomous_edge_policy import apply_policy, strategy_policy


def _make(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("create table signal_log(strategy text,signal_date text,tb_label integer,training_eligible integer,stop_loss real,target real,rr real,tb_r_multiple_net real)")
    conn.executemany("insert into signal_log values(?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def test_negative_clean_edge_is_quarantined(tmp_path):
    path = tmp_path / "signals.db"
    _make(path, [("weak", f"2026-06-{20+i%4:02d}", -1, 1, 99, 102, 1.5, -1.0) for i in range(40)])
    result = apply_policy({"strategy": "weak"}, path)
    assert result["autonomous_edge_status"] == "QUARANTINED"
    assert result["paper_training_mode"] is True


def test_small_positive_sample_cannot_promote(tmp_path):
    path = tmp_path / "signals.db"
    _make(path, [("new", f"2026-06-{20+i%4:02d}", 1, 1, 99, 102, 1.5, 1.0) for i in range(40)])
    result = strategy_policy("new", path)
    assert result["status"] == "VALIDATING"
    assert result["live_ready"] is False
