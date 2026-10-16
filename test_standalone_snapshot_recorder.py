"""Standalone option-chain snapshot recorder + watchdog: record-only, own files,
no writes to bot data, no extra broker load while the bot's recorder runs."""
import ast
import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import standalone_recorder_watchdog as wd
import standalone_snapshot_recorder as rec

REPO = Path(__file__).resolve().parent


def _bot_db(path, last_ts):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE option_chain_snapshots (ts REAL, snapshot_time TEXT, underlying TEXT, ok INTEGER)")
    con.execute("INSERT INTO option_chain_snapshots VALUES (?,?,?,1)", (last_ts, "2026-10-19T10:00:00+0530", "NIFTY"))
    con.commit()
    con.close()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _FakeFetcher:
    def __init__(self, source="angel"):
        self.last_source = source
        self.last_request_id = "req-1"
        self.calls = 0

    def fetch_and_analyze(self):
        self.calls += 1
        df = pd.DataFrame([{"strikePrice": 25000.0, "expiryDate": "2026-10-20", "CE_lastPrice": 120.0,
                            "PE_lastPrice": 110.0, "CE_openInterest": 1.0, "PE_openInterest": 2.0}])
        return SimpleNamespace(dataframe=df, summary={"pcr_oi": 1.1}, spot=25010.0, expiry="2026-10-20",
                               atm_strike=25000.0)


def test_skips_without_api_call_while_bot_recorder_is_active(tmp_path):
    bot = tmp_path / "bot.db"
    _bot_db(bot, time.time() - 60)
    own = tmp_path / "own.db"

    def must_not_fetch(_u):
        raise AssertionError("fetch attempted while the bot's recorder is active")

    res = rec.record_once("NIFTY", own_db=own, bot_db=bot, fetcher_factory=must_not_fetch)
    assert res["action"] == "skipped_bot_recorder_active"
    assert not own.exists()


def test_records_only_into_own_db_when_bot_is_idle(tmp_path):
    bot = tmp_path / "bot.db"
    _bot_db(bot, time.time() - 3600)
    before = _sha(bot)
    own = tmp_path / "own.db"
    f = _FakeFetcher("angel")
    res = rec.record_once("NIFTY", own_db=own, bot_db=bot, fetcher_factory=lambda u: f)
    assert res["action"] == "recorded" and f.calls == 1
    assert _sha(bot) == before                                         # bot data untouched
    row = sqlite3.connect(own).execute("SELECT ok, underlying, source, rows_json FROM option_chain_snapshots").fetchone()
    assert row[0] == 1 and row[1] == "NIFTY" and row[2] == "standalone:angel"
    assert json.loads(row[3])[0]["CE_lastPrice"] == 120.0


def test_non_live_source_is_recorded_as_failure(tmp_path):
    own = tmp_path / "own.db"
    res = rec.record_once("NIFTY", own_db=own, bot_db=tmp_path / "missing.db",
                          fetcher_factory=lambda u: _FakeFetcher("cache"))
    assert res["action"] == "recorded_failure" and res["reason"] == "non_live_source:cache"
    assert sqlite3.connect(own).execute("SELECT ok, rows_json FROM option_chain_snapshots").fetchone() == (0, "[]")


def test_market_hours():
    assert rec.in_market_hours(datetime(2026, 10, 19, 10, 0))          # Monday
    assert not rec.in_market_hours(datetime(2026, 10, 18, 10, 0))      # Sunday
    assert not rec.in_market_hours(datetime(2026, 10, 19, 8, 59))
    assert not rec.in_market_hours(datetime(2026, 10, 19, 15, 31))


ORDER_WORDS = ("place_order", "placeorder", "modify_order", "cancel_order", "execute_trade", "placeOrder",
               "modifyOrder", "cancelOrder", "_quick_execute_option")
TRADING_MODULES = ("trade_manager", "confirmed_option_orders", "kill_switch", "order_manager",
                   "manual_trade_tracker", "main_autonomous", "live_signal_engine", "signal_engine")


@pytest.mark.parametrize("fname", ["standalone_snapshot_recorder.py", "standalone_recorder_watchdog.py"])
def test_record_only_no_trading_code(fname):
    src = (REPO / fname).read_text()
    for w in ORDER_WORDS:
        assert w not in src, w
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert not mods & set(TRADING_MODULES), mods & set(TRADING_MODULES)


def test_live_bot_does_not_reference_the_standalone_service():
    for live in ("main_autonomous.py", "signal_engine.py", "live_signal_engine.py", "option_chain_recorder.py",
                 "option_chain_fetcher.py", "manual_trade_tracker.py"):
        src = (REPO / live).read_text()
        assert "standalone_snapshot_recorder" not in src and "standalone_recorder" not in src, live


def test_service_unit_is_isolated_and_restarts():
    unit = (REPO / "deploy/systemd/option-snapshot-recorder.service").read_text()
    assert "WorkingDirectory=/home/owner/Desktop/trading_robot/standalone_recorder" in unit
    assert "Restart=always" in unit and "RUNTIME_TELEMETRY_DB=" in unit
    assert "standalone_snapshot_recorder.py" in unit and "main_autonomous" not in unit
    timer = (REPO / "deploy/systemd/cpr-forward-report.timer").read_text()
    assert "OnCalendar=Mon..Fri 16:00" in timer


# ── watchdog ───────────────────────────────────────────────────────────────

def test_watchdog_problem_rules():
    assert wd.problems(0, False, 10, 60, 60) == ["option-snapshot-recorder.service is not active"]
    assert "heartbeat stale" in wd.problems(0, True, 13 * 60, 60, 60)[0]
    assert "no ok NIFTY snapshot" in wd.problems(0, True, 30, 16 * 60, 60)[0]
    assert wd.problems(0, True, 30, 16 * 60, 10) == []               # grace period just after the open
    assert wd.problems(0, True, 30, 60, 60) == []


def test_watchdog_alerts_once_then_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(wd.rec, "in_market_hours", lambda now=None: True)
    monkeypatch.setattr(wd, "heartbeat_age", lambda now: 30)
    monkeypatch.setattr(wd, "last_snapshot_age", lambda now, dbs: 60)
    sent = []
    send = lambda text: sent.append(text) or True
    monkeypatch.setattr(wd, "service_active", lambda: False)
    t0 = datetime(2026, 10, 19, 11, 0)
    assert wd.run(t0, send)["alert_sent"] is True
    assert wd.run(datetime(2026, 10, 19, 11, 5), send)["alert_sent"] is False     # no repeat within 30 min
    assert wd.run(datetime(2026, 10, 19, 11, 31), send)["alert_sent"] is True     # reminder after 30 min
    monkeypatch.setattr(wd, "service_active", lambda: True)
    assert wd.run(datetime(2026, 10, 19, 11, 36), send)["alert_sent"] is True     # recovered
    assert "recovered" in sent[-1] and len(sent) == 3


def test_watchdog_silent_outside_market_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "STATE", tmp_path / "state.json")
    assert wd.run(datetime(2026, 10, 18, 11, 0), lambda t: True)["checked"] is False   # Sunday


# ── R001 real-price layer reads both stores ──────────────────────────────────

def _snap_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE option_chain_snapshots (snapshot_time TEXT, underlying TEXT, ok INTEGER, expiry TEXT, rows_json TEXT)")
    con.executemany("INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_real_layer_merges_bot_and_standalone_stores(tmp_path, monkeypatch):
    from cpr_vwap_ema_nifty import options_layers as ol
    from cpr_vwap_ema_nifty.backtest import load_config
    chain = json.dumps([{"strikePrice": 25000.0, "CE_lastPrice": 100.0}])
    _snap_db(tmp_path / "bot.db", [("2026-10-19T09:20:30+0530", "NIFTY", 1, "2026-10-20", chain)])
    _snap_db(tmp_path / "own.db", [("2026-10-20T11:05:10+0530", "NIFTY", 1, "2026-10-20", chain)])
    monkeypatch.setattr(ol, "SNAPSHOT_DB", tmp_path / "bot.db")
    monkeypatch.setattr(ol, "STANDALONE_SNAPSHOT_DB", tmp_path / "own.db")
    real = ol.RealLayer(load_config())
    assert real.days() == ["2026-10-19", "2026-10-20"]
    assert real.price("2026-10-20", 25000.0, "CE", datetime(2026, 10, 20, 11, 0))[0] == 100.0
    assert real.day_counts(["2026-10-19", "2026-10-20"]) == {"2026-10-19": 1, "2026-10-20": 1}
