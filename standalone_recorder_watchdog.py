#!/usr/bin/env python3
"""
standalone_recorder_watchdog.py — alert when option-chain snapshots stop during
market hours (run by option-snapshot-watchdog.timer every 5 minutes, weekdays).

Problems checked (only 09:15-15:30 IST on NSE sessions; silent otherwise):
  1. option-snapshot-recorder.service is not active (systemd --user)
  2. its heartbeat is older than 12 minutes (process hung)
  3. NEITHER the bot's recorder NOR the standalone recorder saved an ok NIFTY
     snapshot in the last 15 minutes (a real coverage gap, e.g. source outage)

Alerts go through the existing Telegram path (alerts.AlertManager, main bot
token/chat from .env) at most once per 30 minutes while a problem lasts, plus a
"recovered" message when it clears. State and alert spool live in
standalone_recorder/ (own files; no bot data touched). Read-only otherwise.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import standalone_snapshot_recorder as rec  # noqa: E402

STATE = rec.HOME / "watchdog_state.json"
SERVICE = "option-snapshot-recorder.service"
HEARTBEAT_MAX_AGE = 12 * 60
NO_SNAPSHOT_MAX_AGE = 15 * 60
REALERT_SEC = 30 * 60


def service_active() -> bool:
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", SERVICE], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def heartbeat_age(now: float) -> Optional[float]:
    try:
        return now - float(json.loads(rec.HEARTBEAT.read_text())["ts"])
    except Exception:
        return None


def last_snapshot_age(now: float, dbs: List[Path]) -> Optional[float]:
    newest = None
    for db in dbs:
        if not Path(db).exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            v = con.execute("SELECT MAX(ts) FROM option_chain_snapshots WHERE underlying='NIFTY' AND ok=1").fetchone()[0]
            con.close()
        except sqlite3.Error:
            continue
        if v and (newest is None or v > newest):
            newest = float(v)
    return None if newest is None else now - newest


def problems(now: float, active: bool, hb_age: Optional[float], snap_age: Optional[float],
             minutes_since_open: float) -> List[str]:
    out = []
    if not active:
        out.append(f"{SERVICE} is not active")
    elif hb_age is None or hb_age > HEARTBEAT_MAX_AGE:
        out.append(f"recorder heartbeat stale ({'none' if hb_age is None else f'{hb_age / 60:.0f} min'})")
    if minutes_since_open >= 20 and (snap_age is None or snap_age > NO_SNAPSHOT_MAX_AGE):
        out.append(f"no ok NIFTY snapshot from either recorder for "
                   f"{'ever' if snap_age is None else f'{snap_age / 60:.0f} min'}")
    return out


def _send(text: str) -> bool:
    from alerts import AlertManager
    am = AlertManager(bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""), chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                      name="standalone_recorder")
    return bool(am.send(text))


def run(now_dt=None, send=_send) -> Dict:
    from datetime import datetime
    now_dt = now_dt or datetime.now()
    now = now_dt.timestamp()
    state = json.loads(STATE.read_text()) if STATE.exists() else {"open": False, "last_alert": 0}
    if not rec.in_market_hours(now_dt):
        return {"checked": False, "reason": "outside market hours"}
    since_open = (now_dt - now_dt.replace(hour=9, minute=15, second=0, microsecond=0)).total_seconds() / 60
    probs = problems(now, service_active(), heartbeat_age(now), last_snapshot_age(now, [rec.BOT_DB, rec.OWN_DB]),
                     since_open)
    sent = False
    if probs and (not state["open"] or now - state["last_alert"] >= REALERT_SEC):
        sent = send("⚠️ <b>Option snapshot recorder</b> (R001 price coverage)\n" + "\n".join(f"• {p}" for p in probs)
                    + "\nEnable: <code>deploy/snapshot_recorder_ctl.sh enable</code>")
        state = {"open": True, "last_alert": now}
    elif not probs and state["open"]:
        sent = send("✅ Option snapshot recording recovered.")
        state = {"open": False, "last_alert": state["last_alert"]}
    STATE.write_text(json.dumps(state))
    return {"checked": True, "problems": probs, "alert_sent": sent}


def main() -> int:
    rec.isolate()  # own working dir (alert dedup/spool files land in standalone_recorder/)
    print(json.dumps(run(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
