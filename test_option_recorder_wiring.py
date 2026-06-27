"""Integration test: prove the data-integrity helpers are actually WIRED into
record_option_chain_snapshot end-to-end (not just defined + unit-tested):
  - _is_live_source gates `ok` (a non-live/stale source must not be ok=1)
  - persist_multistrike_signals runs on a live snapshot (per-strike flow accrues)
"""
import sqlite3

import pandas as pd
import pytest

import option_chain_fetcher
import option_chain_recorder as rec


class _FakeResult:
    def __init__(self, df, spot=25000.0):
        self.summary = {"pcr_oi": 1.1, "pcr_change_oi": 1.0, "max_pain": 25000.0}
        self.dataframe = df
        self.spot = spot
        self.expiry = "2026-07-30"
        self.atm_strike = 25000.0


def _make_fake_fetcher(source, ce_oi):
    df = pd.DataFrame([
        {"strikePrice": 25000, "CE_lastPrice": 100.0, "CE_openInterest": ce_oi,
         "CE_totalTradedVolume": 1500, "PE_lastPrice": 90.0,
         "PE_openInterest": 1000, "PE_totalTradedVolume": 1000},
    ])

    class _FakeFetcher:
        def __init__(self, underlying=None, **kw):
            self.last_source = source
        def fetch_and_analyze(self, *a, **k):
            return _FakeResult(df)

    return _FakeFetcher


def test_live_source_is_recorded_ok(monkeypatch, tmp_path):
    db = str(tmp_path / "oc.db")
    monkeypatch.setattr(option_chain_fetcher, "NSEOptionChainFetcher",
                        _make_fake_fetcher("nse_live", 1000))
    out = rec.record_option_chain_snapshot("NIFTY", db_path=db)
    assert out["ok"] is True
    with sqlite3.connect(db) as c:
        ok = c.execute("SELECT ok FROM option_chain_snapshots ORDER BY ts DESC LIMIT 1").fetchone()[0]
    assert ok == 1


def test_non_live_source_is_downgraded(monkeypatch, tmp_path):
    """_is_live_source wiring: a stale-cache chain has rows but must NOT be ok."""
    db = str(tmp_path / "oc.db")
    monkeypatch.setattr(option_chain_fetcher, "NSEOptionChainFetcher",
                        _make_fake_fetcher("cache", 1000))
    out = rec.record_option_chain_snapshot("NIFTY", db_path=db)
    assert out["ok"] is False
    assert "non_live_source" in out["reason"]


def test_multistrike_flow_persisted_on_live_snapshots(monkeypatch, tmp_path):
    """persist_multistrike_signals wiring: two live snapshots → strike signals."""
    db = str(tmp_path / "oc.db")
    monkeypatch.setattr(option_chain_fetcher, "NSEOptionChainFetcher",
                        _make_fake_fetcher("nse_live", 1000))
    rec.record_option_chain_snapshot("NIFTY", db_path=db)        # first (warmup)
    monkeypatch.setattr(option_chain_fetcher, "NSEOptionChainFetcher",
                        _make_fake_fetcher("nse_live", 1300))     # OI up → flow
    rec.record_option_chain_snapshot("NIFTY", db_path=db)        # second
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "option_strike_signals" in tables, "multistrike table never created → not wired"
        n = c.execute("SELECT COUNT(*) FROM option_strike_signals").fetchone()[0]
    assert n >= 1, "no per-strike flow signals persisted → wiring inert"
