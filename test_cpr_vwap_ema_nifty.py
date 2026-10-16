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
    dup = {**_c001(), "id": "C002", "date": "2026-10-20"}
    assert rl.add_condition(dup, path)["status"] == "duplicate"
    conf = {**_c001(), "id": "C003", "outcome": "Faded", "outcome_class": "loss"}
    res = rl.add_condition(conf, path)
    assert res["status"] == "conflict" and res["with"] == "C001"
    new = {**_c001(), "id": "C004", "setup": {**_c001()["setup"], "cpr_width": "wide"}}
    assert rl.add_condition(new, path)["status"] == "new"
    ids = [c["id"] for c in json.loads(path.read_text())["conditions"]]
    assert ids == ["C001", "C003", "C004"]                                # duplicate not written
    bad = {**_c001(), "id": "C005", "setup": {**_c001()["setup"], "cpr_width": "tiny"}}
    assert rl.add_condition(bad, path)["status"] == "invalid"


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
    allowed = {"angel", "request_governor", "nse_cost_model", "option_intraday_pricer", "dotenv"}
    stdlib_or_third = {"argparse", "json", "logging", "os", "sqlite3", "sys", "time", "shutil", "datetime",
                       "pathlib", "typing", "functools", "dataclasses", "__future__", "numpy", "pandas",
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
