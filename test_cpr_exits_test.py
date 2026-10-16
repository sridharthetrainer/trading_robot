"""Exit test E1-E5 (+ references) on unchanged entries: mechanics for longs and
shorts, ATR at entry without lookahead, E5 level selection, R001 unchanged,
R002 only proposed."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cpr_vwap_ema_nifty import exits_test as xt
from cpr_vwap_ema_nifty import rules as rl
from cpr_vwap_ema_nifty.backtest import load_config

PKG = Path(__file__).resolve().parent / "cpr_vwap_ema_nifty"


def _day(rows, **cols):
    idx = pd.date_range("2026-03-02 09:15", periods=len(rows), freq="5min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).astype(float)
    for k, v in {"ema20_5m": 0.0, "ema50_5m": 0.0, "ema200_5m": 0.0, "ema200_1m": 0.0,
                 "fut_vwap": 0.0, "fut_close": None}.items():
        df[k] = cols.get(k, v) if k != "fut_close" else cols.get(k, df["close"])
    return df


LEV = pd.Series({"BC": 0.0, "TC": 0.0, "PDH": 0.0, "R1": 0.0, "PDL": 0.0, "S1": 0.0})


@pytest.mark.parametrize("code,col", [("E1", "fut_vwap"), ("E2", "ema200_1m"), ("E3", "ema200_5m"),
                                      ("REF20", "ema20_5m"), ("REF50", "ema50_5m")])
def test_close_across_exits_long_and_short(code, col):
    cfg = load_config()
    # long: level 100; closes 105, 104, 99 (across) -> exit next open
    long_day = _day([(105, 106, 104, 105), (105, 106, 104, 105), (105, 106, 103, 104), (104, 104, 98, 99),
                     (97, 98, 96, 97), (97, 98, 96, 97)], **{c: (100.0 if c == col else 0.0) for c in
                                                            ("fut_vwap", "ema200_1m", "ema200_5m", "ema20_5m", "ema50_5m")})
    if col != "fut_vwap":
        long_day["fut_vwap"] = 0.0
    t = xt.simulate_exit(long_day, LEV, 0, 1, code, 1.0, cfg)
    assert t.entry_px == 105 and t.exit_time.endswith("09:35") and t.exit_px == 97 and t.points == -8
    # short mirror: level 100; closes 95, 96, 101 (across) -> exit next open
    short_day = _day([(95, 96, 94, 95), (95, 96, 94, 95), (95, 97, 94, 96), (96, 102, 96, 101),
                      (103, 104, 102, 103), (103, 104, 102, 103)], **{c: (100.0 if c == col else 999.0) for c in
                                                                     ("fut_vwap", "ema200_1m", "ema200_5m", "ema20_5m", "ema50_5m")})
    if col != "fut_vwap":
        short_day["fut_vwap"] = 999.0
    t = xt.simulate_exit(short_day, LEV, 0, -1, code, 1.0, cfg)
    assert t.side == "SHORT" and t.entry_px == 95 and t.exit_time.endswith("09:35") and t.exit_px == 103
    assert t.points == 95 - 103


def test_e4_trailing_stop_long_and_short():
    cfg = load_config()
    # long, ATR 2 -> stop 6 below best. entry 100; highs 104, 110 -> stop 104; low 103 hits at 104
    d = _day([(100, 100, 100, 100), (100, 104, 99, 103), (103, 110, 105, 109), (109, 109, 103, 104),
              (104, 104, 104, 104)], fut_vwap=0.0)
    t = xt.simulate_exit(d, LEV, 0, 1, "E4", 2.0, cfg)
    assert t.exit_reason == "atr_trail" and t.exit_px == 104.0 and t.points == 4.0
    # the candle that makes a new best is checked against the OLD stop first (no same-candle trail)
    d2 = _day([(100, 100, 100, 100), (100, 120, 95, 118), (118, 118, 117, 117)], fut_vwap=0.0)
    t2 = xt.simulate_exit(d2, LEV, 0, 1, "E4", 2.0, cfg)
    # entry 100, stop 94: candle 2 low 95 does not hit the OLD stop even though its high (120) would
    # have trailed the stop to 114; candle 3 low 117 > 114 -> no exit, held to the end of data
    assert t2.exit_reason == "data_end"
    # short, ATR 2: entry 100; lows 96, 90 -> stop 96; high 97 hits at 96
    d3 = _day([(100, 100, 100, 100), (100, 101, 96, 97), (97, 95, 90, 91), (91, 97, 91, 96),
               (96, 96, 96, 96)], fut_vwap=999.0)
    t3 = xt.simulate_exit(d3, LEV, 0, -1, "E4", 2.0, cfg)
    assert t3.exit_reason == "atr_trail" and t3.exit_px == 96.0 and t3.points == 4.0


def test_atr_value_at_entry_has_no_lookahead():
    idx = pd.date_range("2026-03-02 09:15", periods=40, freq="5min")
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 40))
    f = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c}, index=idx)
    a1 = xt.wilder_atr(f)
    f2 = f.copy()
    f2.iloc[25:, :] += 50                                               # change the future
    f2.iloc[25:, f2.columns.get_loc("high")] += 30
    a2 = xt.wilder_atr(f2)
    assert a1.iloc[24] == pytest.approx(a2.iloc[24])
    assert a1.iloc[25] != pytest.approx(a2.iloc[25])


def test_e5_level_selection_and_exit():
    lev = pd.Series({"BC": 98.0, "TC": 103.0, "PDH": 108.0, "R1": 112.0, "PDL": 90.0, "S1": 88.0})
    bar = pd.Series({"close": 101.0, "fut_close": 151.0, "fut_vwap": 150.0})    # spot VWAP equiv = 100
    assert xt.e5_target(bar, lev, 101.0, 1) == 103.0                    # nearest above: TC
    assert xt.e5_target(bar, lev, 101.0, -1) == 100.0                   # nearest below: VWAP (spot equiv)
    assert xt.e5_target(bar, lev, 200.0, 1) is None                     # nothing beyond -> None
    cfg = load_config()
    d = _day([(101, 101.5, 100.5, 101), (101, 102, 100.5, 101.5), (101.5, 104, 101, 103.5), (103, 104, 102, 103)],
             fut_vwap=0.0)
    d["fut_close"] = d["close"]
    t = xt.simulate_exit(d, lev, 0, 1, "E5", 1.0, cfg)
    assert t.exit_reason == "e5_target" and t.exit_px == 103.0          # target TC touched intrabar at 09:25


def test_r001_unchanged_and_r002_only_proposed():
    doc = json.loads((PKG / "rules.json").read_text())
    r1, r2 = doc["preregistered"][0], doc["preregistered"][1]
    assert r1["id"] == "R001" and rl.frozen_ok(r1)
    assert r1["fingerprint"] == "3c3daa6c033b15b43c0952d39e3f93544bb1afe0ca3c911d1ada5a7df5809ddf"
    assert r1["engine_mapping"]["exit"] == {"type": "close_vs_ema", "timeframe": "5m", "period": 20}
    assert r2["id"] == "R002" and r2["status"] == "proposed" and r2["approved_on"] is None and r2["frozen"] is False
    assert r2["definition"]["bias"] == "SHORT" and r2["definition"]["max_trades_per_session"] == 1
    from cpr_vwap_ema_nifty.forward import evaluate
    assert evaluate("R002", fetch=False)["status"] == "not_approved"
    raw = (PKG / "rules.json").read_text()
    for banned in ("pass_if_edge_real", "trades_per_month", "B_total", "day_bootstrap"):
        assert banned not in raw, banned
