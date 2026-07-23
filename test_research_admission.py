from datetime import datetime, timedelta

import pytest

import config
import research_admission as ra


def _answers(**overrides):
    base = {q: f"answer for {q}" for q in ra.REQUIRED_QUESTIONS}
    base.update(overrides)
    return base


def test_propose_requires_all_six_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    incomplete = _answers(termination_evidence="")
    with pytest.raises(ValueError, match="missing required answers"):
        ra.propose(incomplete)


def test_require_admission_blocks_unapproved_in_maintenance_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(config, "ALPHA_RESEARCH_MODE", "MAINTENANCE")
    pid = ra.propose(_answers())
    with pytest.raises(RuntimeError, match="not approved"):
        ra.require_admission(pid)


def test_require_admission_noop_outside_maintenance_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(config, "ALPHA_RESEARCH_MODE", "OPEN")
    # never proposed at all -- should still pass since the gate is off
    ra.require_admission("nonexistent_proposal")


def test_approve_refuses_before_cooling_off_elapses(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    pid = ra.propose(_answers())
    with pytest.raises(ValueError, match="cooling-off period not elapsed"):
        ra.approve(pid, approved_by="operator")


def test_approve_succeeds_after_cooling_off_and_then_admits(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr(config, "ALPHA_RESEARCH_MODE", "MAINTENANCE")
    pid = ra.propose(_answers())

    # simulate the cooling-off period having elapsed by rewriting the logged
    # cooling_off_until timestamp into the past
    entries = ra._read_log()
    entries[0]["cooling_off_until"] = (datetime.now() - timedelta(days=1)).isoformat()
    (tmp_path / "log.jsonl").write_text(
        "\n".join(__import__("json").dumps(e, default=str) for e in entries) + "\n"
    )

    ra.approve(pid, approved_by="operator")
    ra.require_admission(pid)  # must not raise now


def test_approve_is_one_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    pid = ra.propose(_answers())
    entries = ra._read_log()
    entries[0]["cooling_off_until"] = (datetime.now() - timedelta(days=1)).isoformat()
    (tmp_path / "log.jsonl").write_text(
        "\n".join(__import__("json").dumps(e, default=str) for e in entries) + "\n"
    )
    ra.approve(pid, approved_by="operator")
    with pytest.raises(ValueError, match="no pending proposal"):
        ra.approve(pid, approved_by="operator")


def test_list_proposals_shows_latest_status_only(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "LOG_FILE", tmp_path / "log.jsonl")
    pid1 = ra.propose(_answers())
    pid2 = ra.propose(_answers())
    entries = ra._read_log()
    for e in entries:
        e["cooling_off_until"] = (datetime.now() - timedelta(days=1)).isoformat()
    (tmp_path / "log.jsonl").write_text(
        "\n".join(__import__("json").dumps(e, default=str) for e in entries) + "\n"
    )
    ra.approve(pid1, approved_by="operator")

    all_props = ra.list_proposals()
    assert len(all_props) == 2  # one entry per id, not one per log line
    approved = ra.list_proposals(status="approved")
    pending = ra.list_proposals(status="pending")
    assert {p["proposal_id"] for p in approved} == {pid1}
    assert {p["proposal_id"] for p in pending} == {pid2}
