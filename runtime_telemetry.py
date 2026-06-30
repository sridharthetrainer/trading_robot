"""Durable low-overhead runtime, scanner, API, and recovery telemetry."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

DB_PATH = Path(os.getenv("RUNTIME_TELEMETRY_DB", "runtime_telemetry.db"))
BOOT_TS = time.time()
_LOCAL = threading.local()
_SCHEMA_READY_FOR = ""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def ensure_schema() -> None:
    global _SCHEMA_READY_FOR
    if _SCHEMA_READY_FOR == str(DB_PATH) and DB_PATH.exists():
        return
    con = _connect()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS component_heartbeats (
      component TEXT PRIMARY KEY, updated_at REAL NOT NULL, status TEXT NOT NULL,
      detail TEXT NOT NULL DEFAULT '{}', pid INTEGER, process_uptime_sec REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS crash_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL,
      component TEXT NOT NULL, reason TEXT, traceback TEXT, cpu_pct REAL,
      ram_mb REAL, last_scanned_symbol TEXT, last_executed_strategy TEXT,
      recovery_action TEXT, recovered_at REAL, details TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS api_failures (
      id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL,
      provider TEXT, endpoint TEXT, status_code INTEGER, error TEXT,
      attempt INTEGER, details TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS scan_cycles (
      id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL NOT NULL,
      finished_at REAL, scanned INTEGER DEFAULT 0, signals INTEGER DEFAULT 0,
      qualified INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0,
      duration_ms REAL DEFAULT 0, latency_ms REAL DEFAULT 0,
      last_scanned_symbol TEXT, status TEXT DEFAULT 'RUNNING', error TEXT
    );
    CREATE TABLE IF NOT EXISTS raw_signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER, created_at REAL,
      symbol TEXT, strategy TEXT, side TEXT, score REAL, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS qualified_signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER, created_at REAL,
      symbol TEXT, strategy TEXT, side TEXT, score REAL, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS rejected_signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER, created_at REAL,
      symbol TEXT, strategy TEXT, side TEXT, score REAL, reason TEXT, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS strategy_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id INTEGER, created_at REAL,
      symbol TEXT, strategy TEXT, duration_ms REAL, result TEXT, error TEXT
    );
    CREATE TABLE IF NOT EXISTS trade_journal (
      trade_id TEXT PRIMARY KEY, created_at REAL, symbol TEXT, strategy TEXT,
      side TEXT, entry_reason TEXT, exit_reason TEXT, entry_price REAL,
      exit_price REAL, initial_sl REAL, trailing_sl REAL, target REAL,
      risk_reward REAL, brokerage REAL, taxes REAL, net_pnl REAL,
      mfe REAL, mae REAL, duration_sec REAL, capital_used REAL,
      available_capital REAL, drawdown REAL, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS option_snapshot (
      id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at REAL, underlying TEXT,
      expiry TEXT, spot REAL, pcr REAL, max_pain REAL, call_wall REAL,
      put_wall REAL, freshness_sec REAL, quality_score REAL, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS market_context (
      id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at REAL, symbol TEXT,
      regime TEXT, spot REAL, vix REAL, pcr REAL, max_pain REAL,
      freshness_sec REAL, quality_score REAL, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS daily_summary (
      session_date TEXT PRIMARY KEY, generated_at REAL, payload TEXT
    );
    CREATE TABLE IF NOT EXISTS cumulative_summary (
      window_days INTEGER, generated_at REAL, payload TEXT,
      PRIMARY KEY(window_days, generated_at)
    );
    CREATE TABLE IF NOT EXISTS strategy_statistics (
      strategy TEXT, window_days INTEGER, generated_at REAL, samples INTEGER,
      win_rate REAL, profit_factor REAL, expectancy REAL, median_pnl REAL,
      avg_pnl REAL, sample_quality TEXT, weight REAL, confidence REAL,
      PRIMARY KEY(strategy, window_days, generated_at)
    );
    CREATE INDEX IF NOT EXISTS idx_scan_started ON scan_cycles(started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_raw_cycle ON raw_signals(cycle_id);
    CREATE INDEX IF NOT EXISTS idx_qualified_cycle ON qualified_signals(cycle_id);
    CREATE INDEX IF NOT EXISTS idx_rejected_cycle ON rejected_signals(cycle_id);
    CREATE INDEX IF NOT EXISTS idx_crash_time ON crash_history(occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_api_failure_time ON api_failures(occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_option_snapshot ON option_snapshot(underlying,captured_at DESC);
    """)
    con.commit(); con.close(); _SCHEMA_READY_FOR = str(DB_PATH)


def _json(value: Any) -> str:
    return json.dumps(value or {}, separators=(",", ":"), default=str)[:20000]


def heartbeat(component: str, status: str = "OK", **detail: Any) -> None:
    try:
        ensure_schema()
        con = _connect()
        con.execute("""INSERT INTO component_heartbeats
          (component,updated_at,status,detail,pid,process_uptime_sec) VALUES (?,?,?,?,?,?)
          ON CONFLICT(component) DO UPDATE SET updated_at=excluded.updated_at,
          status=excluded.status,detail=excluded.detail,pid=excluded.pid,
          process_uptime_sec=excluded.process_uptime_sec""",
          (str(component), time.time(), str(status), _json(detail), os.getpid(), time.time()-BOOT_TS))
        con.commit(); con.close()
    except Exception:
        pass


def begin_scan(symbols_expected: int = 0) -> int:
    ensure_schema(); con = _connect()
    cur = con.execute("INSERT INTO scan_cycles(started_at,status,scanned) VALUES (?,'RUNNING',0)", (time.time(),))
    cycle_id = int(cur.lastrowid); con.commit(); con.close()
    _LOCAL.scan_cycle_id = cycle_id
    heartbeat("scanner", cycle_id=cycle_id, symbols_expected=symbols_expected)
    return cycle_id


def scan_progress(cycle_id: int, symbol: str, *, duration_ms: float = 0,
                  strategy: str = "", result: str = "", error: str = "") -> None:
    try:
        con = _connect()
        con.execute("UPDATE scan_cycles SET scanned=scanned+1,last_scanned_symbol=? WHERE id=?",
                    (str(symbol), int(cycle_id)))
        if strategy:
            con.execute("INSERT INTO strategy_runs(cycle_id,created_at,symbol,strategy,duration_ms,result,error) VALUES (?,?,?,?,?,?,?)",
                        (cycle_id,time.time(),symbol,strategy,duration_ms,result,error[:500]))
        con.commit(); con.close()
        heartbeat("scanner", cycle_id=cycle_id, last_scanned_symbol=symbol)
    except Exception:
        pass


def log_signal(kind: str, cycle_id: int, signal: Dict[str, Any], reason: str = "") -> None:
    table = {"raw":"raw_signals", "qualified":"qualified_signals", "rejected":"rejected_signals"}.get(kind)
    if not table: return
    try:
        con = _connect(); values = (
            cycle_id,time.time(),str(signal.get("symbol", "")),str(signal.get("strategy", "")),
            str(signal.get("side", signal.get("action", ""))),float(signal.get("score", signal.get("final_score", 0)) or 0),
        )
        if kind == "rejected":
            con.execute(f"INSERT INTO {table}(cycle_id,created_at,symbol,strategy,side,score,reason,payload) VALUES (?,?,?,?,?,?,?,?)",
                        (*values, reason[:500], _json(signal)))
        else:
            con.execute(f"INSERT INTO {table}(cycle_id,created_at,symbol,strategy,side,score,payload) VALUES (?,?,?,?,?,?,?)",
                        (*values, _json(signal)))
        con.commit(); con.close()
    except Exception:
        pass


def finish_scan(cycle_id: int, *, signals: int, qualified: int, rejected: int,
                started_at: float, error: str = "") -> None:
    duration_ms = (time.time() - started_at) * 1000
    try:
        con = _connect()
        con.execute("""UPDATE scan_cycles SET finished_at=?,signals=?,qualified=?,rejected=?,
          duration_ms=?,latency_ms=?,status=?,error=? WHERE id=?""",
          (time.time(),signals,qualified,rejected,duration_ms,duration_ms,
           "ERROR" if error else "OK",error[:1000],cycle_id))
        con.commit(); con.close()
    finally:
        heartbeat("scanner", "ERROR" if error else "OK", cycle_id=cycle_id,
                  signals=signals, qualified=qualified, rejected=rejected,
                  duration_ms=round(duration_ms,1))


def seconds_since_last_scan() -> float:
    """Seconds since the most recent scan cycle BEGAN. inf if none/unavailable.

    Lets the watchdog auto-repair an 'alive but not scanning' bot (fresh heartbeat
    yet no scans during market hours — the gap the stale/memory triggers miss)."""
    try:
        con = _connect()
        row = con.execute("SELECT MAX(started_at) FROM scan_cycles").fetchone()
        con.close()
        if row and row[0]:
            return max(0.0, time.time() - float(row[0]))
    except Exception:
        pass
    return float("inf")


def record_api_failure(provider: str, endpoint: str, error: Any,
                       status_code: int = 0, attempt: int = 0, **details: Any) -> None:
    try:
        ensure_schema(); con = _connect()
        con.execute("INSERT INTO api_failures(occurred_at,provider,endpoint,status_code,error,attempt,details) VALUES (?,?,?,?,?,?,?)",
                    (time.time(),provider,endpoint,int(status_code or 0),str(error)[:1000],attempt,_json(details)))
        con.commit(); con.close()
    except Exception: pass


def sync_trade_journal(trade: Any) -> None:
    try:
        row=trade if isinstance(trade,dict) else vars(trade); meta=row.get("metadata") or {}
        if not isinstance(meta,dict): meta={}
        entry=float(row.get("entry_price",0) or 0); high=float(row.get("highest_price",entry) or entry); low=float(row.get("lowest_price",entry) or entry)
        if str(row.get("side","")).upper()=="SELL": mfe=max(0,entry-low); mae=max(0,high-entry)
        else: mfe=max(0,high-entry); mae=max(0,entry-low)
        costs=meta.get("costs") or {}; con=_connect()
        con.execute("""INSERT OR REPLACE INTO trade_journal
          (trade_id,created_at,symbol,strategy,side,entry_reason,exit_reason,entry_price,
           exit_price,initial_sl,trailing_sl,target,risk_reward,brokerage,taxes,net_pnl,
           mfe,mae,duration_sec,capital_used,available_capital,drawdown,payload)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (str(row.get("trade_id","")),float(row.get("created_at") or time.time()),row.get("symbol"),
           row.get("strategy"),row.get("side"),row.get("entry_reason") or meta.get("entry_reason",""),
           row.get("exit_reason",""),row.get("entry_price",0),row.get("exit_price",0),
           row.get("stop_loss",0),row.get("trail_stop",0),row.get("target_price",0),
           row.get("r_multiple",0),row.get("brokerage",costs.get("brokerage",0)),
           float(row.get("total_charges",0) or 0)-float(row.get("brokerage",0) or 0),
           row.get("realized_pnl",0),meta.get("mfe",mfe),meta.get("mae",mae),
           float(row.get("holding_minutes",0) or 0)*60,float(row.get("entry_price",0) or 0)*int(row.get("qty",0) or 0),
           meta.get("available_capital",0),meta.get("drawdown",0),_json(row)))
        con.commit(); con.close()
    except Exception: pass


def _memory_mb(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"): return float(line.split()[1]) / 1024
    except Exception: pass
    return 0.0


def record_crash(component: str, reason: Any, *, pid: int = 0,
                 recovery_action: str = "", **details: Any) -> int:
    ensure_schema(); pid = int(pid or os.getpid())
    try: load = os.getloadavg()[0]
    except Exception: load = 0.0
    con = _connect(); cur = con.execute("""INSERT INTO crash_history
      (occurred_at,component,reason,traceback,cpu_pct,ram_mb,last_scanned_symbol,
       last_executed_strategy,recovery_action,details) VALUES (?,?,?,?,?,?,?,?,?,?)""",
      (time.time(),component,str(reason)[:2000],traceback.format_exc()[:10000],load,
       _memory_mb(pid),details.get("last_scanned_symbol", ""),
       details.get("last_executed_strategy", ""),recovery_action,_json(details)))
    crash_id=int(cur.lastrowid); con.commit(); con.close(); return crash_id


def mark_recovered(crash_id: int) -> None:
    try:
        con=_connect(); con.execute("UPDATE crash_history SET recovered_at=? WHERE id=?",(time.time(),crash_id)); con.commit(); con.close()
    except Exception: pass


def snapshot() -> Dict[str, Any]:
    ensure_schema(); con=_connect(); con.row_factory=sqlite3.Row
    hearts=[dict(r) for r in con.execute("SELECT * FROM component_heartbeats ORDER BY component")]
    last_scan=con.execute("SELECT * FROM scan_cycles ORDER BY id DESC LIMIT 1").fetchone()
    crashes=con.execute("SELECT COUNT(*) FROM crash_history").fetchone()[0]
    con.close(); now=time.time()
    for row in hearts: row["age_sec"]=round(now-float(row["updated_at"]),1)
    return {"system_uptime_sec":round(now-BOOT_TS,1),"heartbeats":hearts,
            "last_scan":dict(last_scan) if last_scan else {},"crash_count":crashes}


ensure_schema()
