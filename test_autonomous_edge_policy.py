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
    result = apply_policy({"strategy": "weak", "side": "BUY", "direction": "BUY"}, path)
    assert result["autonomous_edge_status"] == "QUARANTINED"
    assert result["paper_training_mode"] is True
    assert result["side"] is None
    assert result["reason"] == "profit_discipline_quarantined_strategy"


def test_early_ugly_loss_is_quarantined_before_thirty_samples(tmp_path):
    path = tmp_path / "signals.db"
    _make(path, [("ugly", f"2026-06-{20+i%4:02d}", -1, 1, 99, 102, 1.5, -1.2) for i in range(12)])
    result = apply_policy({"strategy": "ugly", "side": "SELL", "direction": "SELL"}, path)
    assert result["autonomous_edge_status"] == "QUARANTINED"
    assert result["side"] is None


def test_small_positive_sample_becomes_paper_promising_only(tmp_path):
    path = tmp_path / "signals.db"
    _make(path, [("new", f"2026-06-{20+i%4:02d}", 1, 1, 99, 102, 1.5, 1.0) for i in range(40)])
    result = strategy_policy("new", path)
    assert result["status"] == "PAPER_PROMISING"
    assert result["live_ready"] is False


def test_stop_loss_label_zero_is_included_in_edge_policy(tmp_path):
    path = tmp_path / "signals.db"
    rows = [
        ("mixed", f"2026-06-{20+i%4:02d}", 1, 1, 99, 102, 1.5, 1.0)
        for i in range(20)
    ] + [
        ("mixed", f"2026-06-{20+i%4:02d}", 0, 1, 99, 102, 1.5, -2.0)
        for i in range(20)
    ]
    _make(path, rows)
    result = strategy_policy("mixed", path)
    assert result["outcomes"] == 40
    assert result["avg_net_r"] == -0.5
    assert result["status"] == "QUARANTINED"
