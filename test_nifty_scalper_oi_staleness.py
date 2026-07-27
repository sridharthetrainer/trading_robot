import json
import time
from datetime import date

import nifty_scalper_bot as nsb


def _write_state(tmp_path, monkeypatch, *, refresh_age_sec=None, missing_field=False,
                  wrong_date=False, last_dir="BULLISH"):
    state_file = tmp_path / "oi_tracker_state.json"
    state = {
        "date": "2020-01-01" if wrong_date else date.today().isoformat(),
        "last_dir": {"NIFTY": last_dir},
    }
    if not missing_field:
        ts = time.time() - refresh_age_sec if refresh_age_sec is not None else time.time()
        state["last_refresh_ts"] = {"NIFTY": ts}
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(nsb, "OI_STATE", state_file)


def test_oi_flow_direction_fresh_state_returns_direction(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, refresh_age_sec=60)  # 1 min old
    direction, detail = nsb.oi_flow_direction()
    assert direction == "BULLISH"
    assert "last_dir=BULLISH" in detail


def test_oi_flow_direction_stale_refresh_returns_none(tmp_path, monkeypatch):
    # 2026-07-27 fix: a frozen morning reading (last real refresh 45min ago)
    # must no longer be silently treated as current just because the DATE
    # still matches today.
    _write_state(tmp_path, monkeypatch, refresh_age_sec=45 * 60)
    direction, detail = nsb.oi_flow_direction()
    assert direction is None
    assert "stale" in detail.lower()


def test_oi_flow_direction_wrong_date_returns_none(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, wrong_date=True)
    direction, detail = nsb.oi_flow_direction()
    assert direction is None
    assert "stale" in detail.lower()


def test_oi_flow_direction_missing_refresh_ts_field_treated_as_stale(tmp_path, monkeypatch):
    # Backward-compat: a state file written before this fix has no
    # last_refresh_ts key at all -- must fail closed (stale), not crash or
    # silently trust an unknown-age reading.
    _write_state(tmp_path, monkeypatch, missing_field=True)
    direction, detail = nsb.oi_flow_direction()
    assert direction is None
    assert "stale" in detail.lower()


def test_oi_flow_direction_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(nsb, "OI_STATE", tmp_path / "does_not_exist.json")
    direction, detail = nsb.oi_flow_direction()
    assert direction is None
    assert "unavailable" in detail.lower()


def test_oi_flow_direction_boundary_just_inside_threshold(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, refresh_age_sec=nsb._OI_STALE_AFTER_SEC - 30)
    direction, _ = nsb.oi_flow_direction()
    assert direction == "BULLISH"


def test_oi_flow_direction_boundary_just_outside_threshold(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, refresh_age_sec=nsb._OI_STALE_AFTER_SEC + 30)
    direction, detail = nsb.oi_flow_direction()
    assert direction is None
    assert "stale" in detail.lower()
