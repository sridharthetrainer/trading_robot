#!/usr/bin/env python3
"""Label shadow option strike candidates using historical option EOD prices."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from option_decision_journal import DEFAULT_JOURNAL_FILE, label_option_shadow_decisions


OPTION_DB_CANDIDATES = (
    ("options_nifty.db", "options_eod"),
    ("historical_options.db", "options_eod"),
    ("historical_options.db", "options"),
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return float(default)


def _row_date(row: Dict[str, Any]) -> str:
    raw = str(row.get("time") or row.get("snapshot_time") or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return raw[:10] if len(raw) >= 10 else ""


def _underlying_root(symbol: str) -> str:
    raw = str(symbol or "").upper()
    out = ""
    for ch in raw:
        if ch.isalpha():
            out += ch
        else:
            break
    return out


def _option_store() -> tuple[str, str] | None:
    for db_path, table in OPTION_DB_CANDIDATES:
        path = Path(db_path)
        if not path.exists():
            continue
        try:
            with sqlite3.connect(path) as conn:
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            return db_path, table
        except Exception:
            continue
    return None


def _eod_close(
    *,
    conn: sqlite3.Connection,
    table: str,
    day: str,
    strike: float,
    opt_type: str,
    expiry: str = "",
) -> Optional[float]:
    params: List[Any] = [day, float(strike), str(opt_type).upper()]
    expiry_sql = ""
    if expiry:
        expiry_sql = "AND expiry=?"
        params.append(expiry)
    row = conn.execute(
        f"""
        SELECT close, settle
        FROM {table}
        WHERE date=? AND strike=? AND upper(opt_type)=? {expiry_sql}
        ORDER BY expiry ASC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    close = _safe_float(row[0], 0.0)
    settle = _safe_float(row[1], 0.0)
    return close if close > 0 else settle if settle > 0 else None


def _snapshot_close(
    *,
    day: str,
    underlying: str,
    strike: float,
    opt_type: str,
    snapshot_db: str = "option_chain_snapshots.db",
) -> Optional[float]:
    path = Path(snapshot_db)
    if not path.exists() or not day or not underlying:
        return None
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """
                SELECT rows_json
                FROM option_chain_snapshots
                WHERE upper(underlying)=?
                  AND ok=1
                  AND substr(snapshot_time, 1, 10)=?
                ORDER BY ts DESC
                LIMIT 12
                """,
                (str(underlying).upper(), day),
            ).fetchall()
    except Exception:
        return None
    prefix = str(opt_type or "").upper()
    for (raw_json,) in rows:
        try:
            chain_rows = json.loads(raw_json or "[]")
        except Exception:
            chain_rows = []
        if not isinstance(chain_rows, list):
            continue
        best = None
        best_dist = 10**9
        for item in chain_rows:
            if not isinstance(item, dict):
                continue
            row_strike = _safe_float(item.get("strikePrice") or item.get("strike"), 0.0)
            dist = abs(row_strike - float(strike))
            if row_strike > 0 and dist < best_dist:
                best = item
                best_dist = dist
        if not best or best_dist > 0.01:
            continue
        close = _safe_float(
            best.get(f"{prefix}_lastPrice")
            or best.get(f"{prefix}_LTP")
            or best.get(f"{prefix}_ltp"),
            0.0,
        )
        if close > 0:
            return close
    return None


def label_shadow_candidates_from_eod(
    *,
    journal_file: str = DEFAULT_JOURNAL_FILE,
    option_db: str = "",
    option_table: str = "",
    limit: int = 5000,
    dry_run: bool = False,
) -> Dict[str, Any]:
    store = (option_db, option_table) if option_db and option_table else _option_store()
    if not store:
        return {"ok": False, "reason": "no_historical_option_store"}
    db_path, table = store
    path = Path(journal_file)
    if not path.exists():
        return {"ok": False, "reason": "journal_missing"}

    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-int(limit):]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue

    eligible_rows = 0
    labelled = 0
    skipped = 0
    with sqlite3.connect(db_path) as conn:
        for row in rows:
            if str(row.get("decision", "")) != "selected":
                continue
            trade_id = str(row.get("trade_id") or row.get("source_id") or "")
            shadows = row.get("strikes", [])
            if not trade_id or not isinstance(shadows, list) or not shadows:
                continue
            day = _row_date(row)
            if not day:
                skipped += 1
                continue
            row_underlying = str(row.get("symbol") or "").upper()
            outcomes = []
            for candidate in shadows:
                if not isinstance(candidate, dict) or isinstance(candidate.get("shadow_outcome"), dict):
                    continue
                strike = _safe_float(candidate.get("strike"), 0.0)
                opt_type = str(candidate.get("option_type") or "").upper()
                entry = _safe_float(candidate.get("premium") or candidate.get("entry_price"), 0.0)
                expiry = str(candidate.get("expiry") or candidate.get("option_expiry") or "")
                if strike <= 0 or opt_type not in {"CE", "PE"} or entry <= 0:
                    skipped += 1
                    continue
                exit_price = _eod_close(
                    conn=conn,
                    table=table,
                    day=day,
                    strike=strike,
                    opt_type=opt_type,
                    expiry=expiry,
                )
                if exit_price is None:
                    underlying = (
                        _underlying_root(str(candidate.get("symbol") or ""))
                        or row_underlying
                    )
                    exit_price = _snapshot_close(
                        day=day,
                        underlying=underlying,
                        strike=strike,
                        opt_type=opt_type,
                    )
                if exit_price is None:
                    skipped += 1
                    continue
                pnl = float(exit_price) - float(entry)
                outcomes.append({
                    "symbol": candidate.get("symbol", ""),
                    "strike": strike,
                    "option_type": opt_type,
                    "pnl": round(pnl, 2),
                    "label": 1 if pnl > 0 else -1 if pnl < 0 else 0,
                    "exit_price": round(float(exit_price), 2),
                    "exit_reason": "historical_option_eod_or_snapshot_shadow_label",
                })
            if outcomes:
                eligible_rows += 1
                if not dry_run:
                    labelled += label_option_shadow_decisions(trade_id, outcomes, path=journal_file)
                else:
                    labelled += len(outcomes)

    if labelled and not dry_run:
        try:
            from option_strike_autotune import build_strike_autotune
            build_strike_autotune(journal_file=journal_file)
        except Exception:
            pass
    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "journal_file": journal_file,
        "option_db": db_path,
        "option_table": table,
        "eligible_rows": eligible_rows,
        "labelled_shadow": labelled,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", default=DEFAULT_JOURNAL_FILE)
    parser.add_argument("--option-db", default="")
    parser.add_argument("--option-table", default="")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = label_shadow_candidates_from_eod(
        journal_file=args.journal,
        option_db=args.option_db,
        option_table=args.option_table,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
