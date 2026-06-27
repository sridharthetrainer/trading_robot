#!/usr/bin/env python3
"""
data_gap_filler.py — detect + backfill MISSING post-market EOD values.

Live fetches can miss values (rate-limit, outage, "Scanned:0", bot down). EOD/daily
values ARE recoverable post-market from NSE bhavcopy / archive. This detects
staleness in the key EOD stores and runs the EXISTING backfills to fill the gap,
so missed live values get reconstructed each night.

IMPORTANT: only EOD/DAILY granularity is recoverable this way. Intraday tick / OI
snapshot detail, if missed live, is NOT free-recoverable (needs a paid feed) — the
intraday_oi_logger must capture it live; this fills the daily layer.

Detect-only by default; `--fill` (or the nightly pipeline) runs the backfills.
Best-effort, read-only detection; never raises into a caller.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

def _fill_options_eod(start: date, end: date) -> Dict[str, Any]:
    import options_bhavcopy_backfill
    return options_bhavcopy_backfill.backfill(start, end)


def _fill_nifty_daily(start: date, end: date) -> Dict[str, Any]:
    # nifty_daily is populated by participant_oi_edge.ensure_nifty(conn, dates) —
    # NOT participant_oi_backfill (which only writes participant OI). Caught in audit.
    import participant_oi_edge
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    conn = sqlite3.connect("participant_oi.db")
    try:
        return {"nifty_rows": participant_oi_edge.ensure_nifty(conn, dates)}
    finally:
        conn.close()


# (store name, db file, last-date query, fill callable(start, end))
SOURCES = [
    {"name": "nifty_daily", "db": "participant_oi.db",
     "q": "SELECT MAX(date) FROM nifty_daily", "fill": _fill_nifty_daily},
    {"name": "options_eod (premia)", "db": "options_nifty.db",
     "q": "SELECT MAX(date) FROM options_eod WHERE underlying>0", "fill": _fill_options_eod},
]


def _last_date(db: str, q: str) -> Optional[str]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            v = conn.execute(q).fetchone()[0]
        finally:
            conn.close()
        return str(v) if v else None
    except Exception:
        return None


def freshness() -> List[Dict[str, Any]]:
    today = date.today()
    out = []
    for s in SOURCES:
        last = _last_date(s["db"], s["q"])
        stale = None
        if last:
            try:
                stale = (today - datetime.strptime(last[:10], "%Y-%m-%d").date()).days
            except Exception:
                pass
        out.append({"name": s["name"], "db": s["db"], "last": last, "stale_days": stale})
    return out


def fill(execute: bool = False, max_days: int = 45) -> List[Dict[str, Any]]:
    """For each stale EOD store, run its backfill from last+1 to today. execute=False
    reports what it WOULD do. Best-effort per source."""
    today = date.today()
    results = []
    for s in SOURCES:
        last = _last_date(s["db"], s["q"])
        sd = None
        if last:
            try:
                sd = (today - datetime.strptime(last[:10], "%Y-%m-%d").date()).days
            except Exception:
                pass
        info = {"name": s["name"], "db": s["db"], "last": last, "stale_days": sd}
        if sd is None:
            results.append({**info, "action": "unknown (no data / unreadable)"})
            continue
        if sd <= 1:
            results.append({**info, "action": "current"})
            continue
        start = today - timedelta(days=min(sd, max_days))
        if not execute:
            results.append({**info, "action": f"would backfill {start}..{today}"})
            continue
        try:
            r = s["fill"](start, today)
            results.append({**info, "action": "filled", "result": r})
        except Exception as exc:
            results.append({**info, "action": f"backfill failed: {str(exc)[:80]}"})
    return results


def summary() -> Dict[str, Any]:
    rows = freshness()
    stale = [r["name"] for r in rows if (r.get("stale_days") or 0) > 1]
    return {"sources": len(rows), "stale": stale, "detail": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect + backfill missing post-market EOD values")
    ap.add_argument("--fill", action="store_true", help="actually run the backfills (network)")
    args = ap.parse_args()
    rows = fill(execute=True) if args.fill else freshness()
    print("\nDATA GAP CHECK (EOD post-market backfill)")
    print("-" * 64)
    for r in rows:
        sd = r.get("stale_days")
        flag = "✅" if (sd is not None and sd <= 1) else "⚠️"
        print(f"  {flag} {r['name']:30s} last={r.get('last')}  stale_days={sd}  {r.get('action','')}")
    print("-" * 64)
    print("  Tip: run `python data_gap_filler.py --fill` to backfill, or let the nightly pipeline do it.")
    print("  NOTE: intraday tick/OI detail is NOT recoverable post-market — only EOD/daily.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
