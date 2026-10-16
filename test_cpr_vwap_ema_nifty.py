"""Tests for the research-only CPR_VWAP_EMA_Nifty package (cpr_vwap_ema_nifty/)."""
import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cpr_vwap_ema_nifty import engine as en
from cpr_vwap_ema_nifty import levels as lv
from cpr_vwap_ema_nifty import rules as rl
from cpr_vwap_ema_nifty.backtest import _bootstrap, is_enabled_live, load_config, metrics
from cpr_vwap_ema_nifty.options_layers import option_net_pnl

REPO = Path(__file__).resolve().parent
PKG = REPO / "cpr_vwap_ema_nifty"


# ── formulas (14-Oct-2026 HLC -> 15-Oct levels, confirmed with the user) ────

H, L, C = 22757.95, 22558.95, 22622.7


def test_cpr_formula():
    c = lv.cpr(H, L, C)
    assert round(c["P"], 1) == 22646.5
    assert c["BC"] == pytest.approx(22634.62, abs=0.01) and c["TC"] == pytest.approx(22658.45, abs=0.01)
    assert round(c["width_pct"], 3) == 0.105


def test_cpr_bc_tc_swapped_when_inverted():
    c = lv.cpr(110, 90, 91)        # close near the low -> raw TC < BC
    assert c["BC"] <= c["P"] <= c["TC"]


def test_traditional_pivots():
    p = (H + L + C) / 3
    t = lv.traditional(H, L, C)
    assert t["R1"] == pytest.approx(2 * p - L) and t["S1"] == pytest.approx(2 * p - H)
    assert t["R2"] == pytest.approx(p + (H - L)) and t["S2"] == pytest.approx(p - (H - L))
    assert t["R3"] == pytest.approx(2 * p + (H - 2 * L)) and t["S3"] == pytest.approx(2 * p - (2 * H - L))
    assert t["R4"] == pytest.approx(3 * p + (H - 3 * L)) and t["S4"] == pytest.approx(3 * p - (3 * H - L))
    assert round(t["R4"], 1) == 23020.7


def test_camarilla():
    c = lv.camarilla(H, L, C)
    assert round(c["H3"], 1) == 22677.4 and round(c["H4"], 1) == 22732.2
    assert c["H5"] == pytest.approx(H / L * C)
    assert c["L5"] == pytest.approx(C - (c["H5"] - C))
    assert c["L3"] == pytest.approx(C - 1.1 * (H - L) / 4) and c["L4"] == pytest.approx(C - 1.1 * (H - L) / 2)


@pytest.mark.parametrize("today,prior,want", [
    ({"BC": 105, "TC": 106, "P": 105.5}, {"BC": 100, "TC": 101, "P": 100.5}, "higher"),
    ({"BC": 95, "TC": 96, "P": 95.5}, {"BC": 100, "TC": 101, "P": 100.5}, "lower"),
    ({"BC": 100.2, "TC": 100.8, "P": 100.5}, {"BC": 100, "TC": 101, "P": 100.5}, "inside"),
    ({"BC": 99, "TC": 102, "P": 100.5}, {"BC": 100, "TC": 101, "P": 100.5}, "outside"),
    ({"BC": 100.5, "TC": 101.5, "P": 101}, {"BC": 100, "TC": 101, "P": 100.5}, "overlapping_higher"),
    ({"BC": 99.5, "TC": 100.5, "P": 100}, {"BC": 100, "TC": 101, "P": 100.5}, "overlapping_lower"),
])
def test_cpr_relation_prior(today, prior, want):
    assert lv.cpr_relation_prior(today, prior) == want


def test_cpr_vs_and_width_and_open_location():
    d = {"BC": 100, "TC": 101, "P": 100.5}
    assert lv.cpr_vs(d, {"BC": 90, "TC": 95}) == "above"
    assert lv.cpr_vs(d, {"BC": 105, "TC": 110}) == "below"
    assert lv.cpr_vs(d, {"BC": 100.5, "TC": 103}) == "overlapping"
    assert lv.width_class(0.1, 0.15, 0.35) == "narrow"
    assert lv.width_class(0.2, 0.15, 0.35) == "normal"
    assert lv.width_class(0.4, 0.15, 0.35) == "wide"
    loc = lv.open_location(22671.3, lv.cpr(H, L, C), 22677.4, 22684.4)
    assert loc == {"open_vs_cpr": "above", "open_vs_daily_h3": "below", "open_vs_weekly_h3": "below"}


# ── no lookahead ─────────────────────────────────────────────────────────────

def _synthetic_1m(days=70, seed=3):
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2026-01-01", periods=days):
        idx += list(pd.date_range(d + pd.Timedelta("09:15:00"), d + pd.Timedelta("15:29:00"), freq="1min"))
    idx = pd.DatetimeIndex(idx)
    c = 20000 + np.cumsum(rng.normal(0, 3, len(idx)))
    return pd.DataFrame({"open": c, "high": c + 2, "low": c - 2, "close": c}, index=idx)


def test_day_levels_use_only_prior_periods():
    s = _synthetic_1m()
    daily = lv.session_ohlc(s)
    base = lv.build_day_levels(daily, 0.15, 0.35)
    day = base.index[40]
    future = daily.copy()
    future.loc[future.index >= day, ["high", "low", "close"]] *= 1.5   # change the day itself and after
    future.loc[day, "open"] = daily.loc[day, "open"]
    changed = lv.build_day_levels(future, 0.15, 0.35)
    cols = [c for c in base.columns if c != "open"]
    pd.testing.assert_series_equal(base.loc[day, cols], changed.loc[day, cols], check_names=False)


def test_intraday_indicators_do_not_see_future_bars():
    s = _synthetic_1m(days=40)
    cfg = load_config()
    f1 = en.build_frame(s, None, cfg)
    t = f1.index[len(f1) // 2]
    s2 = s.copy()
    s2.loc[s2.index >= t + pd.Timedelta(minutes=5), ["open", "high", "low", "close"]] += 500
    f2 = en.build_frame(s2, None, cfg)
    for col in ("ema20_5m", "ema50_5m", "ema200_5m", "ema200_1m", "ema200_30m", "close_30m", "twap"):
        assert f1.loc[t, col] == pytest.approx(f2.loc[t, col]), col


# ── entry / exit mechanics ───────────────────────────────────────────────────

def _day(closes, lows=None, start="2026-03-02 09:15"):
    idx = pd.date_range(start, periods=len(closes), freq="5min")
    c = np.array(closes, dtype=float)
    lo = np.array(lows if lows is not None else c - 1, dtype=float)
    df = pd.DataFrame({"open": c, "high": c + 1, "low": lo, "close": c}, index=idx)
    df["ema20_5m"] = 100.0
    for k in ("ema50_5m", "ema200_5m", "ema200_1m", "ema200_30m"):
        df[k] = 50.0
    df["close_30m"] = 60.0
    df["fut_close"] = df["close"]
    df["fut_vwap"] = 102.0
    df["twap"] = 102.0
    return df


LEV = pd.Series({"R1": 0, "PDH": 0, "PWH": 0, "R4": 1e9, "MH3": 1e9, "S1": 0, "PDL": 0, "PWL": 0,
                 "S4": 0, "ML3": 0})
COND = {"bias": "LONG", "context": {"above_vwap": True, "above_ema20_5m": True},
        "exit": {"type": "close_vs_ema", "timeframe": "5m", "period": 20}}


def test_entry_next_open_exit_on_close_below_ema_not_wick():
    closes = [101, 103, 105, 106, 107, 99, 98]
    lows = [100, 102, 104, 90, 106, 98, 97]          # bar 3 wicks to 90 (< EMA 100) but closes above
    t = en.simulate_day(_day(closes, lows), LEV, COND, [], load_config(), "futures")
    assert t.entry_time.endswith("09:25") and t.entry_px == 105      # signal on 09:20 close, fill 09:25 open
    assert t.exit_time.endswith("09:45") and t.exit_reason == "close_vs_ema"   # 09:40 close 99 < 100
    assert t.points == 98 - 105


def test_force_exit_and_no_trade_without_vwap_cross():
    t = en.simulate_day(_day([101] * 3 + [105] * 80), LEV, COND, [], load_config(), "futures")
    assert t.exit_reason == "force_exit" and t.exit_time.endswith("15:20")
    assert en.simulate_day(_day([101] * 60), LEV, COND, [], load_config(), "futures") is None


def test_target_filter_and_breakout_filter():
    lev = LEV.copy(); lev["R4"] = 110.0; lev["R1"] = 106.0
    closes = [103, 104, 105, 107, 108, 109, 111, 112]
    cfg = load_config()
    t = en.simulate_day(_day(closes), lev, COND, ["target_r4_mh3"], cfg, "futures")
    assert t.exit_reason == "target_r4_mh3" and t.exit_px == 110.0
    t2 = en.simulate_day(_day(closes), lev, COND, ["r1_pdh_break"], cfg, "futures")
    assert t2.entry_time.endswith("09:35")                            # first close > max(R1, PDH) is 09:30


# ── rules file ──────────────────────────────────────────────────────────────

def _c001():
    return json.loads((PKG / "rules.json").read_text())["conditions"][0]


def test_rules_file_valid_and_c001_open_location_recorded():
    c = _c001()
    assert rl.validate(c) == []
    assert c["setup"]["open_vs_daily_h3"] == "below" and c["setup"]["open_vs_weekly_h3"] == "below"


def test_duplicate_conflict_and_new(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text((PKG / "rules.json").read_text())
    before = [c["id"] for c in json.loads(path.read_text())["conditions"]]
    dup = {**_c001(), "id": "T002", "date": "2026-10-20"}
    assert rl.add_condition(dup, path)["status"] == "duplicate"
    conf = {**_c001(), "id": "T003", "outcome_class": "loss"}
    res = rl.add_condition(conf, path)
    assert res["status"] == "conflict" and res["with"] == "C001"
    new = {**_c001(), "id": "T004", "setup": {**_c001()["setup"], "cpr_width": "wide"}}
    assert rl.add_condition(new, path)["status"] == "new"
    ids = [c["id"] for c in json.loads(path.read_text())["conditions"]]
    assert ids == before + ["T003", "T004"]                                # duplicate not written
    bad = {**_c001(), "id": "T005", "setup": {**_c001()["setup"], "cpr_width": "tiny"}}
    assert rl.add_condition(bad, path)["status"] == "invalid"
    assert rl.add_condition({**_c001(), "id": "C002"}, path)["status"] == "id_taken"


# ── metrics / costs ─────────────────────────────────────────────────────────

def test_metrics_and_bootstrap():
    m = metrics([10, -5, 20, -5])
    assert m["trades"] == 4 and m["win_rate"] == 50.0 and m["profit_factor"] == 3.0
    assert m["expectancy"] == 5.0 and m["max_drawdown"] == -5.0
    b = _bootstrap([1.0] * 50)
    assert b["ci95"] == [1.0, 1.0] and b["p_mean_le_0"] == 0.0


def test_option_costs_make_flat_trade_negative():
    net, friction = option_net_pnl(100.0, 100.0, pd.Timestamp("2026-10-15").date(), load_config())
    assert net < 0 and friction == pytest.approx(-net)
    assert friction > 2 * 65                                             # at least the 1pt/side slippage


# ── isolation from the live system ──────────────────────────────────────────

def test_live_flag_off_and_not_registered():
    cfg = json.loads((PKG / "config.json").read_text())
    assert cfg["enabled_live"] is False and cfg["research_only"] is True
    assert is_enabled_live() is False
    for live in ("signal_engine.py", "live_signal_engine.py", "main_autonomous.py"):
        src = (REPO / live).read_text()
        assert "cpr_vwap_ema_nifty" not in src, live


def test_package_imports_existing_code_read_only():
    """Only these existing modules may be imported, and only for read-only calls."""
    allowed = {"angel", "request_governor", "nse_cost_model", "option_intraday_pricer", "dotenv", "alerts"}
    stdlib_or_third = {"argparse", "copy", "csv", "hashlib", "json", "logging", "os", "sqlite3", "sys", "time", "shutil", "datetime",
                       "pathlib", "typing", "functools", "dataclasses", "__future__", "numpy", "pandas", "scipy",
                       "matplotlib"}
    for py in PKG.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module.split(".")[0]]
            else:
                continue
            for n in names:
                assert n in allowed | stdlib_or_third, f"{py.name} imports {n}"


# ── field tests / pre-registered rule / forward evaluation ──────────────────

def test_field_tests_are_one_field_subsets_of_base_variant():
    from cpr_vwap_ema_nifty.backtest import _field_tests
    cfg = load_config()
    assert len(cfg["field_tests"]["tests"]) == 5
    n = 40
    T = pd.DataFrame({
        "variant": ["+r1_pdh_break"] * n, "vwap_set": ["futures"] * n,
        "points": np.linspace(-20, 30, n), "B_net": np.linspace(-500, 900, n), "C_net": np.nan,
        "hold_min": 60.0, "mfe": 10.0, "mae": 5.0, "period": ["IS"] * 20 + ["OOS"] * 20,
        "year": [2023] * 20 + [2025] * 20, "date": "2025-01-01",
        "cpr_width": ["narrow", "wide"] * 20, "cpr_vs_prior_day": "higher", "cpr_vs_weekly": "above",
        "cpr_vs_monthly": "below", "open_vs_cpr": "above", "open_vs_daily_h3": "below",
        "open_vs_weekly_h3": ["below"] * 30 + ["above"] * 10,
    })
    out = _field_tests(T, _c001(), cfg)
    assert out["tests_run"] == 5
    assert out["reference_no_field_filter"]["A_points"]["trades"] == 40
    assert out["cpr_width"]["A_points"]["trades"] == 20                 # narrow only
    assert out["cpr_vs_prior_day"]["A_points"]["trades"] == 0           # C001 = overlapping_higher
    assert out["cpr_vs_weekly"]["A_points"]["trades"] == 40
    assert out["open_location"]["A_points"]["trades"] == 30             # all three open fields together
    assert set(out["cpr_width"]["by_year"]) == {2023, 2025}


def test_r001_approved_frozen_and_bar_exactly_as_approved():
    r = json.loads((PKG / "rules.json").read_text())["preregistered"][0]
    assert r["id"] == "R001" and r["mode"] == "paper_only"
    assert r["status"] == "approved" and r["approved_on"] == "2026-10-16" and r["date"] == "2026-10-16"
    assert rl.frozen_ok(r)
    assert r["engine_mapping"]["filters"] == ["r1_pdh_break"]
    b = r["forward_bar"]
    assert b["evaluation_trades"] == 150 and b["kill_check_at_trades"] == 40
    assert b["kill_if_any"] == {"pf_points_below": 0.9, "real_option_net_below_rs": -20000}
    assert b["fail_immediately_if"] == {"real_option_drawdown_beyond_rs": 40000}
    assert b["pass_if_all_at_150"] == {"pf_points_min": 1.15, "t_stat_points_min": 1.65, "real_option_net_min_rs": 0,
                                       "real_option_drawdown_max_rs": 40000, "real_price_coverage_min_pct": 80}


def test_modified_frozen_rule_is_refused(monkeypatch):
    import copy
    from cpr_vwap_ema_nifty import forward as fw
    doc = copy.deepcopy(rl.load())
    doc["preregistered"][0]["forward_bar"]["pass_if_all_at_150"]["pf_points_min"] = 1.0
    monkeypatch.setattr(rl, "load", lambda path=None: doc)
    assert fw.evaluate("R001", fetch=False)["status"] == "frozen_rule_modified"


def test_unapproved_rule_counts_nothing(monkeypatch):
    import copy
    from cpr_vwap_ema_nifty import forward as fw
    doc = copy.deepcopy(rl.load())
    doc["preregistered"][0].update(status="proposed", approved_on=None)
    monkeypatch.setattr(rl, "load", lambda path=None: doc)
    assert fw.evaluate("R001", fetch=False)["status"] == "not_approved"


def test_rules_json_obeys_storage_rule():
    doc = json.loads((PKG / "rules.json").read_text())
    for c in doc["conditions"]:
        assert rl.validate(c) == [], c["id"]
    for item in doc["conditions"] + doc["preregistered"] + doc["hypotheses"]:
        assert rl._iso_date_ok(item["date"]), item["id"]
    h = doc["hypotheses"][0]
    assert h["id"] == "H001" and h["counts_toward_test_total"] is False and h["forward_trades_counted"] is False
    raw = (PKG / "rules.json").read_text()
    for banned in ("outcome\"", "observations", "result_at_logging", "simulated_from_backtest_trades", "22671", "23056"):
        assert banned not in raw, banned


def test_storage_rule_rejects_prices_and_unknown_fields():
    bad = {**_c001(), "id": "C009", "entry_price": 22724.85}
    assert any("entry_price" in e for e in rl.validate(bad))
    bad2 = {**_c001(), "id": "C010", "setup": {**_c001()["setup"]}, "exit": {**_c001()["exit"], "level": 23020.7}}
    assert any("exit.level" in e for e in rl.validate(bad2))
    bad3 = {**_c001(), "id": "C011", "notes": "crossed R1 22734"}
    assert any("notes" in e for e in rl.validate(bad3))


def test_test_ledger_counts_distinct_tests_only(tmp_path, monkeypatch):
    from cpr_vwap_ema_nifty import backtest as bt
    monkeypatch.setattr(bt, "LEDGER_FILE", tmp_path / "ledger.json")
    summary = {"conditions": {"C001": {
        "variants": {"base|futures": {}, "base|with_missing": {}, "+r1_pdh_break|futures": {}},
        "setup_sensitivity": {"drop cpr_width": {}},
        "field_tests": {"base_variant": "+r1_pdh_break", "tests_run": 2, "reference_no_field_filter": {},
                        "cpr_width": {}, "open_location": {}}}}}
    first = bt.update_ledger(summary, "2026-10-16T10:00:00")
    assert first["total"] == 5 and len(first["new_this_run"]) == 5
    again = bt.update_ledger(summary, "2026-10-17T10:00:00")
    assert again["total"] == 5 and again["new_this_run"] == []


def test_snapshot_coverage_flags_risk(tmp_path, monkeypatch):
    import sqlite3
    from datetime import date
    from cpr_vwap_ema_nifty import options_layers as ol
    from cpr_vwap_ema_nifty.forward import snapshot_coverage
    db = tmp_path / "snap.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE option_chain_snapshots (snapshot_time TEXT, underlying TEXT, ok INTEGER)")
    full = [(f"2026-10-19T{9 + i // 12:02d}:{(i % 12) * 5:02d}:00+0530", "NIFTY", 1) for i in range(3, 75)]
    part = [(f"2026-10-20T10:{m:02d}:00+0530", "NIFTY", 1) for m in range(0, 50, 5)]
    con.executemany("INSERT INTO option_chain_snapshots VALUES (?,?,?)", full + part)
    con.commit(); con.close()
    monkeypatch.setattr(ol, "SNAPSHOT_DB", db)
    monkeypatch.setattr(ol, "STANDALONE_SNAPSHOT_DB", tmp_path / "none.db")
    sessions = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    cov = snapshot_coverage(sessions, load_config(), None, 80)
    assert cov["full"] == 1 and cov["partial_days"] == ["2026-10-20"] and cov["no_snapshot_days"] == ["2026-10-21"]
    assert cov["at_risk"] is True and "AT RISK" in cov["line"]
    ok = snapshot_coverage(sessions[:1], load_config(), 100.0, 80)
    assert ok["at_risk"] is False and ok["line"].endswith("OK")


def _fwd(points, c_net):
    return pd.DataFrame({"points": points, "C_net": c_net,
                         "entry_time": pd.date_range("2026-11-02 09:30", periods=len(points), freq="D").astype(str)})


BAR = json.loads((PKG / "rules.json").read_text())["preregistered"][0]["forward_bar"]


def test_forward_verdict_in_progress_kill_fail_pass():
    from cpr_vwap_ema_nifty.forward import verdict
    assert verdict(_fwd([5.0, -3.0], [300.0, -200.0]), BAR)["verdict"] == "IN_PROGRESS"
    bad = _fwd([-10.0] * 30 + [5.0] * 10, [-100.0] * 40)                # PF 0.17 at the 40-trade check
    v = verdict(bad, BAR)
    assert v["verdict"] == "FAIL" and "kill" in v["reason"]
    dd = _fwd([1.0] * 5, [-9000.0] * 5)                                  # real-option drawdown 45k
    assert verdict(dd, BAR)["verdict"] == "FAIL"
    rng = np.random.default_rng(0)
    good_pts = list(rng.normal(12, 40, 150))
    good = _fwd(good_pts, [p * 40 for p in good_pts])
    assert verdict(good, BAR)["verdict"] in {"PASS", "FAIL"}
    assert set(verdict(good, BAR).get("checks", {})) == {"pf_points", "t_stat_points", "real_option_net",
                                                           "real_option_drawdown", "real_price_coverage"}


def test_near_month_plan_picks_smallest_expiry_per_day():
    from datetime import date
    from cpr_vwap_ema_nifty.data import near_month_plan
    contracts = [(date(2026, 10, 27), "48704", "OCT"), (date(2026, 11, 24), "61471", "NOV"),
                 (date(2026, 12, 29), "58875", "DEC")]
    plan = near_month_plan(date(2026, 10, 20), date(2026, 12, 5), contracts)
    assert [(p[0].isoformat(), p[1].isoformat(), p[3]) for p in plan] == [
        ("2026-10-20", "2026-10-27", "OCT"), ("2026-10-28", "2026-11-24", "NOV"), ("2026-11-25", "2026-12-05", "DEC")]



def test_forward_report_never_writes_rules_and_notify_text(tmp_path, monkeypatch):
    from cpr_vwap_ema_nifty import forward as fw
    rules_file = PKG / "rules.json"
    before = rules_file.read_bytes()
    monkeypatch.setattr(fw, "FORWARD_DIR", tmp_path)
    st = fw.evaluate("R001", fetch=False)
    assert rules_file.read_bytes() == before
    assert "snapshot_coverage" in st and st["tests_on_backtest_dataset"] >= 20
    sent = []

    class FakeAM:
        def __init__(self, **kw):
            pass

        def send(self, text, **kw):
            sent.append(text)
            return True

    import alerts
    monkeypatch.setattr(alerts, "AlertManager", FakeAM)
    assert fw.notify(st) is True
    assert "R001 forward (paper)" in sent[0] and "Snapshot coverage" in sent[0] and "Tests run" in sent[0]


# ── priority-focus run (2026-10-16): golden pivot zone, zones, short side ────

def test_golden_pivot_zone_definition():
    cpr_ = {"BC": 100.0, "TC": 101.0, "P": 100.5}
    assert lv.golden_pivot_zone(cpr_, {"L3": 100.5, "H3": 103.0}) == "bullish"
    assert lv.golden_pivot_zone(cpr_, {"L3": 98.0, "H3": 100.2}) == "bearish"
    assert lv.golden_pivot_zone(cpr_, {"L3": 98.0, "H3": 103.0}) == "none"
    assert lv.golden_pivot_zone(cpr_, {"L3": 100.0, "H3": 103.0}) == "bullish"      # boundary counts as inside
    # 15-Oct-2026 from the 14-Oct HLC: L3 below the CPR, H3 above it -> none
    assert lv.golden_pivot_zone(lv.cpr(H, L, C), lv.camarilla(H, L, C)) == "none"


def test_golden_pivot_zone_never_both():
    rng = np.random.default_rng(5)
    for _ in range(2000):
        lo = rng.uniform(100, 200); hi = lo + rng.uniform(0.1, 20); c = rng.uniform(lo, hi)
        cp, cam = lv.cpr(hi, lo, c), lv.camarilla(hi, lo, c)
        assert not (cp["BC"] <= cam["L3"] <= cp["TC"] and cp["BC"] <= cam["H3"] <= cp["TC"])


def test_zone_definitions():
    from cpr_vwap_ema_nifty.hypothesis_tests import in_camarilla_sideways_zone, primary_positions, zone_position
    assert zone_position(105, 100, 110) == "inside" and zone_position(105, 110, 100) == "inside"   # band, either order
    assert zone_position(111, 100, 110) == "above" and zone_position(99, 100, 110) == "below"
    lev = pd.Series({"BC": 100.0, "TC": 101.0, "PDH": 110.0, "R1": 112.0, "PDL": 90.0, "S1": 88.0,
                     "H3": 105.0, "H4": 108.0, "L3": 95.0, "L4": 92.0})
    bar = pd.Series({"close": 111.0, "fut_close": 111.5, "fut_vwap": 111.0, "ema200_1m": 112.0, "ema200_5m": 100.0})
    pos = primary_positions(bar, lev)
    assert pos == {"vwap": "above", "ema200_1m": "below", "ema200_5m": "above", "cpr": "above",
                   "pdh_r1_zone": "inside", "pdl_s1_zone": "above"}
    assert in_camarilla_sideways_zone(106.0, lev) == {"inside_h3_h4": True, "inside_l3_l4": False, "inside_either": True}
    assert in_camarilla_sideways_zone(93.0, lev)["inside_l3_l4"] is True
    assert in_camarilla_sideways_zone(100.5, lev)["inside_either"] is False


def test_short_side_mechanics_mirror_r001():
    closes = [99, 97, 95, 94, 93, 101, 102]
    df = _day(closes)
    df["ema20_5m"] = 100.0
    for k in ("ema50_5m", "ema200_5m", "ema200_1m"):
        df[k] = 150.0
    df["fut_vwap"] = 98.0
    lev = pd.Series({"R1": 1e9, "PDH": 1e9, "PWH": 1e9, "R4": 1e9, "MH3": 1e9, "S1": 96.0, "PDL": 96.5,
                     "PWL": 0, "S4": 0, "ML3": 0})
    cond = {"bias": "SHORT", "context": {"above_vwap": True, "above_ema20_5m": True, "above_ema50_5m": True,
                                         "above_ema200_5m": True, "above_ema200_1m": True},
            "exit": {"type": "close_vs_ema", "timeframe": "5m", "period": 20}}
    t = en.simulate_day(df, lev, cond, ["r1_pdh_break"], load_config(), "futures")
    assert t.side == "SHORT"
    assert t.entry_time.endswith("09:30") and t.entry_px == 94           # 09:25 close 95 < min(S1, PDL)=96 -> 09:30 open 94
    assert t.exit_time.endswith("09:45") and t.exit_reason == "close_vs_ema"   # 09:40 close 101 > EMA20 100
    assert t.points == 94 - 102                                           # short P&L = entry - exit


def test_short_trades_price_puts(monkeypatch):
    from datetime import datetime
    from cpr_vwap_ema_nifty import backtest as bt
    seen = {}

    class FakeMod:
        con = True

        def expiry_for(self, d):
            return "2026-10-20"

        def price(self, d, k, opt, when, spot, expiry=None):
            seen["opt"] = opt
            return 100.0 if when.hour == 9 else 130.0

    t = en.Trade(date="2026-10-19", side="SHORT", entry_time="2026-10-19 09:30", entry_px=25010.0,
                 exit_time="2026-10-19 11:00", exit_px=24950.0, exit_reason="close_vs_ema", points=60.0,
                 mfe=70.0, mae=5.0, hold_min=90.0, vwap_source="futures_vwap")
    out = bt._option_layers(t, load_config(), FakeMod(), None, set())
    assert seen["opt"] == "PE" and out["opt_type"] == "PE" and out["strike"] == 25000.0
    assert out["B_net"] > 0                                               # put gained 30 pts premium


def test_r001_fingerprint_and_bar_unchanged():
    r = json.loads((PKG / "rules.json").read_text())["preregistered"][0]
    assert r["fingerprint"] == "3c3daa6c033b15b43c0952d39e3f93544bb1afe0ca3c911d1ada5a7df5809ddf"
    assert rl.fingerprint(r) == r["fingerprint"] and rl.frozen_ok(r)


def test_storage_rule_optional_fields_and_no_results_in_rules():
    ok = {**_c001(), "id": "T012", "setup": {**_c001()["setup"], "golden_pivot_zone": "none",
                                             "weekly_cpr_vs_monthly": "inside", "daily_cpr_inside_weekly": True}}
    assert rl.validate(ok) == []                                          # approved optional fields
    bad = {**_c001(), "id": "T013", "setup": {**_c001()["setup"], "golden_pivot_zone": "maybe"}}
    assert any("golden_pivot_zone" in e for e in rl.validate(bad))
    bad_bool = {**_c001(), "id": "T014", "setup": {**_c001()["setup"], "daily_cpr_inside_weekly": "yes"}}
    assert any("daily_cpr_inside_weekly" in e for e in rl.validate(bad_bool))
    unknown = {**_c001(), "id": "T015", "setup": {**_c001()["setup"], "prior_day_cpr_width": "narrow"}}
    assert any("prior_day_cpr_width unknown" in e for e in rl.validate(unknown))
    bad2 = {**_c001(), "id": "T016", "verification": {"open": 22671.3}}
    assert any("verification" in e for e in rl.validate(bad2))
    doc = json.loads((PKG / "rules.json").read_text())
    for c in doc["conditions"]:
        assert rl.validate(c) == [], c["id"]
    raw = (PKG / "rules.json").read_text()
    for banned in ("H-A", "priority_focus", "\"points\":", "B_net", "entry_px", "exit_px"):   # H-D is now an R002 entry source (rule field)
        assert banned not in raw, banned


def test_h_c_pairs_strict_gap_and_no_span_over_missing_sessions():
    from cpr_vwap_ema_nifty.hypothesis_tests import h_c, late_close_pairs

    def day(d, open_, last_close, ema):
        idx = pd.date_range(f"{d} 09:15", f"{d} 15:25", freq="5min")
        df = pd.DataFrame({"open": open_, "high": open_ + 1, "low": open_ - 1, "close": open_, "ema200_1m": ema},
                          index=idx)
        df.iloc[-1, df.columns.get_loc("close")] = last_close
        return df

    by_day = {pd.Timestamp("2026-10-12"): day("2026-10-12", 100, 105, 100),   # final close above EMA
              pd.Timestamp("2026-10-13"): day("2026-10-13", 106, 95, 100),    # next open 106 > 105: gap up; close below
              pd.Timestamp("2026-10-14"): day("2026-10-14", 95, 99, 98),      # next open 95 == 95: neither
              pd.Timestamp("2026-10-16"): day("2026-10-16", 120, 121, 100)}   # 15th missing -> no 14->16 pair
    prep = {"by_day": by_day, "days": list(by_day),
            "spot_source": {"2026-10-12": "angel", "2026-10-13": "angel", "2026-10-14": "angel",
                            "2026-10-15": "insufficient", "2026-10-16": "angel"}}
    P = late_close_pairs(prep, "2026-10-14")
    assert [d.strftime("%m-%d") for d in P.date] == ["10-12", "10-13"]
    assert P.iloc[0].above and P.iloc[0].gap_up
    assert P.iloc[1].below and not P.iloc[1].gap_up and not P.iloc[1].gap_down   # strictly above / below only
    r = h_c(prep, {"out_of_sample_start": "2026-10-14"})
    assert r["all"]["final_close_above_ema"] == {"sessions": 1, "gap_up": 1, "hit_rate_pct": 100.0,
                                                 "p_vs_baseline": 0.5}   # baseline 1 of 2 pairs gap up
    assert r["equal_open_pairs"] == 1
