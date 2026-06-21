#!/usr/bin/env python3
"""Repair signal/execution integrity issues found by the EOD audit."""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional


def _parse_expiry(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d%b%Y", "%d%b%y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except Exception:
            continue
    return None


def _append_reason(existing: str, reason: str) -> str:
    parts = [p for p in str(existing or "").split(",") if p]
    if reason not in parts:
        parts.append(reason)
    return ",".join(parts)


def repair_signal_log(
    *,
    signal_db: str = "signal_log.db",
    trades_db: str = "trades.db",
    day: str = "",
    dry_run: bool = False,
) -> dict:
    if not Path(signal_db).exists():
        return {"ok": False, "error": f"missing {signal_db}"}
    day = day or date.today().isoformat()
    trade_ids = set()
    if Path(trades_db).exists():
        with sqlite3.connect(trades_db) as conn:
            trade_ids = {
                str(row[0])
                for row in conn.execute("SELECT trade_id FROM trades WHERE COALESCE(trade_id,'') != ''")
            }

    scanned = 0
    repaired = 0
    details = []
    with sqlite3.connect(signal_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, signal_date, signal_time, symbol, strategy, trade_id,
                   option_expiry, option_dte, option_symbol, rejection_reason
              FROM signal_log
             WHERE executed = 1 AND signal_date = ?
             ORDER BY id
            """,
            (day,),
        ).fetchall()
        for row in rows:
            scanned += 1
            reasons = []
            trade_id = str(row["trade_id"] or "").strip()
            if not trade_id or trade_id not in trade_ids:
                reasons.append("repair_no_matching_trade")
            exp = _parse_expiry(row["option_expiry"])
            if exp and exp < date.today():
                reasons.append("repair_expired_option")
            try:
                if str(row["option_symbol"] or "").strip() and int(row["option_dte"] or 0) < 0:
                    reasons.append("repair_negative_dte")
            except Exception:
                pass
            if not reasons:
                continue
            repaired += 1
            new_reason = str(row["rejection_reason"] or "")
            for reason in reasons:
                new_reason = _append_reason(new_reason, reason)
            details.append({
                "id": row["id"],
                "symbol": row["symbol"],
                "strategy": row["strategy"],
                "trade_id": trade_id,
                "reasons": reasons,
            })
            if not dry_run:
                conn.execute(
                    """
                    UPDATE signal_log
                       SET executed = 0,
                           trade_id = '',
                           rejection_reason = ?
                     WHERE id = ?
                    """,
                    (new_reason, row["id"]),
                )
        if not dry_run:
            conn.commit()
    return {"ok": True, "day": day, "scanned": scanned, "repaired": repaired, "details": details}


def repair_bse_snapshot_failures(
    *,
    db_path: str = "option_chain_snapshots.db",
    day: str = "",
    dry_run: bool = False,
) -> dict:
    if not Path(db_path).exists():
        return {"ok": False, "error": f"missing {db_path}"}
    day = day or date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
              FROM option_chain_snapshots
             WHERE substr(snapshot_time,1,10) = ?
               AND underlying IN ('SENSEX','BANKEX')
               AND COALESCE(ok,0) = 0
            """,
            (day,),
        ).fetchone()[0]
        if not dry_run:
            conn.execute(
                """
                DELETE FROM option_chain_snapshots
                 WHERE substr(snapshot_time,1,10) = ?
                   AND underlying IN ('SENSEX','BANKEX')
                   AND COALESCE(ok,0) = 0
                """,
                (day,),
            )
            conn.commit()
    return {"ok": True, "day": day, "removed_failed_bse_rows": int(count or 0)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    signal = repair_signal_log(day=args.day, dry_run=args.dry_run)
    bse = repair_bse_snapshot_failures(day=args.day, dry_run=args.dry_run)
    print({"signal_log": signal, "bse_snapshots": bse})
    return 0 if signal.get("ok") and bse.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
