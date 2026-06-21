#!/usr/bin/env python3
"""Label pending option signal_log rows from captured option-chain snapshots."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _underlying_from_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper()
    out = ""
    for ch in raw:
        if ch.isalpha():
            out += ch
        else:
            break
    return out


def _snapshot_ltp(
    *,
    snapshot_db: str,
    day: str,
    underlying: str,
    strike: float,
    option_type: str,
) -> Optional[float]:
    if not Path(snapshot_db).exists():
        return None
    try:
        with sqlite3.connect(snapshot_db) as conn:
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
                (str(underlying or "").upper(), str(day or "")),
            ).fetchall()
    except Exception:
        return None
    prefix = str(option_type or "").upper()
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
        ltp = _safe_float(
            best.get(f"{prefix}_lastPrice")
            or best.get(f"{prefix}_LTP")
            or best.get(f"{prefix}_ltp"),
            0.0,
        )
        if ltp > 0:
            return ltp
    return None


def label_pending_option_signals_from_snapshots(
    *,
    signal_db: str = "signal_log.db",
    snapshot_db: str = "option_chain_snapshots.db",
    limit: int = 5000,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply snapshot-derived outcomes to pending option signal rows."""
    if not Path(signal_db).exists():
        return {"ok": False, "reason": "signal_log_missing", "updated": 0}
    if not Path(snapshot_db).exists():
        return {"ok": False, "reason": "option_snapshots_missing", "updated": 0}

    conn = sqlite3.connect(signal_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, signal_date, symbol, side, option_symbol, option_strike,
               option_type, option_premium
          FROM signal_log
         WHERE tb_label=-99
           AND (option_symbol != '' OR option_strike > 0)
         ORDER BY id DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()

    checked = 0
    updated = 0
    skipped = 0
    samples = []
    for row in rows:
        checked += 1
        day = str(row["signal_date"] or "")
        strike = _safe_float(row["option_strike"], 0.0)
        opt_type = str(row["option_type"] or "").upper()
        premium = _safe_float(row["option_premium"], 0.0)
        underlying = str(row["symbol"] or "").upper() or _underlying_from_symbol(row["option_symbol"])
        if not day or strike <= 0 or opt_type not in {"CE", "PE"} or premium <= 0:
            skipped += 1
            continue
        exit_price = _snapshot_ltp(
            snapshot_db=snapshot_db,
            day=day,
            underlying=underlying,
            strike=strike,
            option_type=opt_type,
        )
        if exit_price is None:
            skipped += 1
            continue
        pnl = float(exit_price) - float(premium)
        label = 1 if pnl > 0 else -1 if pnl < 0 else 0
        if not dry_run:
            conn.execute(
                """
                UPDATE signal_log
                   SET tb_label=?,
                       outcome_price=?,
                       outcome_time=?,
                       peak_price=MAX(COALESCE(peak_price, 0), ?),
                       max_favorable_move=MAX(COALESCE(max_favorable_move, 0), ?)
                 WHERE id=?
                """,
                (
                    label,
                    round(float(exit_price), 4),
                    time.time(),
                    round(float(exit_price), 4),
                    round(max(0.0, pnl), 4),
                    int(row["id"]),
                ),
            )
        updated += 1
        samples.append({
            "id": int(row["id"]),
            "day": day,
            "symbol": str(row["option_symbol"] or ""),
            "entry": premium,
            "exit": round(float(exit_price), 2),
            "label": label,
        })

    if updated and not dry_run:
        conn.commit()
    conn.close()
    return {
        "ok": True,
        "checked": checked,
        "updated": updated,
        "skipped": skipped,
        "dry_run": dry_run,
        "samples": samples[:10],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-db", default="signal_log.db")
    parser.add_argument("--snapshot-db", default="option_chain_snapshots.db")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = label_pending_option_signals_from_snapshots(
        signal_db=args.signal_db,
        snapshot_db=args.snapshot_db,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
