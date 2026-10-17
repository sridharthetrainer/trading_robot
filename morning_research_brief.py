#!/usr/bin/env python3
"""
morning_research_brief.py — one read-only morning Telegram summary (2026-10-17).

Collects what was spread across reports: R001 forward status + snapshot
coverage, R002 status, the model-vs-real option calibration gap, yesterday's
option-snapshot coverage and any open recorder alert, open /journal plans, the
momentum paper tracker, and the backtest test count. Read-only: writes nothing
except the alert-state files of its own AlertManager (in cpr_vwap_ema_nifty/logs).

  python morning_research_brief.py          # print
  python morning_research_brief.py --send   # print + Telegram (main bot)
Scheduled by research-brief.timer (weekdays 08:50 IST).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List

REPO = Path(__file__).resolve().parent
PKG = REPO / "cpr_vwap_ema_nifty"
FORWARD = PKG / "results" / "forward"
RULES = PKG / "rules.json"
LEDGER = PKG / "test_ledger.json"
MOMENTUM = REPO / "reports" / "momentum_paper" / "ledger.json"
WATCHDOG_STATE = REPO / "standalone_recorder" / "watchdog_state.json"
HEARTBEAT = REPO / "standalone_recorder" / "heartbeat.json"


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def section_forward() -> List[str]:
    out = []
    rules = _json(RULES) or {}
    for r in rules.get("preregistered", []):
        if r.get("status") == "approved":
            st = _json(FORWARD / f"{r['id']}_status.json") or {}
            out.append(f"• {r['id']} (paper): {st.get('verdict', st.get('status', 'no report yet'))} — "
                       f"{st.get('reason', '')}".rstrip(" —"))
            cov = (st.get("snapshot_coverage") or {}).get("line")
            if cov:
                out.append(f"   {cov}")
        else:
            out.append(f"• {r['id']}: {r.get('status')} (not counting)")
    return out or ["• no pre-registered rules"]


def section_recorder(today: date) -> List[str]:
    out = []
    y = _prev_weekday(today)
    try:
        sys.path.insert(0, str(REPO))
        from cpr_vwap_ema_nifty.backtest import load_config
        from cpr_vwap_ema_nifty.options_layers import RealLayer
        n = RealLayer(load_config()).day_counts([y.isoformat()]).get(y.isoformat(), 0)
        out.append(f"• Option snapshots {y}: {n} minutes recorded (expect ~66 on a trading day)")
    except Exception as exc:
        out.append(f"• Option snapshots: unavailable ({exc})")
    ws = _json(WATCHDOG_STATE) or {}
    if ws.get("open"):
        out.append("• ⚠️ Recorder watchdog alert is OPEN")
    hb = _json(HEARTBEAT) or {}
    if hb.get("time"):
        out.append(f"• Standalone recorder last heartbeat {hb['time'][:16]} ({hb.get('state')})")
    return out


def section_calibration() -> List[str]:
    try:
        from cpr_vwap_ema_nifty.calibration import line, report
        return [f"• {line(report())}"]
    except Exception as exc:
        return [f"• Calibration unavailable ({exc})"]


def section_journal() -> List[str]:
    try:
        import manual_journal
        rows = manual_journal.recent(50)
        open_ = [r for r in rows if not r.get("result")]
        if not rows:
            return ["• Journal: empty (use /journal plan … before a trade)"]
        return [f"• Journal: {len(rows)} plans, {len(open_)} without a result"
                + (f" (oldest open {open_[0]['id']})" if open_ else "")]
    except Exception as exc:
        return [f"• Journal unavailable ({exc})"]


def section_momentum(today: date) -> List[str]:
    led = _json(MOMENTUM)
    nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    if not led or not led.get("entries"):
        return [f"• Momentum paper tracker: no record yet; next run {nxt} 19:30 (records the month-end selection)"]
    last = led["entries"][-1]
    settled = [e for e in led["entries"] if e.get("result")]
    return [f"• Momentum paper tracker: {len(led['entries'])} month(s) recorded, {len(settled)} settled; "
            f"last {last.get('month_end')}; next run {nxt} 19:30"]


def build(today: date = None) -> str:
    today = today or date.today()
    tests = len((_json(LEDGER) or {}).get("tests", {}))
    lines = [f"🌅 <b>Research brief {today:%a %d %b}</b> (paper only)"]
    lines += section_forward()
    lines += section_recorder(today)
    lines += section_calibration()
    lines += section_journal()
    lines += section_momentum(today)
    lines.append(f"• Tests run on the backtest dataset: {tests}")
    return "\n".join(lines)


def send(text: str) -> bool:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    logs = PKG / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    prev = os.getcwd()
    os.chdir(logs)                                   # alert dedup/spool files stay out of the bot's folder
    try:
        from alerts import AlertManager
        am = AlertManager(bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""), chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                          name="research_brief")
        return bool(am.send(text))
    finally:
        os.chdir(prev)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Morning research brief (read-only)")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(REPO))
    text = build()
    print(text)
    if a.send:
        print("telegram sent:", send(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
