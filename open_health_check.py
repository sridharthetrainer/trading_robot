#!/usr/bin/env python3
"""
open_health_check.py — market-open health probe (run by cron at ~09:25 IST).

Reads the LOCAL runtime logs/processes (which only exist on this machine) and
sends a concise Telegram summary so a silent failure at the open is impossible
to miss. Read-only: it never touches trades, config, or the bot itself.

Checks:
  1. Scan health — "SCAN: N symbols with data" lines since 09:15 today.
  2. Errors      — SCANNED:0 / _is_expiry_day / Traceback since 09:15 today.
  3. Watchdog    — heartbeat.json freshness (stale => watchdog may kill).
  4. Manual trade monitoring — tracker process alive + Angel connected today.

Telegram creds are read from .env (cron has no inherited env).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, date

ROOT = os.path.dirname(os.path.abspath(__file__))
BOT_LOG     = os.path.join(ROOT, "trading_bot.log")
TRACKER_LOG = os.path.join(ROOT, "manual_tracker.log")
HEARTBEAT   = os.path.join(ROOT, "heartbeat.json")
ENV_FILE    = os.path.join(ROOT, ".env")

OPEN_HHMM   = (9, 15)   # NSE open; only consider log lines at/after this


def _load_env(keys):
    """Minimal .env reader — returns {key: value} for requested keys."""
    out = {k: os.getenv(k, "") for k in keys}
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in keys and not out.get(k):
                    out[k] = v
    except Exception:
        pass
    return out


def _today_lines(path):
    """Yield today's log lines at/after the open (best-effort, tolerant)."""
    today = date.today().strftime("%Y-%m-%d")
    open_t = datetime.now().replace(hour=OPEN_HHMM[0], minute=OPEN_HHMM[1],
                                    second=0, microsecond=0)
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if not line.startswith(today):
                    continue
                m = re.match(r"\d{4}-\d{2}-\d{2} (\d{2}):(\d{2})", line)
                if m:
                    hh, mm = int(m.group(1)), int(m.group(2))
                    if (hh, mm) < OPEN_HHMM:
                        continue
                yield line
    except FileNotFoundError:
        return


def check_scans():
    counts = []
    for line in _today_lines(BOT_LOG):
        m = re.search(r"SCAN:\s+(\d+)\s+symbols with data", line)
        if m:
            counts.append(int(m.group(1)))
    return counts


def check_errors():
    hits = {"SCANNED:0": 0, "_is_expiry_day": 0, "Traceback": 0}
    for line in _today_lines(BOT_LOG):
        if "SCANNED: 0" in line or "SCAN: 0 symbols" in line:
            hits["SCANNED:0"] += 1
        if "_is_expiry_day" in line:
            hits["_is_expiry_day"] += 1
        if "Traceback (most recent call last)" in line:
            hits["Traceback"] += 1
    return hits


def check_rate_limits():
    """Count Angel 'exceeding access rate' errors since 09:15 today.

    Before the angel.py anti-storm fix this ran into the thousands during the
    morning; a low number here confirms the reconnect storm is gone.
    """
    n = 0
    for line in _today_lines(BOT_LOG):
        if "exceeding access rate" in line:
            n += 1
    return n


def check_heartbeat():
    try:
        ts = json.load(open(HEARTBEAT)).get("ts", 0)
        return int(time.time() - float(ts))
    except Exception:
        return None  # missing/unreadable


def check_tracker():
    alive = subprocess.run(
        ["pgrep", "-f", "manual_trade_tracker.py"],
        capture_output=True).returncode == 0
    today = date.today().strftime("%Y-%m-%d")
    connected = False
    try:
        with open(TRACKER_LOG, errors="replace") as f:
            for line in f:
                if line.startswith(today) and "Connected to Angel One" in line:
                    connected = True
    except FileNotFoundError:
        pass
    return alive, connected


def send_telegram(text):
    env = _load_env(["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
    token, chat = env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]
    if not token or not chat:
        print("No Telegram creds — printing instead:\n" + text)
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print("Telegram send failed: %s\n%s" % (e, text))


def main():
    counts = check_scans()
    errs   = check_errors()
    rlim   = check_rate_limits()
    hb     = check_heartbeat()
    t_alive, t_conn = check_tracker()

    healthy = (
        bool(counts) and min(counts) > 0 and
        errs["SCANNED:0"] == 0 and errs["_is_expiry_day"] == 0 and
        rlim < 500 and
        (hb is not None and hb < 300) and
        t_alive and t_conn
    )
    head = "✅ OPEN HEALTH — all good" if healthy else "⚠️ OPEN HEALTH — needs a look"

    if counts:
        scan_line = (f"Scans: {len(counts)} cycles, "
                     f"min={min(counts)} max={max(counts)} symbols")
    else:
        scan_line = "Scans: none yet (no SCAN lines since 09:15)"

    err_bits = [f"{k}={v}" for k, v in errs.items() if v]
    err_line = "Errors: " + (", ".join(err_bits) if err_bits else "none")

    rl_line = (f"Angel rate-limit hits: {rlim}" +
               (" ⚠️" if rlim >= 500 else " ✓"))

    if hb is None:
        hb_line = "Heartbeat: MISSING ⚠️"
    elif hb < 300:
        hb_line = f"Heartbeat: fresh ({hb}s) ✓"
    else:
        hb_line = f"Heartbeat: STALE ({hb}s) ⚠️"

    trk_line = ("Manual tracker: " +
                ("running ✓" if t_alive else "NOT running ⚠️") + " | " +
                ("Angel connected ✓" if t_conn else "Angel NOT connected ⚠️"))

    msg = (f"<b>{head}</b>\n"
           f"{datetime.now():%Y-%m-%d %H:%M}\n"
           f"• {scan_line}\n"
           f"• {err_line}\n"
           f"• {rl_line}\n"
           f"• {hb_line}\n"
           f"• {trk_line}")
    send_telegram(msg)
    print(msg)


if __name__ == "__main__":
    main()
