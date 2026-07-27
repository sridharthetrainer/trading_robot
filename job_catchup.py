"""
job_catchup.py — re-execute EXTERNAL (cron) jobs missed while the machine
was off (2026-07-10, operator-requested: "if any process is skipped, ensure
it's carried out during system-on mode; note which happened and which not;
re-execute on next run").

The IdleEngine handles its own 17 in-process tasks; this covers the jobs
that live in crontab and simply never fire when the box is off at their
slot:

  - post_market_ml (cron 16:30) — freshness marker: ml_pipeline_last_run.json
    timestamp. The pipeline is WINDOWED (trailing --days), so a late run
    still folds in every missed day's signals/labels — running it once on
    catch-up fully recovers a missed night.
  - condor_forward_test (cron 15:35) — freshness marker: its own state file
    mtime (it saves state on every step()).

Deliberately NOT caught up: open_health_check (09:25) — it validates
market-open conditions in the moment; running it late is meaningless.

Policy: catch-up only outside market hours (before 09:10 / after 16:35).
Booting mid-session with last night's ML run missed is fine to defer:
today's regular 16:30 cron covers it because the pipeline window includes
yesterday. Every catch-up execution is recorded in job_catchup_report.json
(same ledger the IdleEngine writes) so the operator can see what ran on
time, what was caught up, and when.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

REPORT_FILE = Path("job_catchup_report.json")
_LAST_ATTEMPT: Dict[str, float] = {}   # job -> ts, don't retry a failure more than hourly
_ATTEMPT_COOLDOWN_S = 3600


def _most_recent_due(now: datetime, hh: int, mm: int) -> datetime:
    slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return slot if now >= slot else slot - timedelta(days=1)


def _pipeline_last_run() -> Optional[datetime]:
    try:
        d = json.loads(Path("ml_pipeline_last_run.json").read_text())
        return datetime.fromisoformat(str(d.get("timestamp", "")))
    except Exception:
        return None


def _file_mtime(path: str) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except Exception:
        return None


JOBS = (
    {
        "name": "post_market_ml",
        "due": (16, 30),
        "last_run": _pipeline_last_run,
        "cmd": [sys.executable, "post_market_ml.py",
                "--days", "90", "--candle-days", "30", "--force"],
        # 1800s was set when typical runtime was ~400-800s; by 2026-07-27
        # real runs were taking 2536.8s (signals_used grew ~1.6x over the
        # same window while runtime grew ~6.8x -- per-run cost, not just data
        # volume, is what's growing). A catch-up attempt was now GUARANTEED
        # to time out even without the duplicate-run race the PID lock
        # (post_market_ml.py's own _acquire_lock/_release_lock) already
        # fixed. 3600s gives real headroom above the current worst case.
        "timeout": 3600,
    },
    {
        "name": "condor_forward_test",
        "due": (15, 35),
        "last_run": lambda: _file_mtime("condor_forward_test.json"),
        "cmd": [sys.executable, "condor_forward_test.py"],
        "timeout": 300,
    },
)


def _record(name: str, due_slot: datetime, status: str) -> None:
    try:
        rep = json.loads(REPORT_FILE.read_text()) if REPORT_FILE.exists() else {}
        rep.setdefault(due_slot.date().isoformat(), {})[name] = {
            "scheduled": due_slot.strftime("%H:%M"),
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "mode": status,
        }
        for old in sorted(rep)[:-14]:
            rep.pop(old, None)
        REPORT_FILE.write_text(json.dumps(rep, indent=2))
    except Exception as e:
        logger.debug("catchup record: %s", e)


def check_and_run_external(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Called every IdleEngine pass (cheap file checks). Runs at most one
    missed external job per call, serialized, never during market hours."""
    now = now or datetime.now()
    result: Dict[str, Any] = {}
    if now.weekday() < 5 and dtime(9, 10) <= now.time() <= dtime(16, 35):
        return result   # regular cron owns this window; never compete with it
    for job in JOBS:
        name = job["name"]
        due_slot = _most_recent_due(now, *job["due"])
        # 15-min grace so we never race the real cron at its own slot.
        if (now - due_slot).total_seconds() < 900:
            continue
        last = job["last_run"]()
        if last is not None and last >= due_slot:
            continue   # ran (or cron got it) — nothing missed
        if time.time() - _LAST_ATTEMPT.get(name, 0) < _ATTEMPT_COOLDOWN_S:
            continue
        _LAST_ATTEMPT[name] = time.time()
        logger.info("CATCH-UP external job %s (missed %s slot) …",
                    name, due_slot.strftime("%Y-%m-%d %H:%M"))
        try:
            proc = subprocess.run(job["cmd"], timeout=job["timeout"],
                                  capture_output=True, text=True)
            ok = proc.returncode == 0
            _record(name, due_slot, "caught_up" if ok else "catch_up_failed")
            result[name] = "caught_up" if ok else f"failed rc={proc.returncode}"
            if not ok:
                logger.warning("catch-up %s failed rc=%s: %s", name,
                               proc.returncode, (proc.stderr or "")[-200:])
        except subprocess.TimeoutExpired:
            _record(name, due_slot, "catch_up_timeout")
            result[name] = "timeout"
            logger.warning("catch-up %s timed out after %ss", name, job["timeout"])
        except Exception as e:
            result[name] = f"error {e}"
            logger.warning("catch-up %s: %s", name, e)
        break   # one heavy job per pass; next pass picks up the rest
    return result
