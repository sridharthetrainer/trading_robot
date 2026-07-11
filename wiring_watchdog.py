"""
wiring_watchdog.py — automated detector for the silent-default bug class
(2026-07-11, operator-approved after the week's audits).

Nearly every bug found in the 2026-07-07..07-11 audits was the same species:
a value computed then dropped, a .get(col, default) masking missing data, a
gate no code could open, a job that silently never fired. Each was found by
hand with the same techniques; this module runs them nightly:

  1. CONSTANT-COLUMN SWEEP — any signal_log column with <=1 distinct value
     over the trailing window, diffed against a persisted baseline of
     known-dead columns. A NEWLY dead column (was alive, went constant)
     alerts immediately — this catches "computed but never wired" in one
     day instead of ten. A REVIVED column (was dead, now varies) is logged
     and removed from the baseline — this is how the 07-10 fixes (news,
     sector, IV percentile, PCR, ...) get positively verified.
  2. ARTIFACT FRESHNESS — every scheduled output file has an expected
     update cadence; stale means its producer silently stopped (the OI
     tracker sat frozen for a MONTH before anyone noticed). Max ages are
     weekend-tolerant.

Report: wiring_watchdog_report.json. Alerts (best-effort Telegram) only on
regressions — new dead columns or newly stale artifacts — never on the
steady state. Wired into post_market_ml nightly.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

BASELINE_FILE = Path("wiring_watchdog_baseline.json")
REPORT_FILE = Path("wiring_watchdog_report.json")
SWEEP_DAYS = int(os.getenv("WIRING_WATCHDOG_DAYS", "5"))
MIN_ROWS = int(os.getenv("WIRING_WATCHDOG_MIN_ROWS", "300"))

# Expected update cadence per scheduled artifact, in hours. Generous enough
# to span a weekend (Fri 15:30 -> Mon evening ~ 78h) without false alarms.
ARTIFACTS: Dict[str, float] = {
    "ml_pipeline_last_run.json": 80,
    "learned_filters.json": 80,
    "learned_filter_ledger.json": 80,
    "modifier_edge_report.json": 80,
    "edge_analysis_last_run.json": 80,
    "daily_report.json": 80,
    "oi_tracker_state.json": 100,          # written only during market hours
    "job_catchup_report.json": 100,
    "option_strike_autotune.json": 80,
}


def _constant_columns(days: int = SWEEP_DAYS) -> Dict[str, Any]:
    """signal_log columns with <=1 distinct value over the trailing window."""
    import sqlite3
    out: Dict[str, Any] = {"checked": 0, "rows": 0, "dead": []}
    try:
        conn = sqlite3.connect("signal_log.db")
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        n = conn.execute(
            "SELECT COUNT(*) FROM signal_log WHERE signal_date >= ?", (cutoff,)
        ).fetchone()[0]
        out["rows"] = int(n)
        if n < MIN_ROWS:
            out["skipped"] = f"only {n} rows in window (need {MIN_ROWS})"
            conn.close()
            return out
        cols = [r[1] for r in conn.execute("PRAGMA table_info(signal_log)").fetchall()]
        dead = []
        for col in cols:
            try:
                d = conn.execute(
                    f'SELECT COUNT(DISTINCT "{col}") FROM signal_log '
                    "WHERE signal_date >= ?", (cutoff,)).fetchone()[0]
                if d <= 1:
                    dead.append(col)
            except Exception:
                continue
        conn.close()
        out["checked"] = len(cols)
        out["dead"] = sorted(dead)
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.debug("constant sweep: %s", e)
    return out


def _artifact_staleness() -> List[Dict[str, Any]]:
    stale = []
    now = time.time()
    for name, max_h in ARTIFACTS.items():
        p = Path(name)
        if not p.exists():
            stale.append({"file": name, "status": "missing"})
            continue
        age_h = (now - p.stat().st_mtime) / 3600.0
        if age_h > max_h:
            stale.append({"file": name, "status": "stale",
                          "age_hours": round(age_h, 1), "max_hours": max_h})
    return stale


def _send_alert(text: str) -> None:
    try:
        from alerts import AlertManager
        am = AlertManager(bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                          chat_id=os.getenv("TELEGRAM_CHAT_ID", ""))
        am.send(text, dedup_key=f"wiring:{datetime.now():%Y-%m-%d}",
                cooldown=6 * 3600)
    except Exception as e:
        logger.debug("wiring alert send: %s", e)


def run(send_telegram: bool = True) -> Dict[str, Any]:
    """Nightly entry point (post_market_ml). Alerts only on regressions."""
    sweep = _constant_columns()
    current_dead = set(sweep.get("dead", []))

    baseline: Dict[str, Any] = {}
    try:
        if BASELINE_FILE.exists():
            baseline = json.loads(BASELINE_FILE.read_text())
    except Exception as e:
        logger.debug("baseline load: %s", e)
    known_dead = set(baseline.get("dead_columns", []))

    first_run = not baseline
    new_dead = sorted(current_dead - known_dead) if not first_run else []
    revived = sorted(known_dead - current_dead) if (not first_run and not sweep.get("skipped")) else []

    # Persist updated baseline: today's dead set becomes the new baseline
    # (so a regression alerts once, then becomes known until it revives).
    if not sweep.get("skipped") and not sweep.get("error"):
        try:
            BASELINE_FILE.write_text(json.dumps({
                "dead_columns": sorted(current_dead),
                "updated": datetime.now().isoformat(timespec="seconds"),
            }, indent=2))
        except Exception as e:
            logger.debug("baseline write: %s", e)

    stale = _artifact_staleness()
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sweep_rows": sweep.get("rows"),
        "sweep_days": SWEEP_DAYS,
        "columns_checked": sweep.get("checked"),
        "dead_columns_now": sorted(current_dead),
        "new_dead_columns": new_dead,
        "revived_columns": revived,
        "stale_artifacts": stale,
        "first_run_baseline": first_run,
        "ok": not new_dead and not stale,
    }
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as e:
        logger.debug("report write: %s", e)

    if revived:
        logger.info("wiring watchdog: %d column(s) REVIVED (fixes verified): %s",
                    len(revived), ", ".join(revived[:10]))
    if new_dead or stale:
        lines = ["🔌 <b>WIRING WATCHDOG</b>"]
        if new_dead:
            lines.append(f"  ❌ newly DEAD columns ({len(new_dead)}): "
                         + ", ".join(new_dead[:8]))
        for s in stale[:6]:
            lines.append(f"  ⏳ {s['file']}: {s['status']}"
                         + (f" ({s.get('age_hours')}h > {s.get('max_hours')}h)"
                            if s.get("age_hours") else ""))
        lines.append("  A producer silently stopped — same bug class as the "
                     "July audit finds.")
        msg = "\n".join(lines)
        logger.warning("wiring watchdog: %d new dead, %d stale artifacts",
                       len(new_dead), len(stale))
        if send_telegram:
            _send_alert(msg)
    else:
        logger.info("wiring watchdog: OK (%d known-dead cols, 0 regressions, "
                    "0 stale artifacts)", len(current_dead))
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    print(json.dumps(run(send_telegram=False), indent=2))
