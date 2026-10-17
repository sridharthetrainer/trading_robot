"""Confirmation-candle test: rule mechanics for longs/shorts, no-trade when not
confirmed, entry two candles after the signal, ledger tests 41-42, R001/R002 untouched."""
import json
from pathlib import Path

import pandas as pd

from cpr_vwap_ema_nifty import confirmation_test as ct
from cpr_vwap_ema_nifty import exits_test as xt
from cpr_vwap_ema_nifty import rules as rl
from cpr_vwap_ema_nifty.backtest import load_config

PKG = Path(__file__).resolve().parent / "cpr_vwap_ema_nifty"


def _day(closes, vwap, ema1m, ema20=0.0):
    idx = pd.date_range("2026-03-02 09:15", periods=len(closes), freq="5min")
    df = pd.DataFrame({"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                       "close": closes}, index=idx).astype(float)
    df["fut_close"] = df["close"]
    df["fut_vwap"] = vwap
    df["ema200_1m"] = ema1m
    df["ema20_5m"] = ema20
    df["ema50_5m"] = 0.0
    df["ema200_5m"] = 0.0
    return df


def test_confirmed_long_and_short():
    d = _day([105, 104, 103, 99, 98], vwap=100.0, ema1m=101.0)
    assert ct.confirmed(d, 0, 1) is True                  # candle 1 closes 104 > VWAP 100 and > EMA 101
    assert ct.confirmed(d, 2, 1) is False                 # candle 3 closes 99 < VWAP
    s = _day([95, 96, 97, 102, 103], vwap=100.0, ema1m=96.5)
    assert ct.confirmed(s, 0, -1) is True                 # 96 < 100 and < 96.5
    assert ct.confirmed(s, 1, -1) is False                # candle 2 closes 97: below VWAP but NOT below EMA 96.5 -> no
    assert ct.confirmed(s, 3, -1) is False                # no candle s+2 to enter on


def test_confirmed_entry_is_two_candles_after_signal():
    cfg = load_config()
    d = _day([105, 106, 107, 108, 109, 95, 94], vwap=100.0, ema1m=101.0, ema20=100.0)
    assert ct.confirmed(d, 0, 1)
    t_un = xt.simulate_exit(d, pd.Series(dtype=float), 0, 1, "REF20", 0.0, cfg)
    t_cf = xt.simulate_exit(d, pd.Series(dtype=float), 1, 1, "REF20", 0.0, cfg)
    assert t_un.entry_time.endswith("09:20") and t_cf.entry_time.endswith("09:25")
    assert t_un.exit_time == t_cf.exit_time               # same exit rule, same exit candle


def test_ledger_has_42_and_rules_untouched():
    led = json.loads((PKG / "test_ledger.json").read_text())
    assert set(ct.TESTS) <= set(led["tests"]) and len(led["tests"]) == 42
    doc = json.loads((PKG / "rules.json").read_text())
    r1 = next(r for r in doc["preregistered"] if r["id"] == "R001")
    r2 = next(r for r in doc["preregistered"] if r["id"] == "R002")
    assert rl.frozen_ok(r1) and r1["fingerprint"] == "3c3daa6c033b15b43c0952d39e3f93544bb1afe0ca3c911d1ada5a7df5809ddf"
    assert r2["status"] == "proposed" and r2["approved_on"] is None
    assert not any(r["id"] == "R003" for r in doc["preregistered"])
