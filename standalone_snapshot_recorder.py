#!/usr/bin/env python3
"""
standalone_snapshot_recorder.py — RECORD-ONLY option-chain snapshot service that
keeps recording when the trading bot is stopped.

Why: R001's forward bar needs >= 80% of trades priced from real option-chain
snapshots. The bot's in-process recorder only runs while main_autonomous.py is
up; between 2026-06-19 and 2026-10-15, 25 of 82 sessions had no snapshots at
all (bot down, not fetch failures).

Isolation (no writes to any existing bot data):
  * Runs with its working directory = standalone_recorder/, so every relative
    path written by shared code (fetcher cache, master-contract refresh, alert
    state) lands in that folder.
  * Own database:  standalone_recorder/option_chain_snapshots_standalone.db
  * Own log:       standalone_recorder/recorder.log
  * Own telemetry: RUNTIME_TELEMETRY_DB -> standalone_recorder/runtime_telemetry_standalone.db
  * Reads the bot's option_chain_snapshots.db READ-ONLY (sqlite mode=ro).
  * Does NOT call option_chain_recorder.record_option_chain_snapshot (that one
    also writes strike-flow signals, the option journal and Telegram alerts);
    it reuses only its pure helpers _conn (schema on OUR db path), _chain_rows
    and _is_live_source.
  * No trading or order code is called. (The chain fetcher imports the broker
    wrapper to read quotes; nothing here places, modifies or cancels orders.)

No extra broker load while the bot runs: each cycle first checks the bot's
database; if the bot recorded an ok snapshot for that underlying in the last
6 minutes, this cycle is skipped without any API call.

Sleeps outside 09:15-15:30 IST, weekends and NSE holidays.

  python standalone_snapshot_recorder.py            # service loop
  python standalone_snapshot_recorder.py --once     # one cycle (manual check)
  python standalone_snapshot_recorder.py --status   # heartbeat + today's counts
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parent
HOME = REPO / "standalone_recorder"
OWN_DB = HOME / "option_chain_snapshots_standalone.db"
BOT_DB = REPO / "option_chain_snapshots.db"
HEARTBEAT = HOME / "heartbeat.json"
LOG_FILE = HOME / "recorder.log"
HOLIDAYS = REPO / "trading_holidays.json"

INTERVAL_SEC = int(os.getenv("SNAPSHOT_STANDALONE_INTERVAL_SEC", "300"))
BOT_FRESH_SEC = int(os.getenv("SNAPSHOT_STANDALONE_BOT_FRESH_SEC", "360"))
UNDERLYINGS = [s.strip().upper() for s in os.getenv("SNAPSHOT_STANDALONE_UNDERLYINGS", "NIFTY").split(",") if s.strip()]
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)

logger = logging.getLogger("standalone_snapshot_recorder")


def isolate() -> None:
    """Own working dir, own telemetry DB, credentials from the repo .env."""
    HOME.mkdir(parents=True, exist_ok=True)
    os.environ["RUNTIME_TELEMETRY_DB"] = str(HOME / "runtime_telemetry_standalone.db")
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    os.chdir(HOME)
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except Exception as exc:
        logger.warning("dotenv not loaded: %s", exc)


def holidays() -> set:
    try:
        data = json.loads(HOLIDAYS.read_text())
        if isinstance(data, dict):
            data = data.get("holidays") or data.get("dates") or []
        return {str(d)[:10] for d in (data or [])}
    except Exception:
        return set()


def in_market_hours(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5 or now.strftime("%Y-%m-%d") in holidays():
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def bot_recorded_recently(underlying: str, now_ts: Optional[float] = None, bot_db: Path = BOT_DB) -> bool:
    """True when the bot's own recorder saved an ok snapshot within BOT_FRESH_SEC (read-only)."""
    if not Path(bot_db).exists():
        return False
    try:
        con = sqlite3.connect(f"file:{bot_db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT MAX(ts) FROM option_chain_snapshots WHERE underlying=? AND ok=1",
                              (underlying,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("bot db read failed: %s", exc)
        return False
    last = row[0] if row else None
    return bool(last) and ((now_ts or time.time()) - float(last)) < BOT_FRESH_SEC


def _default_fetcher(underlying: str):
    from option_chain_fetcher import NSEOptionChainFetcher
    return NSEOptionChainFetcher(underlying=underlying, cache_file=str(HOME / f"cache_{underlying.lower()}.json"),
                                 use_cache_fallback=False)


def record_once(underlying: str, *, own_db: Path = OWN_DB, bot_db: Path = BOT_DB,
                fetcher_factory: Callable = _default_fetcher, now_ts: Optional[float] = None) -> Dict:
    """One cycle for one underlying. Writes only to own_db."""
    from option_chain_recorder import _chain_rows, _conn, _is_live_source
    if bot_recorded_recently(underlying, now_ts, bot_db):
        return {"underlying": underlying, "action": "skipped_bot_recorder_active"}
    fetcher = fetcher_factory(underlying)
    try:
        result = fetcher.fetch_and_analyze()
        err = ""
    except Exception as exc:
        result, err = None, str(exc)[:200]
    rows = _chain_rows(result.dataframe) if result else []
    src = str(getattr(fetcher, "last_source", "") or "")
    live = bool(rows) and _is_live_source(src)
    reason = "" if live else (err or ("no_option_chain" if not result else
                                      "empty_chain_rows" if not rows else f"non_live_source:{src or 'unknown'}"))
    summary = (result.summary or {}) if result else {}
    con = _conn(str(own_db))
    try:
        con.execute(
            "INSERT INTO option_chain_snapshots (ts, snapshot_time, underlying, spot, expiry, atm_strike, pcr_oi, "
            "pcr_change_oi, max_pain, ok, reason, rows_json, summary_json, source, is_live, provider_request_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now_ts or time.time(), time.strftime("%Y-%m-%dT%H:%M:%S%z"), underlying,
             float(getattr(result, "spot", 0) or 0) if result else 0.0,
             str(getattr(result, "expiry", "") or "") if result else "",
             float(getattr(result, "atm_strike", 0) or 0) if result else 0.0,
             float(summary.get("pcr_oi", 0) or 0), float(summary.get("pcr_change_oi", 0) or 0),
             float(summary.get("max_pain", 0) or 0), 1 if live else 0, reason,
             json.dumps(rows if live else [], default=str), json.dumps(summary, default=str),
             f"standalone:{src}" if src else "standalone", 1 if live else 0,
             str(getattr(fetcher, "last_request_id", "") or "")))
        con.commit()
    finally:
        con.close()
    return {"underlying": underlying, "action": "recorded" if live else "recorded_failure", "rows": len(rows),
            "reason": reason}


def write_heartbeat(state: str, results: List[Dict]) -> None:
    HEARTBEAT.write_text(json.dumps({"ts": time.time(), "time": datetime.now().isoformat(timespec="seconds"),
                                     "pid": os.getpid(), "state": state, "results": results}, indent=2))


def cycle() -> List[Dict]:
    out = []
    for u in UNDERLYINGS:
        try:
            out.append(record_once(u))
        except Exception as exc:  # never die on one bad cycle; systemd restarts on crashes
            logger.exception("cycle %s failed", u)
            out.append({"underlying": u, "action": "error", "reason": str(exc)[:200]})
    return out


def run_forever() -> None:
    logger.info("standalone recorder started pid=%s underlyings=%s interval=%ss db=%s",
                os.getpid(), UNDERLYINGS, INTERVAL_SEC, OWN_DB)
    while True:
        if not in_market_hours():
            write_heartbeat("idle_outside_market_hours", [])
            time.sleep(60)
            continue
        started = time.time()
        res = cycle()
        write_heartbeat("market_hours", res)
        logger.info("cycle: %s", "; ".join(f"{r['underlying']}={r['action']}" for r in res))
        time.sleep(max(5.0, INTERVAL_SEC - (time.time() - started)))


def status() -> Dict:
    hb = json.loads(HEARTBEAT.read_text()) if HEARTBEAT.exists() else {}
    today = datetime.now().strftime("%Y-%m-%d")
    counts = {}
    if OWN_DB.exists():
        con = sqlite3.connect(f"file:{OWN_DB}?mode=ro", uri=True)
        counts = {u: dict(zip(("ok", "failed"), con.execute(
            "SELECT SUM(ok=1), SUM(ok=0) FROM option_chain_snapshots WHERE underlying=? AND substr(snapshot_time,1,10)=?",
            (u, today)).fetchone())) for u in UNDERLYINGS}
        con.close()
    age = round(time.time() - hb["ts"]) if hb.get("ts") else None
    return {"heartbeat_age_sec": age, "heartbeat": hb, "today_own_db": counts, "own_db": str(OWN_DB)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record-only standalone option-chain snapshot recorder")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    isolate()
    logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("smartConnect", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if a.status:
        print(json.dumps(status(), indent=2, default=str))
        return 0
    if a.once:
        res = cycle()
        write_heartbeat("manual_once", res)
        print(json.dumps(res, indent=2))
        return 0
    run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
