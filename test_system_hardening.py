import json
from pathlib import Path

import pytest


def test_option_liquidity_fields_fail_closed():
    from option_execution_quality import evaluate_selected_option_execution
    result = evaluate_selected_option_execution(
        {
            "symbol": "NIFTY30JUL2625000CE", "expiry": "2026-07-30",
            "strike": 25000, "option_type": "CE", "premium": 100,
            "oi": 1000, "volume": 1000,
        },
        require_liquidity_fields=True,
    )
    assert not result.ok
    assert "spread_unknown" in result.hard_blocks


def test_incomplete_scalp_contract_is_not_actionable():
    from signal_lifecycle import evaluate_option_candidate
    result = evaluate_option_candidate({
        "selected": {
            "symbol": "NIFTY25000CE", "strike": 25000, "option_type": "CE",
        },
        "cost_positive": False,
    })
    assert not result.actionable
    assert result.stage == "GENERATED"
    assert {"missing_expiry", "missing_premium", "cost_edge_unverified"} <= set(result.reasons)


def test_correlated_candidates_collapse_to_highest_score():
    from signal_lifecycle import consolidate_correlated_candidates
    rows = [
        {"symbol": "NIFTY", "side": "BUY", "score": 7},
        {"symbol": "BANKNIFTY", "side": "BUY", "score": 8},
        {"symbol": "NIFTY", "side": "SELL", "score": 6},
    ]
    selected = consolidate_correlated_candidates(
        rows,
        score=lambda row: row["score"],
        cluster=lambda row: "INDEX",
        side=lambda row: row["side"],
    )
    assert [(row["side"], row["score"]) for row in selected] == [("BUY", 8), ("SELL", 6)]


def test_audit_snapshots_are_immutable_and_hashed(tmp_path):
    from audit_artifacts import write_report_with_snapshot
    latest = tmp_path / "report.json"
    result = write_report_with_snapshot(
        latest, {"value": 1}, snapshot_root=tmp_path / "snapshots"
    )
    snapshot = Path(result["snapshot"])
    original = snapshot.read_bytes()
    write_report_with_snapshot(
        latest, {"value": 2}, snapshot_root=tmp_path / "snapshots"
    )
    assert snapshot.read_bytes() == original
    assert json.loads(latest.read_text())["value"] == 2


def test_written_report_embeds_its_own_snapshot_hash(tmp_path):
    """Regression for a 2026-07-28 audit finding: write_report_with_snapshot
    computed a real sha256 but only returned it -- both production call sites
    discarded the return value, so a report on disk had no way to be checked
    against its own claimed snapshot. Now embedded in the payload itself."""
    from audit_artifacts import write_report_with_snapshot
    latest = tmp_path / "report.json"
    result = write_report_with_snapshot(
        latest, {"value": 1}, snapshot_root=tmp_path / "snapshots"
    )
    on_disk = json.loads(latest.read_text())
    assert on_disk["_audit_snapshot"]["sha256"] == result["sha256"]
    assert Path(on_disk["_audit_snapshot"]["snapshot_file"]) == Path(result["snapshot"])
    assert json.loads(Path(result["snapshot"]).read_text())["_audit_snapshot"]["sha256"] == result["sha256"]


def test_failed_photo_is_spooled_with_local_copy(tmp_path, monkeypatch):
    from alerts import AlertManager
    source = tmp_path / "chart.png"
    source.write_bytes(b"not-a-real-png-but-persistent")
    manager = AlertManager("token", "chat", name="test_hardening")
    manager._spool_file = tmp_path / "spool.jsonl"
    manager._media_spool_dir = tmp_path / "media"
    monkeypatch.setattr(manager, "_post_photo", lambda *args, **kwargs: False)
    monkeypatch.setattr(manager, "flush_spool", lambda *args, **kwargs: 0)

    assert manager.send_photo(str(source), "evidence") is False
    row = json.loads(manager._spool_file.read_text().strip())
    assert row["kind"] == "photo"
    assert Path(row["photo_path"]).exists()


def test_ml_rejects_single_class_dataset():
    import numpy as np
    import ml_trainer
    with pytest.raises(ValueError):
        ml_trainer._train_model(
            np.ones((60, 3)), np.zeros(60, dtype=int), ["a", "b", "c"]
        )


def test_request_governor_is_shared_across_callers(monkeypatch, tmp_path):
    import request_governor
    monkeypatch.setattr(request_governor, "_STATE_DIR", tmp_path)
    clock = {"now": 10.0}
    waits = []
    monkeypatch.setattr(request_governor.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        request_governor.time, "sleep",
        lambda seconds: waits.append(seconds) or clock.__setitem__("now", clock["now"] + seconds),
    )
    request_governor.acquire("broker", 0.5)
    request_governor.acquire("broker", 0.5)
    assert waits == [0.5]


def test_request_governor_shares_state_across_separate_module_instances(monkeypatch, tmp_path):
    """The real bug: two OS processes each import request_governor and get
    their own in-memory state. Simulate that by clearing the in-process
    thread-lock cache between calls -- the file-backed state must still
    enforce the interval since that's what actually crosses process
    boundaries."""
    import request_governor
    monkeypatch.setattr(request_governor, "_STATE_DIR", tmp_path)
    clock = {"now": 10.0}
    waits = []
    monkeypatch.setattr(request_governor.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        request_governor.time, "sleep",
        lambda seconds: waits.append(seconds) or clock.__setitem__("now", clock["now"] + seconds),
    )
    request_governor.acquire("broker", 0.5)
    request_governor._LOCKS.clear()  # simulate a fresh process's empty in-memory cache
    request_governor.acquire("broker", 0.5)
    assert waits == [0.5]
