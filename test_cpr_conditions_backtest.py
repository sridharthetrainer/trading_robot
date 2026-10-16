"""Multi-condition engine (C001-C007): fixed definitions, version A matching,
short-side put mechanics, staged exit, and R001 untouched."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cpr_vwap_ema_nifty import conditions_backtest as cb
from cpr_vwap_ema_nifty import rules as rl
from cpr_vwap_ema_nifty.backtest import load_config

PKG = Path(__file__).resolve().parent / "cpr_vwap_ema_nifty"


def _bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c})


def test_strong_candle_definitions():
    assert not cb.strong_red(_bar(110, 111, 99, 103), 0.6)        # body 7 / range 12 = 0.58 -> not strong
    assert not cb.strong_red(_bar(110, 112, 99, 104), 0.6)        # 6/13
    assert cb.strong_red(_bar(110, 110.5, 99.5, 102), 0.6)        # 8/11
    assert not cb.strong_red(_bar(100, 110, 99, 108), 0.6)        # green
    assert cb.strong_green(_bar(100, 109, 99, 108), 0.6)          # 8/10
    assert not cb.strong_green(_bar(100, 100, 100, 100), 0.6)     # zero range


def _day(rows, start="2026-03-02 09:15"):
    idx = pd.date_range(start, periods=len(rows), freq="5min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).astype(float)
    df["ema20_5m"] = 1000.0
    df["ema50_5m"] = 1000.0
    df["ema200_5m"] = 1000.0
    df["ema200_1m"] = 1000.0
    df["fut_close"] = df["close"]
    df["fut_vwap"] = 1000.0
    return df


LEV = pd.Series({"PDH": 2000.0, "R1": 2010.0, "PDL": 960.0, "S1": 950.0, "WP": 1500.0, "WTC": 900.0,
                 "WBC": 890.0, "TC": 1100.0, "BC": 1090.0, "H3": 0, "H4": 0, "L3": 0, "L4": 0})


def test_short_c006_entry_and_two_strong_bullish_exit():
    # bars: below VWAP, below PDL 960 and all EMAs -> signal at 09:15, entry 09:20 open
    rows = [(955, 956, 940, 945), (944, 945, 930, 932), (931, 940, 929, 933),
            (933, 942, 932, 941), (941, 950, 940, 949), (949, 950, 948, 949.5)]
    cfg = load_config()
    t = cb.simulate(_day(rows), LEV, "vwap_pdl_ema20_resistance_short", {"type": "two_strong_bullish"}, -1, cfg)
    assert t.side == "SHORT" and t.signal_time == "09:15"
    assert t.entry_time.endswith("09:20") and t.entry_px == 944
    # 09:30 green 8/10 strong, 09:35 green 8/10 strong -> exit at 09:40 open 949
    assert t.exit_reason == "two_strong_bullish" and t.exit_time.endswith("09:40") and t.exit_px == 949
    assert t.points == 944 - 949


def test_green_after_three_reds_exit():
    rows = [(990, 991, 979, 980), (980, 981, 969, 970), (970, 971, 959, 960), (960, 961, 949, 950),
            (950, 958, 949, 957), (957, 958, 950, 952)]
    d = _day(rows)
    cfg = load_config()
    # force a signal at 09:15 by using the C006 entry (below VWAP/PDL/EMAs)
    lev = LEV.copy(); lev["PDL"] = 995.0
    t = cb.simulate(d, lev, "vwap_pdl_ema20_resistance_short", {"type": "green_after_reds"}, -1, cfg)
    # reds counted from the entry candle (09:20, 09:25, 09:30) then green 09:35 -> exit 09:40 open
    assert t.exit_reason == "green_after_reds" and t.exit_time.endswith("09:40") and t.exit_px == 957


def test_staged_exit_three_parts_short(monkeypatch):
    monkeypatch.setattr(cb, "find_signal", lambda *a, **k: 0)          # exercise the exit only
    cfg = load_config()
    rows = [(995, 996, 985, 990), (990, 991, 975, 980), (980, 981, 960, 965), (965, 966, 940, 945),
            (945, 946, 930, 935)] + [(935, 936, 934, 935)] * 3
    d = _day(rows)
    d["ema200_5m"] = 970.0                      # stage 1 level below entry
    lev = LEV.copy(); lev["WTC"] = 950.0
    lev["PDL"], lev["S1"] = 940.0, 938.0        # PDL/S1 zone top 940
    t = cb.simulate(d, lev, "vwap_pdl_ema20_resistance_short", {"type": "staged_zones"}, -1, cfg)
    # entry 09:20 open 990; ema 970 touched 09:25 (low 960), weekly CPR 950 at 09:30 (low 940), zone 940 at 09:30
    assert [p[2] for p in t.parts] == ["stage1:ema200_5m", "stage2:weekly_cpr", "stage3:pdl_s1_zone"]
    assert [p[1] for p in t.parts] == [970.0, 950.0, 940.0]
    assert t.points == pytest.approx(np.mean([990 - 970, 990 - 950, 990 - 940]), abs=0.01)


def test_staged_level_not_beyond_entry_uses_next_stage(monkeypatch):
    monkeypatch.setattr(cb, "find_signal", lambda *a, **k: 0)
    cfg = load_config()
    rows = [(995, 996, 985, 990), (990, 991, 975, 980), (980, 981, 945, 950)] + [(950, 951, 949, 950)] * 3
    d = _day(rows)
    d["ema200_5m"] = 999.0                      # ABOVE the 990 entry -> unavailable for a short
    lev = LEV.copy(); lev["WTC"] = 970.0; lev["PDL"], lev["S1"] = 900.0, 890.0
    t = cb.simulate(d, lev, "vwap_pdl_ema20_resistance_short", {"type": "staged_zones"}, -1, cfg)
    assert t.parts[0][2] == "stage1:weekly_cpr" and t.parts[1][2] == "stage2:weekly_cpr"


def test_version_a_match_all_includes_optional_fields():
    row = pd.Series({"cpr_width": "narrow", "golden_pivot_zone": "none", "daily_cpr_inside_weekly": True,
                     "weekly_cpr_vs_monthly": "below"})
    assert cb.match_all(row, {"cpr_width": "narrow", "daily_cpr_inside_weekly": True})
    assert not cb.match_all(row, {"cpr_width": "narrow", "daily_cpr_inside_weekly": False})
    assert not cb.match_all(row, {"golden_pivot_zone": "bullish"})
    assert not cb.match_all(row, {"missing_field": "x"})


def test_put_pricing_for_short_staged_parts():
    from cpr_vwap_ema_nifty import engine as en

    class FakeMod:
        con = True

        def expiry_for(self, d):
            return "2026-10-20"

        def price(self, d, k, opt, when, spot, expiry=None):
            assert opt == "PE"
            return 100.0 + (25010.0 - spot)            # put gains as spot falls

    t = cb.CTrade(date="2026-10-19", side="SHORT", entry_time="2026-10-19 09:30", entry_px=25010.0,
                  exit_time="2026-10-19 12:00", exit_px=24950.0, exit_reason="staged", points=60.0, mfe=0, mae=0,
                  hold_min=150, signal_time="09:25",
                  parts=[("2026-10-19 10:00", 24990.0, "s1"), ("2026-10-19 11:00", 24960.0, "s2"),
                         ("2026-10-19 12:00", 24900.0, "s3")])
    o = cb.option_layers(t, load_config(), FakeMod(), None, set())
    assert o["opt_type"] == "PE" and o["B_net"] > 0


def test_stored_conditions_c001_to_c007_and_r001_unchanged():
    doc = json.loads((PKG / "rules.json").read_text())
    ids = [c["id"] for c in doc["conditions"]]
    assert ids == ["C001", "C002", "C003", "C004", "C005", "C006", "C007"]
    dates = {c["id"]: c["date"] for c in doc["conditions"]}
    assert dates == {"C001": "2026-10-15", "C002": "2026-10-08", "C003": "2026-10-16", "C004": "2026-10-14",
                     "C005": "2026-10-13", "C006": "2026-10-01", "C007": "2026-09-22"}
    by = {c["id"]: c for c in doc["conditions"]}
    # data-verified values stored as approved
    assert by["C005"]["setup"]["cpr_width"] == "normal" and by["C007"]["setup"]["cpr_width"] == "narrow"
    assert by["C006"]["setup"]["cpr_vs_prior_day"] == "outside" and by["C004"]["setup"]["cpr_vs_weekly"] == "overlapping"
    for c in doc["conditions"]:
        assert rl.validate(c) == [], c["id"]
    r = doc["preregistered"][0]
    assert r["fingerprint"] == "3c3daa6c033b15b43c0952d39e3f93544bb1afe0ca3c911d1ada5a7df5809ddf" and rl.frozen_ok(r)
