"""
test_session_modules.py — regression tests for the modules built/changed in the
2026-06-20 hardening session. These lock in behaviours that were verified inline:
experiment registry dedup, capital-sim DB resilience, strategy-matrix idempotency
+ cap, buy-&-hold benchmark edge cases, macro sector/bias logic, dashboard
degradation. All isolated (temp files / temp cwd) — no network, no live DBs.
"""
import sqlite3

import pandas as pd
import pytest


# ── experiment_registry ───────────────────────────────────────────────────────
def test_experiment_registry_dedup(tmp_path):
    import experiment_registry as er
    db = str(tmp_path / "exp.db")

    class R:
        strategy = "breakout"; symbol = "NIFTY"; best_params = {"d": 15, "a": 1.5}
        n_trials = 360; dev_avg_sharpe = 0.4; holdout_sharpe = None
        deflated_sharpe = 0.0; beats_benchmark = False; verdict = "FAIL"

    h1 = er.log_result(R(), timeframe="5m", db_path=db)
    h2 = er.log_result(R(), timeframe="5m", db_path=db)  # same config
    assert h1 == h2
    seen, rec = er.already_tested("breakout", "NIFTY", "5m", {"a": 1.5, "d": 15}, db_path=db)
    assert seen and rec["run_count"] == 2 and rec["verdict"] == "FAIL"
    assert len(er.list_experiments(db)) == 1  # no duplicate row


# ── capital_simulation ────────────────────────────────────────────────────────
def test_capital_sim_missing_db_is_graceful():
    import capital_simulation as cs
    assert cs.load_trade_returns(db_path="/tmp/_nope_does_not_exist.db") == []


def test_capital_sim_basic_math():
    import capital_simulation as cs
    # all-winning returns, no costs -> equity grows; ruin flag false
    res = cs.simulate([0.1, 0.1, 0.1], capital=100000, risk_frac=0.1,
                      cost_bps=0, brokerage=0)
    assert res["final_equity"] > 100000 and not res["ruined"]
    # heavy losses -> ruin
    res2 = cs.simulate([-0.9] * 50, capital=50000, risk_frac=0.5,
                       cost_bps=20, brokerage=40)
    assert res2["ruined"]


# ── strategy_performance_matrix (idempotency + cap) ───────────────────────────
def test_matrix_idempotent_refresh(tmp_path):
    import strategy_performance_matrix as spm
    m = spm.StrategyPerformanceMatrix(matrix_file=str(tmp_path / "m.json"))
    m.record_trade(strategy="trend", pnl=1.0, regime="TREND", src="live", autosave=False)

    def eod_pass():
        m.purge_source("eod")
        for i in range(50):
            m.record_trade(strategy="trend", pnl=1.0 if i % 2 else -1.0,
                           regime="TREND", src="eod", autosave=False)

    def count(src=None):
        return sum(1 for c in m._data.values() for v in c.values() for t in v
                   if src is None or t.get("src", "live") == src)

    eod_pass(); eod_pass(); eod_pass()  # re-running must not double-count
    assert count("eod") == 50 and count("live") == 1 and count() == 51


def test_matrix_per_bucket_cap(tmp_path):
    import strategy_performance_matrix as spm
    m = spm.StrategyPerformanceMatrix(matrix_file=str(tmp_path / "m.json"))
    for _ in range(spm.StrategyPerformanceMatrix.MAX_PER_BUCKET + 120):
        m.record_trade(strategy="x", pnl=1.0, regime="TREND", src="eod", autosave=False)
    bucket = next(iter(m._data["x"].values()))
    assert len(bucket) == spm.StrategyPerformanceMatrix.MAX_PER_BUCKET


# ── validation_harness.buy_hold_sharpe ────────────────────────────────────────
def test_buy_hold_sharpe_edge_cases():
    from validation_harness import buy_hold_sharpe
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    rising = pd.DataFrame({"Close": [100 + i for i in range(40)]}, index=idx)
    assert buy_hold_sharpe(rising) > 0
    flat = pd.DataFrame({"Close": [100, 100, 100]})
    assert buy_hold_sharpe(flat) is None
    assert buy_hold_sharpe(pd.DataFrame({"x": [1, 2, 3]})) is None  # no Close col


# ── macro_global_profit_engine ────────────────────────────────────────────────
def test_macro_sector_map_rules():
    import macro_global_profit_engine as mg
    s = mg.sector_impact_map({"BRENT": 2.0, "USDINR": 0.4, "SP500": 0.8})
    assert "OMC" in s["negative_sectors"] and "IT" in s["positive_sectors"]


def test_macro_bias_labels():
    import macro_global_profit_engine as mg
    assert mg._bias_label(50, 18) == "BULLISH"
    assert mg._bias_label(-50, 18) == "BEARISH"
    assert mg._bias_label(5, 18) == "NO_TRADE_ZONE"
    assert mg._bias_label(50, 30) == "HIGH_RISK"


def test_macro_log_dedup(tmp_path):
    import macro_global_profit_engine as mg
    db = str(tmp_path / "macro.db")
    ctx = {"timestamp": "2026-06-20T16:00:00", "gift_change_pct": 0.45,
           "global_score": 62.0, "bias": "BULLISH", "us_vix": 16.2,
           "sectors": {"positive_sectors": ["IT"], "negative_sectors": ["Banks"]},
           "reasons": ["GIFT +0.45%"]}
    assert mg.log_sentiment(ctx, db_path=db) is True
    assert mg.log_sentiment(ctx, db_path=db) is False  # same-day dedup
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM macro_global_sentiment").fetchone()[0]
    assert n == 1


# ── daily_dashboard degradation ───────────────────────────────────────────────
def test_dashboard_handles_missing_everything(tmp_path, monkeypatch):
    import daily_dashboard as dd
    monkeypatch.chdir(tmp_path)  # no signal_log.db, no report jsons here
    report = dd.build_report()
    assert isinstance(report, str) and "(no " in report  # renders placeholders, no crash


# ── intraday_oi_logger (parser) ───────────────────────────────────────────────
def test_intraday_oi_extract_rows_windows_strikes():
    import intraday_oi_logger as iol
    data = [{"strikePrice": k, "expiryDate": "26-Jun-2026",
             "CE": {"openInterest": 1000, "lastPrice": 50, "impliedVolatility": 12},
             "PE": {"openInterest": 900, "lastPrice": 48, "impliedVolatility": 13}}
            for k in range(21000, 25001, 100)]
    raw = {"records": {"data": data, "underlyingValue": 23000,
                       "expiryDates": ["26-Jun-2026"]}}
    spot, exp, rows = iol.extract_rows(raw, window_pct=0.06)
    assert spot == 23000 and exp == "26-Jun-2026"
    assert rows and all(21620 <= r[0] <= 24380 for r in rows)
    assert iol.extract_rows({}, 0.06) == (0.0, "", [])  # empty raw is graceful


# ── trend_basket_research (#4c engine) ────────────────────────────────────────
def test_trend_engine_detects_trend_not_noise():
    import numpy as np
    import trend_basket_research as tb
    idx = pd.date_range("2020-01-01", periods=900, freq="B")
    up = pd.Series(np.cumprod(1 + np.r_[0, np.full(899, 0.0006)]) * 100, index=idx)
    rng = np.random.default_rng(0)
    rw = pd.Series(np.cumprod(1 + rng.normal(0, 0.01, 900)) * 100, index=idx)
    up_sh = tb._sharpe(tb.instrument_returns(up, 100))
    rw_sh = tb._sharpe(tb.instrument_returns(rw, 100))
    assert up_sh > 1.0                 # captures a real trend
    assert up_sh > rw_sh + 1.0         # trend clearly beats noise (robust to RW luck)
    assert tb.validate({})["status"] == "NO_DATA"                  # graceful on empty


# ── pruning (gate-off mechanism) ──────────────────────────────────────────────
def test_pruning_load_and_suggest(tmp_path):
    import json
    import pruning
    p = str(tmp_path / "pruned.json")
    open(p, "w").write(json.dumps({"strategies": ["scalping"], "modifiers": ["mtf_pivot_mod"]}))
    s, m = pruning.load_pruned(p)
    assert "scalping" in s and "mtf_pivot_mod" in m
    # missing file → empty (no env set in test) ; never raises
    s2, m2 = pruning.load_pruned(str(tmp_path / "nope.json"))
    assert isinstance(s2, set) and isinstance(m2, set)


# ── db_health ─────────────────────────────────────────────────────────────────
def test_db_health_good_and_bad(tmp_path):
    import db_health
    good = str(tmp_path / "good.db")
    c = sqlite3.connect(good); c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES(1)")
    c.commit(); c.close()
    r = db_health.check_db(good)
    assert r["ok"] and r["integrity"] == "ok" and r["tables"] == 1 and r["rows"] == 1
    bad = str(tmp_path / "bad.db")
    open(bad, "wb").write(b"this is not a sqlite database \x00\x01\x02")
    rb = db_health.check_db(bad)
    assert rb["ok"] is False                      # corrupt detected, no raise


# ── core invariants (the safety contract) ─────────────────────────────────────
def test_paper_mode_is_hard_guaranteed():
    import config
    if config.PAPER_TRADING:
        assert config.ENABLE_REAL_TRADING is False   # PAPER must force real OFF


def test_core_engines_import_and_registry_nonempty():
    import importlib
    for m in ("signal_engine", "live_signal_engine", "trade_manager",
              "option_chain_fetcher", "post_market_ml"):
        importlib.import_module(m)                    # no import-time errors
    import signal_engine
    assert len(signal_engine.STRATEGIES) > 10        # registry intact


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
