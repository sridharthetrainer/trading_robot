"""Forward trade journal (/journal): plan before result, append-only, owner-gated."""
from datetime import datetime

import pytest

import manual_journal as mj


def test_plan_then_result_and_list(tmp_path):
    p = tmp_path / "j.jsonl"
    rec = mj.add_plan("short below VWAP, exit close above 1m 200 EMA", user="1", now=datetime(2026, 10, 19, 9, 31), path=p)
    assert rec["id"] == "J-20261019-0931" and rec["type"] == "plan"
    rows = mj.recent(path=p)
    assert rows[0]["result"] is None
    mj.add_result("J-20261019-0931", "+42 pts win", user="1", now=datetime(2026, 10, 19, 11, 0), path=p)
    assert mj.recent(path=p)[0]["result"] == "+42 pts win"


def test_ids_unique_within_same_minute(tmp_path):
    p = tmp_path / "j.jsonl"
    t = datetime(2026, 10, 19, 9, 31)
    assert mj.add_plan("a", now=t, path=p)["id"] == "J-20261019-0931"
    assert mj.add_plan("b", now=t, path=p)["id"] == "J-20261019-0931-2"


def test_append_only_and_unknown_id(tmp_path):
    p = tmp_path / "j.jsonl"
    rec = mj.add_plan("x", now=datetime(2026, 10, 19, 10, 0), path=p)
    mj.add_result(rec["id"], "loss", path=p)
    with pytest.raises(ValueError):
        mj.add_result(rec["id"], "changed my mind", path=p)            # results cannot be rewritten
    with pytest.raises(KeyError):
        mj.add_result("J-NOPE", "win", path=p)
    with pytest.raises(ValueError):
        mj.add_plan("   ", path=p)


def test_handle_commands(tmp_path):
    p = tmp_path / "j.jsonl"
    assert "empty" in mj.handle("/journal", path=p)
    out = mj.handle("/journal plan long above VWAP reason R1 break", user="1", path=p)
    assert "Logged plan" in out
    jid = mj.recent(path=p)[0]["id"]
    assert "Result logged" in mj.handle(f"/journal result {jid} +30 pts", path=p)
    assert jid in mj.handle("/journal list", path=p)
    assert "⚠️" in mj.handle("/journal result J-X win", path=p)


def test_journal_is_owner_only_in_telegram_handler():
    src = open("telegram_commands.py").read() if __import__("os").path.exists("telegram_commands.py") else \
        open(__import__("pathlib").Path(__file__).resolve().parent / "telegram_commands.py").read()
    assert '{"prepare", "orderstatus", "journal"}' in src
    assert 'self.register("journal",' in src
