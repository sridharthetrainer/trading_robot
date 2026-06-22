#!/usr/bin/env python3
"""
option_historical_replay_labeller.py

Build selected/shadow option training labels from historical EOD option data.

This is not live execution history. Rows are marked as historical replay and are
used only to warm up strike autotune when the live journal has too few labelled
option decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from option_decision_journal import DEFAULT_JOURNAL_FILE, load_recent_option_decisions, record_option_decision
from option_strike_autotune import build_strike_autotune


REPORT_FILE = "option_historical_replay_report.json"
DEFAULT_DB = os.getenv("OPTION_HISTORICAL_REPLAY_DB", "options_nifty.db")
DEFAULT_TABLE = os.getenv("OPTION_HISTORICAL_REPLAY_TABLE", "options_eod")
DEFAULT_ROOT = os.getenv("OPTION_HISTORICAL_REPLAY_ROOT", "NIFTY").upper()
DEFAULT_MAX_ROWS = int(os.getenv("OPTION_HISTORICAL_REPLAY_MAX_ROWS", "80"))
DEFAULT_LOOKBACK_DATES = int(os.getenv("OPTION_HISTORICAL_REPLAY_LOOKBACK_DATES", "45"))
DEFAULT_MAX_PER_DATE = int(os.getenv("OPTION_HISTORICAL_REPLAY_MAX_PER_DATE", "6"))
DEFAULT_MIN_OI = float(os.getenv("OPTION_HISTORICAL_REPLAY_MIN_OI", "1000"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _date_diff_days(start: str, end: str) -> int:
    try:
        return max(0, (datetime.strptime(end[:10], "%Y-%m-%d") - datetime.strptime(start[:10], "%Y-%m-%d")).days)
    except Exception:
        return 0


def _price(close: Any, settle: Any) -> float:
    close_f = _safe_float(close, 0.0)
    settle_f = _safe_float(settle, 0.0)
    return close_f if close_f > 0 else settle_f if settle_f > 0 else 0.0


def _existing_sources(path: str) -> Set[str]:
    out: Set[str] = set()
    for row in load_recent_option_decisions(path=path, limit=300000):
        source_id = str(row.get("source_id") or "").strip()
        if source_id:
            out.add(source_id)
    return out


def _style_for_dte(dte: int) -> str:
    if dte <= 1:
        return "scalping"
    if dte <= 7:
        return "intraday"
    if dte <= 35:
        return "swing"
    return "position"


def _strike_type(strike: float, opt_type: str, spot: float) -> str:
    if spot <= 0:
        return "UNKNOWN"
    dist_pct = abs(strike - spot) / max(spot, 1.0) * 100.0
    if dist_pct <= 0.35:
        return "ATM"
    if opt_type == "CE":
        return "ITM" if strike < spot else "OTM"
    if opt_type == "PE":
        return "ITM" if strike > spot else "OTM"
    return "UNKNOWN"


def _option_symbol(root: str, expiry: str, strike: float, opt_type: str) -> str:
    compact_expiry = str(expiry or "").replace("-", "")[2:]
    return f"{root}{compact_expiry}{int(float(strike))}{str(opt_type).upper()}"


def _latest_dates(conn: sqlite3.Connection, table: str, lookback_dates: int) -> List[str]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT date
        FROM {table}
        WHERE date IS NOT NULL
        ORDER BY date DESC
        LIMIT ?
        """,
        (int(lookback_dates),),
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _next_date(conn: sqlite3.Connection, table: str, day: str) -> Optional[str]:
    row = conn.execute(
        f"SELECT MIN(date) FROM {table} WHERE date > ?",
        (day,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _shadow_candidates(
    conn: sqlite3.Connection,
    *,
    table: str,
    root: str,
    day: str,
    next_day: str,
    expiry: str,
    selected_strike: float,
    opt_type: str,
    spot: float,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT e.strike, e.close, e.settle, e.oi, n.close, n.settle
        FROM {table} e
        JOIN {table} n
          ON n.date=?
         AND n.expiry=e.expiry
         AND n.strike=e.strike
         AND upper(n.opt_type)=upper(e.opt_type)
        WHERE e.date=?
          AND e.expiry=?
          AND upper(e.opt_type)=?
          AND e.strike != ?
          AND COALESCE(e.close, e.settle, 0) > 0
          AND COALESCE(n.close, n.settle, 0) > 0
        ORDER BY ABS(e.strike - ?), COALESCE(e.oi, 0) DESC
        LIMIT ?
        """,
        (next_day, day, expiry, opt_type, float(selected_strike), float(selected_strike), int(limit)),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for strike, close, settle, oi, exit_close, exit_settle in rows:
        entry = _price(close, settle)
        exit_price = _price(exit_close, exit_settle)
        if entry <= 0 or exit_price <= 0:
            continue
        pnl = exit_price - entry
        strike_f = _safe_float(strike, 0.0)
        out.append({
            "symbol": _option_symbol(root, expiry, strike_f, opt_type),
            "strike": strike_f,
            "option_type": opt_type,
            "premium": round(entry, 2),
            "entry_price": round(entry, 2),
            "exit_price": round(exit_price, 2),
            "oi": _safe_float(oi, 0.0),
            "spot": round(spot, 2) if spot > 0 else 0.0,
            "dte": _date_diff_days(day, expiry),
            "expiry": expiry,
            "strike_type": _strike_type(strike_f, opt_type, spot),
            "shadow": True,
            "synthetic_shadow": False,
            "entry_source": "historical_option_eod_replay",
            "shadow_outcome": {
                "label": 1 if pnl > 0 else -1 if pnl < 0 else 0,
                "pnl": round(pnl, 2),
                "exit_price": round(exit_price, 2),
                "exit_reason": "historical_option_eod_replay_next_session",
            },
        })
    return out


def run_historical_option_replay(
    *,
    db_path: str = DEFAULT_DB,
    table: str = DEFAULT_TABLE,
    journal_file: str = DEFAULT_JOURNAL_FILE,
    root: str = DEFAULT_ROOT,
    max_rows: int = DEFAULT_MAX_ROWS,
    lookback_dates: int = DEFAULT_LOOKBACK_DATES,
    max_per_date: int = DEFAULT_MAX_PER_DATE,
    min_oi: float = DEFAULT_MIN_OI,
    dry_run: bool = False,
) -> Dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {"ok": False, "reason": "historical_option_db_missing", "db_path": db_path}

    existing = _existing_sources(journal_file)
    written = 0
    skipped_existing = 0
    skipped_no_exit = 0
    dates_seen = 0
    candidates_seen = 0
    shadow_written = 0
    type_counts: Dict[str, int] = {}
    style_counts: Dict[str, int] = {}

    with sqlite3.connect(path) as conn:
        dates = _latest_dates(conn, table, lookback_dates)
        for day in sorted(dates, reverse=True):
            if written >= max_rows:
                break
            next_day = _next_date(conn, table, day)
            if not next_day:
                continue
            dates_seen += 1
            rows = conn.execute(
                f"""
                SELECT e.date, e.expiry, e.strike, upper(e.opt_type), e.close, e.settle,
                       e.oi, e.underlying, n.close, n.settle
                FROM {table} e
                JOIN {table} n
                  ON n.date=?
                 AND n.expiry=e.expiry
                 AND n.strike=e.strike
                 AND upper(n.opt_type)=upper(e.opt_type)
                WHERE e.date=?
                  AND COALESCE(e.close, e.settle, 0) > 0
                  AND COALESCE(n.close, n.settle, 0) > 0
                  AND COALESCE(e.oi, 0) >= ?
                  AND e.expiry >= e.date
                  AND upper(e.opt_type) IN ('CE', 'PE')
                ORDER BY COALESCE(e.oi, 0) DESC, e.expiry ASC
                LIMIT 300
                """,
                (next_day, day, float(min_oi)),
            ).fetchall()
            per_date = 0
            per_type = {"CE": 0, "PE": 0}
            for row in rows:
                if written >= max_rows or per_date >= max_per_date:
                    break
                date, expiry, strike, opt_type, close, settle, oi, spot_raw, exit_close, exit_settle = row
                opt_type = str(opt_type or "").upper()
                if per_type.get(opt_type, 0) >= max(1, max_per_date // 2):
                    continue
                entry = _price(close, settle)
                exit_price = _price(exit_close, exit_settle)
                if entry <= 0 or exit_price <= 0:
                    skipped_no_exit += 1
                    continue
                source_id = f"historical_option_replay:{date}:{expiry}:{float(strike):.2f}:{opt_type}"
                if source_id in existing:
                    skipped_existing += 1
                    continue
                candidates_seen += 1
                strike_f = _safe_float(strike, 0.0)
                spot = _safe_float(spot_raw, 0.0)
                dte = _date_diff_days(str(date), str(expiry))
                style = _style_for_dte(dte)
                pnl = exit_price - entry
                selected = {
                    "symbol": _option_symbol(root, str(expiry), strike_f, opt_type),
                    "strike": strike_f,
                    "option_type": opt_type,
                    "premium": round(entry, 2),
                    "entry_price": round(entry, 2),
                    "exit_price": round(exit_price, 2),
                    "oi": _safe_float(oi, 0.0),
                    "spot": round(spot, 2) if spot > 0 else 0.0,
                    "dte": dte,
                    "expiry": str(expiry),
                    "style": style,
                    "strike_type": _strike_type(strike_f, opt_type, spot),
                    "entry_source": "historical_option_eod_replay",
                }
                shadows = _shadow_candidates(
                    conn,
                    table=table,
                    root=root,
                    day=str(date),
                    next_day=str(next_day),
                    expiry=str(expiry),
                    selected_strike=strike_f,
                    opt_type=opt_type,
                    spot=spot,
                )
                shadow_written += len(shadows)
                if not dry_run:
                    record_option_decision(
                        strategy=f"historical_replay_{style}",
                        symbol=root,
                        decision="selected",
                        reason="historical_option_eod_replay",
                        side="BUY",
                        spot=spot,
                        setup_score=6.5,
                        quality={
                            "replay": True,
                            "min_oi": float(min_oi),
                            "entry_date": str(date),
                            "exit_date": str(next_day),
                            "oi": _safe_float(oi, 0.0),
                        },
                        selected=selected,
                        strikes=shadows,
                        source_id=source_id,
                        outcome_label=1 if pnl > 0 else -1 if pnl < 0 else 0,
                        pnl=round(pnl, 2),
                        outcome={
                            "label": 1 if pnl > 0 else -1 if pnl < 0 else 0,
                            "pnl": round(pnl, 2),
                            "entry_price": round(entry, 2),
                            "exit_price": round(exit_price, 2),
                            "entry_date": str(date),
                            "exit_date": str(next_day),
                            "exit_reason": "historical_option_eod_replay_next_session",
                        },
                        metadata={
                            "historical_replay": True,
                            "db_path": db_path,
                            "table": table,
                            "root": root,
                        },
                        path=journal_file,
                    )
                    existing.add(source_id)
                written += 1
                per_date += 1
                per_type[opt_type] = per_type.get(opt_type, 0) + 1
                type_counts[opt_type] = type_counts.get(opt_type, 0) + 1
                style_counts[style] = style_counts.get(style, 0) + 1

    model: Dict[str, Any] = {}
    if not dry_run and written:
        model = build_strike_autotune(journal_file=journal_file)

    result = {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_path": db_path,
        "table": table,
        "journal_file": journal_file,
        "root": root,
        "dry_run": bool(dry_run),
        "dates_seen": dates_seen,
        "candidates_seen": candidates_seen,
        "written": written,
        "shadow_outcomes": shadow_written,
        "skipped_existing": skipped_existing,
        "skipped_no_exit": skipped_no_exit,
        "type_counts": type_counts,
        "style_counts": style_counts,
        "autotune_labelled_selected": model.get("labelled_selected", 0) if model else None,
        "autotune_labelled_shadow": model.get("labelled_shadow", 0) if model else None,
    }
    if not dry_run:
        Path(REPORT_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--journal", default=DEFAULT_JOURNAL_FILE)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--lookback-dates", type=int, default=DEFAULT_LOOKBACK_DATES)
    parser.add_argument("--max-per-date", type=int, default=DEFAULT_MAX_PER_DATE)
    parser.add_argument("--min-oi", type=float, default=DEFAULT_MIN_OI)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = run_historical_option_replay(
        db_path=args.db,
        table=args.table,
        journal_file=args.journal,
        root=args.root,
        max_rows=args.max_rows,
        lookback_dates=args.lookback_dates,
        max_per_date=args.max_per_date,
        min_oi=args.min_oi,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
