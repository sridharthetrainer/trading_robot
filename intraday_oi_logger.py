#!/usr/bin/env python3
"""
intraday_oi_logger.py — FREE accrual of intraday options OI/greeks snapshots.

Purpose: the system already fetches the intraday option chain every cycle but
never STORES it. This persists periodic per-strike snapshots (near-ATM CE/PE OI,
LTP, IV + spot) to `intraday_oi_snapshots`, so the intraday OI-flow / dealer-gamma
hypotheses can be VALIDATED later (holdout + DSR) without paying for a data feed.
It just accrues data — it makes NO prediction and NO edge claim.

Reuses option_chain_fetcher (does not re-implement fetching). Best-effort and
throttled (default one snapshot / 15 min during market hours). Disable with
INTRADAY_OI_LOGGING=false; tune cadence with INTRADAY_OI_SNAPSHOT_MIN.

Usage:
    python intraday_oi_logger.py                 # one manual snapshot (live)
    python intraday_oi_logger.py --underlying BANKNIFTY
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Tuple

DB_PATH = "signal_log.db"
_last_snapshot_ts: Dict[str, float] = {}


def _cfg(name: str, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS intraday_oi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT,
            underlying TEXT,
            spot       REAL,
            expiry     TEXT,
            strike     REAL,
            ce_oi      REAL,
            pe_oi      REAL,
            ce_ltp     REAL,
            pe_ltp     REAL,
            ce_iv      REAL,
            pe_iv      REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_oi_snap_ts ON intraday_oi_snapshots(timestamp)"
    )
    conn.commit()


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def extract_rows(raw: Dict[str, Any], window_pct: float = 0.06) -> Tuple[float, str, List[tuple]]:
    """Pure parser: NSE-record raw -> (spot, expiry, [(strike,ce_oi,pe_oi,ce_ltp,pe_ltp,ce_iv,pe_iv)]).
    Keeps only strikes within window_pct of spot, nearest expiry. Testable offline."""
    rec = (raw or {}).get("records", {}) if isinstance(raw, dict) else {}
    data = rec.get("data", []) or []
    spot = _f(rec.get("underlyingValue"))
    expiries = rec.get("expiryDates", []) or []
    target_exp = expiries[0] if expiries else ""
    rows: List[tuple] = []
    if spot <= 0 or not data:
        return spot, target_exp, rows
    lo, hi = spot * (1 - window_pct), spot * (1 + window_pct)
    for d in data:
        if not isinstance(d, dict):
            continue
        if target_exp and d.get("expiryDate") and d.get("expiryDate") != target_exp:
            continue
        strike = _f(d.get("strikePrice"))
        if strike <= 0 or not (lo <= strike <= hi):
            continue
        ce = d.get("CE", {}) or {}
        pe = d.get("PE", {}) or {}
        rows.append((
            strike,
            _f(ce.get("openInterest")), _f(pe.get("openInterest")),
            _f(ce.get("lastPrice")),   _f(pe.get("lastPrice")),
            _f(ce.get("impliedVolatility")), _f(pe.get("impliedVolatility")),
        ))
    return spot, target_exp, rows


def snapshot(underlying: str = "NIFTY", db_path: str = DB_PATH,
             window_pct: float = 0.06) -> int:
    """Fetch the live chain and persist a snapshot. Returns rows written (0 on
    failure / no live data). Best-effort — never raises into the caller."""
    try:
        from option_chain_fetcher import NSEOptionChainFetcher
        raw = NSEOptionChainFetcher(underlying=underlying).fetch()
        if not raw:
            return 0
        spot, expiry, rows = extract_rows(raw, window_pct=window_pct)
        if not rows:
            return 0
        ts = datetime.now().isoformat(timespec="seconds")
        conn = sqlite3.connect(db_path)
        try:
            _ensure_table(conn)
            conn.executemany(
                "INSERT INTO intraday_oi_snapshots "
                "(timestamp, underlying, spot, expiry, strike, ce_oi, pe_oi, ce_ltp, pe_ltp, ce_iv, pe_iv) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(ts, underlying, spot, expiry, *r) for r in rows],
            )
            conn.commit()
        finally:
            conn.close()
        return len(rows)
    except Exception:
        return 0


def _market_open_now() -> bool:
    now = datetime.now().time()
    return dtime(9, 15) <= now <= dtime(15, 30)


def maybe_snapshot(underlying: str = "NIFTY") -> int:
    """Throttled, market-hours-gated snapshot for the live loop. Best-effort."""
    if not bool(_cfg("INTRADAY_OI_LOGGING", True)):
        return 0
    if not _market_open_now():
        return 0
    every = max(1, int(_cfg("INTRADAY_OI_SNAPSHOT_MIN", 15))) * 60
    if time.time() - _last_snapshot_ts.get(underlying, 0.0) < every:
        return 0
    n = snapshot(underlying)
    if n > 0:
        _last_snapshot_ts[underlying] = time.time()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Intraday OI snapshot logger (free accrual)")
    ap.add_argument("--underlying", default="NIFTY")
    args = ap.parse_args()
    n = snapshot(args.underlying)
    print(f"snapshot wrote {n} strike rows for {args.underlying} "
          f"({'live data' if n else 'no live data — market closed or fetch failed'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
