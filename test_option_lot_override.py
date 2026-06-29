"""Dynamic option lot ceiling (Telegram /optlots) — applied as a cap, daily-expiring."""
import json
from datetime import date

import option_lot_override as olo


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(olo, "_FILE", tmp_path / "olo.json")


def test_unset_returns_none_and_passthrough(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert olo.get_lots_override() is None
    assert olo.apply_override(5) == 5          # no override → unchanged


def test_set_caps_lots_but_never_forces_a_trade(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    olo.set_lots_override(2)
    assert olo.get_lots_override() == 2
    assert olo.apply_override(5) == 2          # ceiling applied
    assert olo.apply_override(1) == 1          # below ceiling → unchanged
    assert olo.apply_override(0) == 0          # not affordable → no forced trade


def test_clear_and_zero(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    olo.set_lots_override(3)
    olo.set_lots_override(0)                   # 0 clears
    assert olo.get_lots_override() is None
    olo.set_lots_override(2); olo.clear_lots_override()
    assert olo.get_lots_override() is None


def test_hard_max_cap(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    olo.set_lots_override(999)
    assert olo.get_lots_override() == olo._HARD_MAX


def test_expires_next_day(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    (tmp_path / "olo.json").write_text(json.dumps({"lots": 3, "active": True, "date": "2000-01-01"}))
    assert olo.get_lots_override() is None     # stale date → ignored
