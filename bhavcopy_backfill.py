"""
bhavcopy_backfill.py — long-horizon NSE bhavcopy backfill (2026-07-29,
operator: "download all the available data, we will use for backtest").

bhavcopy_cache.download_bhavcopy() already downloads ONE day's NSE bhavcopy
correctly (now covering both the 2024+ flat-CSV format and the classic
pre-2024 per-day ZIP archive). download_last_n_days() loops backward but is
hardcoded to a 90-day cap. This module extends that into a resumable,
multi-year loop:

  - Resumable: skips any date already present in nse_cache.db (a date is
    "present" if the classic Nifty-anchor symbol RELIANCE has a row for it --
    cheap single-row check, avoids re-downloading a whole day already stored).
  - Polite: a delay between requests (default 1s) so a 10-year pull (~2,500
    trading days) doesn't hammer NSE's archive servers.
  - Trading-day aware: skips weekends and any date trading_calendar.py knows
    to be a holiday (unknown historical holidays are simply empty responses,
    handled gracefully by download_bhavcopy, not a correctness problem).

Standalone script, run on demand -- same precedent as candle_coverage_backfill.py
and the option-edge miners. Not wired into any nightly pipeline (a 10-year
pull is a one-time historical seed, not a recurring job).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

import bhavcopy_cache as bc

logger = logging.getLogger(__name__)

_ANCHOR_SYMBOL = "RELIANCE"  # listed for the entire lookback window


def _already_cached(conn: sqlite3.Connection, day: date) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ohlcv WHERE symbol=? AND date=? LIMIT 1",
        (_ANCHOR_SYMBOL, day.isoformat()),
    ).fetchone()
    return row is not None


def _is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    try:
        from trading_calendar import is_trading_day
        return is_trading_day(day)
    except Exception:
        return True


def backfill_years(
    years: int = 10,
    end: date | None = None,
    start: date | None = None,
    delay_sec: float = 1.0,
    progress_every: int = 50,
) -> Dict[str, Any]:
    """Backfill bhavcopy from (end - years) to end, skipping already-cached
    and non-trading days. Returns a summary dict. Pass `start` explicitly to
    override the years-back computation with a precise date range."""
    end = end or date.today()
    start = start or date(end.year - years, end.month, end.day)

    conn = bc._init_db()
    days_total = 0
    days_skipped_cached = 0
    days_skipped_non_trading = 0
    days_attempted = 0
    days_succeeded = 0
    days_empty = 0
    rows_stored = 0

    day = start
    while day <= end:
        days_total += 1
        if not _is_trading_day(day):
            days_skipped_non_trading += 1
            day += timedelta(days=1)
            continue
        if _already_cached(conn, day):
            days_skipped_cached += 1
            day += timedelta(days=1)
            continue

        days_attempted += 1
        try:
            n = bc.download_bhavcopy(day)
        except Exception as exc:
            logger.warning("bhavcopy backfill failed for %s: %s", day, exc)
            n = 0
        if n > 0:
            days_succeeded += 1
            rows_stored += n
        else:
            days_empty += 1

        if days_attempted % progress_every == 0:
            logger.info(
                "bhavcopy backfill progress: day=%s attempted=%d succeeded=%d "
                "empty=%d rows=%d", day, days_attempted, days_succeeded,
                days_empty, rows_stored,
            )
        time.sleep(delay_sec)
        day += timedelta(days=1)

    conn.close()
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "days_total": days_total,
        "days_skipped_cached": days_skipped_cached,
        "days_skipped_non_trading": days_skipped_non_trading,
        "days_attempted": days_attempted,
        "days_succeeded": days_succeeded,
        "days_empty": days_empty,
        "rows_stored": rows_stored,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--delay-sec", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
    result = backfill_years(years=args.years, delay_sec=args.delay_sec)
    print(result)
    Path("bhavcopy_backfill_report.json").write_text(__import__("json").dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
