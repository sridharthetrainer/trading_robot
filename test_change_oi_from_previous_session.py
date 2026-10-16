"""Angel chains have no change-in-OI; it is derived from the previous session's
last recorded snapshot (current OI - previous closing OI per strike)."""
import json
import sqlite3
import sys
import types
from datetime import date, timedelta

import option_chain_fetcher as ocf


def _snapshot_db(path, day, rows, expiry="2026-10-20", time_="15:30:01"):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE option_chain_snapshots (ts REAL, snapshot_time TEXT, underlying TEXT, spot REAL, "
                "expiry TEXT, atm_strike REAL, pcr_oi REAL, pcr_change_oi REAL, max_pain REAL, rows_json TEXT, "
                "summary_json TEXT, ok INTEGER, reason TEXT, source TEXT, is_live INTEGER, provider_request_id TEXT)")
    con.execute("INSERT INTO option_chain_snapshots (snapshot_time, underlying, expiry, rows_json, ok) VALUES (?,?,?,?,1)",
                (f"{day}T{time_}+0530", "NIFTY", expiry, json.dumps(rows)))
    con.commit()
    con.close()


def _fake_angel(monkeypatch, chain):
    class FakeEngine:
        def __init__(self, **kw):
            pass

        def scan_option_chain(self):
            return 25000.0, 25000.0, "2026-10-20", chain

    monkeypatch.setitem(sys.modules, "angel", types.SimpleNamespace(AngelOne=lambda **kw: object()))
    monkeypatch.setattr("angel_option_chain.AngelOptionChainEngine", FakeEngine)


def _row(strike, ce_oi, pe_oi):
    return {"strike": strike, "CE_LTP": 100, "PE_LTP": 90, "CE_OI": ce_oi, "PE_OI": pe_oi,
            "CE_CHG_OI": 0, "PE_CHG_OI": 0}


def test_change_oi_derived_from_previous_session(tmp_path, monkeypatch):
    db = tmp_path / "snap.db"
    yday = (date.today() - timedelta(days=1)).isoformat()
    _snapshot_db(db, yday, [{"expiryDate": "2026-10-20", "strikePrice": 25000.0,
                             "CE_openInterest": 1000.0, "PE_openInterest": 2000.0}])
    monkeypatch.setattr(ocf, "_SNAPSHOT_DB", str(db))
    monkeypatch.setattr(ocf, "_PREV_SESSION_OI", {})
    _fake_angel(monkeypatch, [_row(25000, 1500, 1800), _row(25050, 700, 900)])
    payload = ocf.NSEOptionChainFetcher("NIFTY")._fetch_from_angel()
    rows = {r["strikePrice"]: r for r in payload["records"]["data"]}
    assert rows[25000.0]["CE"]["changeinOpenInterest"] == 500.0
    assert rows[25000.0]["PE"]["changeinOpenInterest"] == -200.0
    assert rows[25050.0]["CE"]["changeinOpenInterest"] == 0.0          # no baseline -> neutral 0
    assert payload["_change_oi_source"].startswith("previous_session_snapshot (1/2")


def test_no_previous_session_keeps_change_unavailable(tmp_path, monkeypatch):
    db = tmp_path / "snap.db"
    _snapshot_db(db, (date.today() - timedelta(days=10)).isoformat(),        # too old
                 [{"expiryDate": "2026-10-20", "strikePrice": 25000.0, "CE_openInterest": 1, "PE_openInterest": 1}])
    monkeypatch.setattr(ocf, "_SNAPSHOT_DB", str(db))
    monkeypatch.setattr(ocf, "_PREV_SESSION_OI", {})
    _fake_angel(monkeypatch, [_row(25000, 1500, 1800)])
    payload = ocf.NSEOptionChainFetcher("NIFTY")._fetch_from_angel()
    assert payload["records"]["data"][0]["CE"]["changeinOpenInterest"] == 0.0
    assert payload["_change_oi_source"] == "unavailable"
