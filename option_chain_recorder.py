#!/usr/bin/env python3
"""
option_chain_recorder.py

Persist option-chain snapshots for EOD option-bot learning.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime, time as dtime
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

DB_PATH = "option_chain_snapshots.db"


def _conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            ts REAL NOT NULL,
            snapshot_time TEXT NOT NULL,
            underlying TEXT NOT NULL,
            spot REAL DEFAULT 0,
            expiry TEXT DEFAULT '',
            atm_strike REAL DEFAULT 0,
            pcr_oi REAL DEFAULT 0,
            pcr_change_oi REAL DEFAULT 0,
            max_pain REAL DEFAULT 0,
            ok INTEGER DEFAULT 1,
            reason TEXT DEFAULT '',
            rows_json TEXT DEFAULT '[]',
            summary_json TEXT DEFAULT '{}',
            source TEXT DEFAULT '',
            is_live INTEGER DEFAULT 0,
            provider_request_id TEXT DEFAULT ''
        )
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(option_chain_snapshots)").fetchall()}
    if "ok" not in cols:
        conn.execute("ALTER TABLE option_chain_snapshots ADD COLUMN ok INTEGER DEFAULT 1")
    if "reason" not in cols:
        conn.execute("ALTER TABLE option_chain_snapshots ADD COLUMN reason TEXT DEFAULT ''")
    if "source" not in cols:
        conn.execute("ALTER TABLE option_chain_snapshots ADD COLUMN source TEXT DEFAULT ''")
    if "is_live" not in cols:
        conn.execute("ALTER TABLE option_chain_snapshots ADD COLUMN is_live INTEGER DEFAULT 0")
    if "provider_request_id" not in cols:
        conn.execute("ALTER TABLE option_chain_snapshots ADD COLUMN provider_request_id TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oc_snap_u_ts ON option_chain_snapshots(underlying, ts)")
    conn.commit()
    return conn


def _chain_rows(df) -> List[Dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    cols = list(getattr(df, "columns", []))
    keep = [
        c for c in cols
        if str(c) in {
            "strikePrice", "expiryDate", "CE_OI", "PE_OI", "CE_CHG_OI", "PE_CHG_OI",
            "CE_LTP", "PE_LTP", "CE_IV", "PE_IV", "CE_VOLUME", "PE_VOLUME",
            "CE_openInterest", "PE_openInterest",
            "CE_changeinOpenInterest", "PE_changeinOpenInterest",
            "CE_totalTradedVolume", "PE_totalTradedVolume",
            "CE_lastPrice", "PE_lastPrice",
            "CE_impliedVolatility", "PE_impliedVolatility",
            "CE_bidPrice", "PE_bidPrice", "CE_bidQty", "PE_bidQty",
            "CE_askPrice", "PE_askPrice", "CE_askQty", "PE_askQty",
            "CE_delta", "PE_delta", "CE_theta", "PE_theta",
            "distance_from_atm", "gamma_approx",
        }
    ]
    try:
        return df[keep].head(80).to_dict("records")
    except Exception:
        return []


def record_option_chain_snapshot(
    underlying: str,
    *,
    db_path: str = DB_PATH,
    insert_failure: bool = True,
) -> Dict[str, Any]:
    underlying = str(underlying or "").upper()
    fetcher = None
    try:
        from option_chain_fetcher import NSEOptionChainFetcher
        fetcher = NSEOptionChainFetcher(underlying=underlying)
        result = fetcher.fetch_and_analyze()
    except Exception as exc:
        result = None
        fetch_error = str(exc)
    else:
        fetch_error = ""
    if not result:
        reason = fetch_error or "no_option_chain"
        if not insert_failure:
            return {"underlying": underlying, "ok": False, "skipped": True, "reason": reason}
        conn = _conn(db_path)
        conn.execute(
            """
            INSERT INTO option_chain_snapshots
            (ts, snapshot_time, underlying, spot, expiry, atm_strike,
             pcr_oi, pcr_change_oi, max_pain, ok, reason, rows_json, summary_json,
             source, is_live, provider_request_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                time.time(),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                underlying,
                0.0,
                "",
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                reason,
                "[]",
                "{}",
                "",
                0,
                "",
            ),
        )
        conn.commit()
        conn.close()
        return {"underlying": underlying, "ok": False, "reason": reason}

    summary = result.summary or {}
    rows = _chain_rows(result.dataframe)
    ok = bool(rows)
    reason = "" if ok else "empty_chain_rows"
    # Only count chains from a LIVE source as ok — a stale-cache fallback has rows
    # but must not be recorded as a valid live snapshot (downstream flow/worthiness
    # queries filter on ok=1). Source comes from the fetcher's last_source.
    _src = str(getattr(fetcher, "last_source", "") or "")
    is_live = bool(ok and _is_live_source(_src))
    if ok and not is_live:
        ok = False
        reason = f"non_live_source:{_src or 'unknown'}"
    payload = {
        "underlying": underlying,
        "ok": ok,
        "reason": reason,
        "spot": float(result.spot or 0),
        "expiry": str(result.expiry or ""),
        "atm_strike": float(result.atm_strike or 0),
        "rows": len(rows),
        "summary": summary,
    }
    snap_ts = time.time()
    snap_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    conn = _conn(db_path)
    conn.execute(
        """
        INSERT INTO option_chain_snapshots
        (ts, snapshot_time, underlying, spot, expiry, atm_strike,
         pcr_oi, pcr_change_oi, max_pain, ok, reason, rows_json, summary_json,
         source, is_live, provider_request_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            snap_ts,
            snap_time,
            underlying,
            payload["spot"],
            payload["expiry"],
            payload["atm_strike"],
            float(summary.get("pcr_oi", 0) or 0),
            float(summary.get("pcr_change_oi", 0) or 0),
            float(summary.get("max_pain", 0) or 0),
            1 if ok else 0,
            reason,
            json.dumps(rows, default=str),
            json.dumps(summary, default=str),
            _src,
            1 if is_live else 0,
            str(getattr(fetcher, "last_request_id", "") or ""),
        ),
    )
    conn.commit()
    # Per-strike CE/PE flow signals: compare this LIVE snapshot to the previous
    # one and persist ranked LONG_BUILDUP/SHORT_COVERING signals. Best-effort —
    # never let flow computation break snapshot recording.
    if ok:
        try:
            from option_multistrike_signals import persist_multistrike_signals
            persist_multistrike_signals(
                conn=conn,
                snapshot_time=snap_time,
                underlying=underlying,
                expiry=payload["expiry"],
                current_rows=rows,
                source=_src or "nse_live",
            )
            conn.commit()
        except Exception as exc:
            logger.debug("multistrike flow persist failed for %s: %s", underlying, exc, exc_info=True)
    conn.close()
    return payload


def record_option_chains(
    underlyings: List[str] | None = None,
    *,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    conn = _conn(db_path)
    conn.close()
    if underlyings is None:
        try:
            from live_signal_engine import SUPPORTED_OPTION_UNDERLYINGS
            underlyings = sorted(SUPPORTED_OPTION_UNDERLYINGS)
        except Exception:
            underlyings = ["NIFTY", "BANKNIFTY", "SENSEX"]
    results = []
    for underlying in underlyings:
        symbol = str(underlying or "").upper()
        # BSE option chains can be intermittently unavailable via public APIs.
        # Avoid filling the learning DB with repeated SENSEX/BANKEX failure rows;
        # successful BSE rows are still recorded when the fallback returns data.
        results.append(record_option_chain_snapshot(
            symbol,
            db_path=db_path,
            insert_failure=symbol not in {"SENSEX", "BANKEX"},
        ))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "requested": len(results),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "skipped_count": sum(1 for r in results if r.get("skipped")),
        "results": results,
    }


def _load_trading_holidays() -> set:
    """NSE holiday dates ('YYYY-MM-DD') from trading_holidays.json. Best-effort."""
    try:
        import json as _j
        with open("trading_holidays.json") as f:
            data = _j.load(f)
        if isinstance(data, dict):
            data = data.get("holidays") or data.get("dates") or []
        return {str(d)[:10] for d in (data or [])}
    except Exception:
        return set()


def _in_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:                                  # weekend
        return False
    if now.strftime("%Y-%m-%d") in _load_trading_holidays():  # NSE holiday
        return False
    return dtime(9, 15) <= now.time() <= dtime(15, 35)


# Only these sources are LIVE chain data — anything else (cache aliases, unknown
# resilience tags) must NOT be persisted as a live snapshot.
_LIVE_SOURCES = {"nse_live", "angel", "angel_eod", "angel_fallback", "upstox_live",
                 "dhan_live", "bse_oc", "bse"}


def _is_live_source(src) -> bool:
    return str(src or "").lower() in _LIVE_SOURCES


def run_snapshot_loop(
    underlyings: List[str],
    *,
    interval_sec: int = 300,
    once: bool = False,
    market_hours_only: bool = True,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    interval_sec = max(60, int(interval_sec or 300))
    rounds = 0
    ok_total = 0
    last: Dict[str, Any] = {}
    while True:
        if market_hours_only and not _in_market_hours():
            last = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "ok": False,
                "reason": "outside_market_hours",
                "rounds": rounds,
                "ok_total": ok_total,
            }
            if once:
                return last
            time.sleep(min(interval_sec, 300))
            continue

        last = record_option_chains(underlyings, db_path=db_path)
        rounds += 1
        ok_total += int(last.get("ok_count", 0) or 0)
        last.update({"rounds": rounds, "ok_total": ok_total})
        print(json.dumps(last, default=str), flush=True)
        if once:
            return last
        time.sleep(interval_sec)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--underlyings", default="NIFTY,BANKNIFTY,FINNIFTY,SENSEX")
    parser.add_argument("--loop", action="store_true", help="Keep recording snapshots every interval")
    parser.add_argument("--interval-sec", type=int, default=300)
    parser.add_argument("--allow-after-hours", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    underlyings = [u.strip().upper() for u in args.underlyings.split(",") if u.strip()]
    if args.loop:
        run_snapshot_loop(
            underlyings,
            interval_sec=args.interval_sec,
            once=False,
            market_hours_only=not args.allow_after_hours,
        )
        return 0
    result = run_snapshot_loop(
        underlyings,
        interval_sec=args.interval_sec,
        once=True,
        market_hours_only=not args.allow_after_hours,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
