#!/usr/bin/env python3
"""
signal_trade_reconciler.py

Backfill signal_log.executed/trade_id from trades.db when live entry-time
mark_executed missed a candidate row.

Run:
    .venv/bin/python signal_trade_reconciler.py --dry-run
    .venv/bin/python signal_trade_reconciler.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _connect(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _json_obj(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        out = json.loads(str(raw))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _fetch_unlinked_trades(trades_db: str) -> List[Dict[str, Any]]:
    if not Path(trades_db).exists():
        return []
    conn = _connect(trades_db)
    rows = conn.execute(
        """
        SELECT trade_id, symbol, side, strategy, entry_price, entry_time,
               created_at, metadata
        FROM trades
        ORDER BY entry_time
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _already_linked(signal_db: str, trade_id: str) -> bool:
    conn = _connect(signal_db)
    row = conn.execute(
        "SELECT id FROM signal_log WHERE trade_id = ? LIMIT 1",
        (str(trade_id),),
    ).fetchone()
    conn.close()
    return row is not None


def _find_candidate(
    signal_db: str,
    *,
    symbol: str,
    strategy: str,
    side: str,
    entry_time: float,
    max_time_diff_sec: int,
) -> Optional[Dict[str, Any]]:
    conn = _connect(signal_db)
    params = {
        "symbol": str(symbol or "").upper(),
        "strategy": str(strategy or "").upper(),
        "side": str(side or "").upper(),
        "entry_time": float(entry_time or 0.0),
        "lo": float(entry_time or 0.0) - max_time_diff_sec,
        "hi": float(entry_time or 0.0) + max_time_diff_sec,
    }
    row = conn.execute(
        """
        SELECT id, symbol, strategy, side, log_time,
               ABS(log_time - :entry_time) AS dt
        FROM signal_log
        WHERE executed = 0
          AND UPPER(symbol) = :symbol
          AND UPPER(strategy) = :strategy
          AND UPPER(side) = :side
          AND log_time BETWEEN :lo AND :hi
        ORDER BY dt ASC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT id, symbol, strategy, side, log_time,
                   ABS(log_time - :entry_time) AS dt
            FROM signal_log
            WHERE executed = 0
              AND UPPER(symbol) = :symbol
              AND UPPER(side) = :side
              AND log_time BETWEEN :lo AND :hi
            ORDER BY dt ASC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def _option_metadata_from_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
    meta = _json_obj(trade.get("metadata"))
    if str(meta.get("asset_type", "")).upper() != "OPTION":
        return {}
    return {
        "option_type": meta.get("option_type", ""),
        "option_strike": int(meta.get("strike", 0) or 0),
        "option_expiry": str(meta.get("option_expiry", meta.get("expiry", "")) or ""),
        "option_dte": int(meta.get("dte", meta.get("option_dte", 0)) or 0),
        "option_style": str(meta.get("style", "") or ""),
        "option_premium": float(trade.get("entry_price", 0) or 0),
        "option_symbol": str(trade.get("symbol", "") or ""),
    }


def _mark(
    signal_db: str,
    *,
    signal_id: int,
    trade_id: str,
    option_metadata: Dict[str, Any],
) -> None:
    cols = ["executed = 1", "trade_id = ?"]
    vals: List[Any] = [str(trade_id)]
    for key, col in (
        ("option_type", "option_type"),
        ("option_strike", "option_strike"),
        ("option_expiry", "option_expiry"),
        ("option_dte", "option_dte"),
        ("option_style", "option_style"),
        ("option_premium", "option_premium"),
        ("option_symbol", "option_symbol"),
    ):
        if key in option_metadata:
            cols.append(f"{col} = ?")
            vals.append(option_metadata[key])
    vals.append(int(signal_id))
    conn = _connect(signal_db)
    conn.execute(f"UPDATE signal_log SET {', '.join(cols)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def reconcile_signal_trades(
    *,
    trades_db: str = "trades.db",
    signal_db: str = "signal_log.db",
    max_time_diff_sec: int = 900,
    dry_run: bool = False,
) -> Dict[str, Any]:
    trades = _fetch_unlinked_trades(trades_db)
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "trades_seen": len(trades),
        "already_linked": 0,
        "matched": 0,
        "updated": 0,
        "unmatched": [],
        "matches": [],
    }
    if not Path(signal_db).exists():
        result["error"] = "signal_log_db_missing"
        return result

    for trade in trades:
        trade_id = str(trade.get("trade_id", "") or "")
        if not trade_id:
            continue
        if _already_linked(signal_db, trade_id):
            result["already_linked"] += 1
            continue
        entry_time = float(trade.get("entry_time") or trade.get("created_at") or 0.0)
        candidate = _find_candidate(
            signal_db,
            symbol=str(trade.get("symbol", "")),
            strategy=str(trade.get("strategy", "")),
            side=str(trade.get("side", "")),
            entry_time=entry_time,
            max_time_diff_sec=max_time_diff_sec,
        )
        if not candidate:
            result["unmatched"].append({
                "trade_id": trade_id,
                "symbol": trade.get("symbol"),
                "strategy": trade.get("strategy"),
                "entry_time": entry_time,
            })
            continue
        result["matched"] += 1
        match = {
            "trade_id": trade_id,
            "signal_id": candidate.get("id"),
            "symbol": candidate.get("symbol"),
            "strategy": candidate.get("strategy"),
            "dt_sec": round(float(candidate.get("dt", 0.0) or 0.0), 3),
        }
        result["matches"].append(match)
        if not dry_run:
            _mark(
                signal_db,
                signal_id=int(candidate["id"]),
                trade_id=trade_id,
                option_metadata=_option_metadata_from_trade(trade),
            )
            result["updated"] += 1
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades-db", default="trades.db")
    parser.add_argument("--signal-db", default="signal_log.db")
    parser.add_argument("--max-time-diff-sec", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = reconcile_signal_trades(
        trades_db=args.trades_db,
        signal_db=args.signal_db,
        max_time_diff_sec=args.max_time_diff_sec,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
