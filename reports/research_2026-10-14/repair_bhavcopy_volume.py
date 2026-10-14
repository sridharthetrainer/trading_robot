"""Re-download every bhavcopy day lacking NSE's prev_close (which also restores
volume stored as 0), then fill the 2026 gap.

One-off repair after the TTL_TRD_QNTY fix (commit 7d1e1db3) and the
prev_close capture added alongside it. download_bhavcopy
uses INSERT OR REPLACE, so a re-download rewrites the same prices with real
volume. Run from the repo root:
    .venv/bin/python reports/research_2026-10-14/repair_bhavcopy_volume.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bhavcopy_backfill  # noqa: E402
import bhavcopy_cache as bc  # noqa: E402


def zero_volume_days() -> list[date]:
    bc._init_db().close()  # make sure the prev_close table exists
    conn = sqlite3.connect(str(ROOT / "nse_cache.db"))
    rows = conn.execute(
        "SELECT DISTINCT date FROM ohlcv WHERE date NOT IN (SELECT DISTINCT date FROM prev_close) "
        "ORDER BY date").fetchall()
    conn.close()
    return [date.fromisoformat(r[0]) for r in rows]


def main() -> int:
    days = zero_volume_days()
    fixed, failed = 0, []
    for i, day in enumerate(days, 1):
        try:
            n = bc.download_bhavcopy(day)
        except Exception as exc:  # keep going; report at the end
            n, _ = 0, exc
        if n > 0:
            fixed += 1
        else:
            failed.append(day.isoformat())
        if i % 50 == 0:
            print(f"{i}/{len(days)} re-downloaded, fixed={fixed} failed={len(failed)}", flush=True)
        time.sleep(1.0)
    gap = bhavcopy_backfill.backfill_years(start=date(2026, 7, 30), end=date.today(), delay_sec=1.0)
    report = {"zero_volume_days": len(days), "fixed": fixed, "failed": failed, "gap_fill": gap,
              "days_still_missing_prev_close": len(zero_volume_days())}
    (Path(__file__).resolve().parent / "repair_bhavcopy_volume_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: (v if k != "failed" else len(v)) for k, v in report.items()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
