#!/usr/bin/env python3
"""
option_autotune_backfill.py

Backfill option_decision_journal.jsonl from historical closed option trades and
labelled signal_log rows, then rebuild option_strike_autotune.json.

Run:
    python option_autotune_backfill.py
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from option_decision_journal import (
    DEFAULT_JOURNAL_FILE,
    load_recent_option_decisions,
    record_option_decision,
    repair_missing_shadow_candidates,
)
from option_strike_autotune import build_strike_autotune


_OPTION_RE = re.compile(r"(?P<strike>\d{4,6})(?P<otype>CE|PE)$", re.IGNORECASE)


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


def _parse_option_symbol(symbol: str) -> Dict[str, Any]:
    m = _OPTION_RE.search(str(symbol or "").upper())
    if not m:
        return {}
    return {
        "strike": _safe_int(m.group("strike")),
        "option_type": m.group("otype").upper(),
    }


def _existing_journal_keys(path: str) -> Set[str]:
    keys: Set[str] = set()
    for row in load_recent_option_decisions(path=path, limit=200000):
        if str(row.get("decision", "")) != "selected":
            continue
        trade_id = str(row.get("trade_id", "") or "").strip()
        if trade_id:
            keys.add(f"trade:{trade_id}")
            continue
        source_id = str(row.get("source_id", "") or "").strip()
        if source_id:
            keys.add(f"source:{source_id}")
    return keys


def _is_option_trade(symbol: str, metadata: Dict[str, Any]) -> bool:
    return (
        str(metadata.get("asset_type", "")).upper() == "OPTION"
        or bool(_parse_option_symbol(symbol))
    )


def backfill_from_trades_db(
    db_path: str = "trades.db",
    journal_file: str = DEFAULT_JOURNAL_FILE,
    dry_run: bool = False,
) -> int:
    if not Path(db_path).exists():
        return 0
    existing = _existing_journal_keys(journal_file)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT trade_id, symbol, side, strategy, entry_price, exit_price, qty,
               realized_pnl, status, exit_reason, score, metadata
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchall()
    conn.close()

    written = 0
    for row in rows:
        trade_id = str(row["trade_id"] or "")
        if not trade_id or f"trade:{trade_id}" in existing:
            continue
        metadata = _json_obj(row["metadata"])
        symbol = str(row["symbol"] or "")
        if not _is_option_trade(symbol, metadata):
            continue

        parsed = _parse_option_symbol(symbol)
        signal_data = metadata.get("signal_data", {}) if isinstance(metadata.get("signal_data"), dict) else {}
        selected = {
            "symbol": symbol,
            "strike": metadata.get("strike") or parsed.get("strike", 0),
            "strike_type": metadata.get("strike_type", ""),
            "option_type": metadata.get("option_type") or parsed.get("option_type", ""),
            "premium": _safe_float(row["entry_price"]),
            "dte": metadata.get("dte", metadata.get("option_dte", 0)),
            "lot_size": metadata.get("lot_size", 1),
            "qty": _safe_int(row["qty"], 0),
            "style": metadata.get("style", ""),
        }
        pnl = _safe_float(row["realized_pnl"])
        if dry_run:
            written += 1
            continue
        record_option_decision(
            strategy=str(row["strategy"] or "AUTO"),
            symbol=str(metadata.get("source_symbol") or signal_data.get("symbol") or symbol),
            decision="selected",
            reason="backfill_trades_db",
            side=str(row["side"] or signal_data.get("side", "")),
            spot=_safe_float(signal_data.get("spot"), _safe_float(signal_data.get("price"), 0.0)),
            setup_score=_safe_float(row["score"]),
            quality=signal_data.get("option_quality") if isinstance(signal_data.get("option_quality"), dict) else {},
            selected=selected,
            strikes=metadata.get("shadow_candidates", []),
            trade_id=trade_id,
            outcome_label=1 if pnl > 0 else -1 if pnl < 0 else 0,
            pnl=pnl,
            outcome={
                "label": 1 if pnl > 0 else -1 if pnl < 0 else 0,
                "pnl": pnl,
                "exit_reason": str(row["exit_reason"] or ""),
            },
            path=journal_file,
        )
        existing.add(f"trade:{trade_id}")
        written += 1
    return written


def backfill_from_signal_log(
    db_path: str = "signal_log.db",
    journal_file: str = DEFAULT_JOURNAL_FILE,
    dry_run: bool = False,
) -> int:
    if not Path(db_path).exists():
        return 0
    existing = _existing_journal_keys(journal_file)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, symbol, side, strategy, score, executed, trade_id,
               option_type, option_strike, option_expiry, option_dte,
               option_style, option_premium, option_symbol, tb_label,
               outcome_price
        FROM signal_log
        WHERE (option_symbol != '' OR option_strike > 0)
          AND tb_label != -99
        """
    ).fetchall()
    conn.close()

    written = 0
    for row in rows:
        source_id = f"signal_log:{row['id']}"
        trade_id = str(row["trade_id"] or "")
        key = f"trade:{trade_id}" if trade_id else f"source:{source_id}"
        if key in existing:
            continue
        premium = _safe_float(row["option_premium"])
        outcome_price = _safe_float(row["outcome_price"])
        label = _safe_int(row["tb_label"], 0)
        pnl = outcome_price - premium if outcome_price > 0 and premium > 0 else float(label)
        selected = {
            "symbol": str(row["option_symbol"] or ""),
            "strike": _safe_int(row["option_strike"]),
            "option_type": str(row["option_type"] or ""),
            "premium": premium,
            "dte": _safe_int(row["option_dte"]),
            "style": str(row["option_style"] or ""),
        }
        if dry_run:
            written += 1
            continue
        record_option_decision(
            strategy=str(row["strategy"] or "AUTO"),
            symbol=str(row["symbol"] or ""),
            decision="selected",
            reason="backfill_signal_log",
            side=str(row["side"] or ""),
            setup_score=_safe_float(row["score"]),
            selected=selected,
            trade_id=trade_id,
            source_id=source_id,
            outcome_label=label,
            pnl=pnl,
            outcome={"label": label, "pnl": pnl, "exit_reason": "signal_log_label"},
            path=journal_file,
        )
        existing.add(key)
        written += 1
    return written


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades-db", default="trades.db")
    parser.add_argument("--signal-log-db", default="signal_log.db")
    parser.add_argument("--journal", default=DEFAULT_JOURNAL_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    snapshot_labels = {}
    if not args.dry_run:
        try:
            from option_signal_snapshot_labeller import label_pending_option_signals_from_snapshots
            snapshot_labels = label_pending_option_signals_from_snapshots(signal_db=args.signal_log_db)
            print(
                "snapshot option labels | "
                f"updated={snapshot_labels.get('updated', 0)} "
                f"checked={snapshot_labels.get('checked', 0)} "
                f"skipped={snapshot_labels.get('skipped', 0)}"
            )
        except Exception as exc:
            print(f"snapshot option labels skipped: {exc}")
    from_trades = backfill_from_trades_db(args.trades_db, args.journal, dry_run=args.dry_run)
    from_signals = backfill_from_signal_log(args.signal_log_db, args.journal, dry_run=args.dry_run)
    replay = {"skipped": True, "reason": "dry_run"}
    if not args.dry_run:
        try:
            from option_historical_replay_labeller import run_historical_option_replay
            replay = run_historical_option_replay(journal_file=args.journal)
        except Exception as exc:
            replay = {"ok": False, "reason": str(exc), "written": 0, "shadow_outcomes": 0}
    print(
        "option backfill complete | "
        f"trades_db={from_trades} signal_log={from_signals} "
        f"historical_replay={replay.get('written', 0)} "
        f"replay_shadow={replay.get('shadow_outcomes', 0)} "
        f"dry_run={args.dry_run}"
    )
    if not args.dry_run:
        repair = repair_missing_shadow_candidates(path=args.journal)
        print(
            "shadow ladder repair | "
            f"selected={repair.get('selected_seen', 0)} "
            f"updated={repair.get('updated', 0)}"
        )
        try:
            from option_shadow_labeller import label_shadow_candidates_from_eod
            shadow_result = label_shadow_candidates_from_eod(journal_file=args.journal)
            print(
                "shadow labels | "
                f"labelled_shadow={shadow_result.get('labelled_shadow', 0)} "
                f"eligible={shadow_result.get('eligible_rows', 0)} "
                f"skipped={shadow_result.get('skipped', 0)}"
            )
        except Exception as exc:
            print(f"shadow labels skipped: {exc}")
        model = build_strike_autotune(journal_file=args.journal)
        print(
            "autotune rebuilt | "
            f"labelled_selected={model.get('labelled_selected', 0)} "
            f"labelled_shadow={model.get('labelled_shadow', 0)} "
            f"features={len(model.get('feature_weights', {}) or {})}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
