"""Accurate realized P&L attribution for options versus cash/equity trades."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


_OPTION_SYMBOL = re.compile(r"(?:\d|\b)(?:CE|PE)$", re.IGNORECASE)


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def is_option_trade(row: dict) -> bool:
    """Classify using contract symbol first, then persisted trade metadata."""
    symbol = str(row.get("symbol") or "").upper().replace(" ", "")
    strategy = str(row.get("strategy") or "").upper()
    if _OPTION_SYMBOL.search(symbol) or "OPTION" in strategy or "HERO_ZERO" in strategy:
        return True
    for payload in (_json(row.get("metadata")), _json(row.get("signal_metadata"))):
        option_type = str(payload.get("option_type") or "").upper()
        instrument = str(payload.get("instrument_type") or payload.get("asset_class") or "").upper()
        option_symbol = str(payload.get("option_symbol") or "").upper()
        if option_type in {"CE", "PE", "CALL", "PUT"} or "OPT" in instrument or option_symbol:
            return True
    return False


def today_pnl_breakdown(db_path: str = "trades.db", day: str | None = None) -> dict:
    report_day = day or date.today().isoformat()
    empty = lambda: {"trades": 0, "wins": 0, "gross": 0.0, "charges": 0.0, "net": 0.0}
    result = {"day": report_day, "options": empty(), "normal": empty(), "total": empty(), "open": 0}
    if not Path(db_path).exists():
        return result
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT symbol,strategy,metadata,signal_metadata,gross_pnl,
                      total_charges,realized_pnl,status,entry_time,exit_time
                 FROM trades
                WHERE (status='CLOSED' AND date(exit_time,'unixepoch','localtime')=?)
                   OR (status='OPEN' AND date(entry_time,'unixepoch','localtime')=?)""",
            (report_day, report_day),
        ).fetchall()
    for raw in rows:
        row = dict(raw)
        if str(row.get("status")).upper() == "OPEN":
            result["open"] += 1
            continue
        bucket = result["options" if is_option_trade(row) else "normal"]
        net = float(row.get("realized_pnl") or 0)
        bucket["trades"] += 1
        bucket["wins"] += int(net > 0)
        bucket["gross"] += float(row.get("gross_pnl") or 0)
        bucket["charges"] += float(row.get("total_charges") or 0)
        bucket["net"] += net
    for key in ("options", "normal"):
        for metric in ("gross", "charges", "net"):
            result[key][metric] = round(result[key][metric], 2)
        for metric in ("trades", "wins", "gross", "charges", "net"):
            result["total"][metric] += result[key][metric]
    for metric in ("gross", "charges", "net"):
        result["total"][metric] = round(result["total"][metric], 2)
    return result


def format_today_pnl(db_path: str = "trades.db", day: str | None = None) -> str:
    report = today_pnl_breakdown(db_path, day)
    lines = [f"💰 <b>TODAY'S REALIZED P&amp;L — {report['day']}</b>"]
    for label, key in (("Options", "options"), ("Normal", "normal")):
        row = report[key]
        losses = row["trades"] - row["wins"]
        lines.append(
            f"• <b>{label}</b>: ₹{row['net']:+,.2f} net "
            f"({row['trades']} trades · {row['wins']}W/{losses}L)\n"
            f"  Gross ₹{row['gross']:+,.2f} · Charges ₹{row['charges']:,.2f}"
        )
    total = report["total"]
    lines.append(f"<b>Total: ₹{total['net']:+,.2f}</b> · Open positions {report['open']}")
    return "\n".join(lines)

