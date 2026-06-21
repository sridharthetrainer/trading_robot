"""
manual_learning.py — self-evolving edge tracker for MANUAL trades.

Every closed manual trade is recorded with its context (regime, VIX, indicator
alignment, option/equity, days-to-expiry, whether it was held overnight, entry
hour) and outcome (win/loss, R-multiple). Over time this builds an EMPIRICAL
picture of what works for *this trader* — which the exit-decision layer consults.

This is honest, data-driven adaptation (not a fabricated ML model): plain
conditional win-rate / expectancy with a minimum-sample guard so it never acts
on noise. A learned classifier can be layered on later once the sample is large.

Kept separate from the bot's strategy calibrator on purpose — discretionary
manual trades must not pollute the algo strategy weights.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Dict, List, Optional

logger = logging.getLogger("manual_learning")

DB_PATH      = "manual_learning.db"
MIN_SAMPLES  = 10          # don't draw conclusions from fewer outcomes


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.execute("""
        CREATE TABLE IF NOT EXISTS outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            symbol TEXT, side TEXT, is_option INTEGER,
            entry REAL, exit REAL, pnl REAL, pnl_pct REAL, r_multiple REAL,
            win INTEGER,
            regime TEXT, vix REAL, alignment_pct REAL,
            dte INTEGER, held_overnight INTEGER, entry_hour INTEGER,
            closed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
    return c


def record_outcome(ctx: Dict) -> None:
    """Persist a closed-trade outcome. ctx keys mirror the columns above."""
    try:
        c = _conn()
        c.execute(
            "INSERT OR REPLACE INTO outcomes "
            "(order_id,symbol,side,is_option,entry,exit,pnl,pnl_pct,r_multiple,"
            "win,regime,vix,alignment_pct,dte,held_overnight,entry_hour) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ctx.get("order_id"), ctx.get("symbol"), ctx.get("side"),
             1 if ctx.get("is_option") else 0,
             ctx.get("entry"), ctx.get("exit"), ctx.get("pnl"),
             ctx.get("pnl_pct"), ctx.get("r_multiple"),
             1 if (ctx.get("pnl", 0) or 0) > 0 else 0,
             ctx.get("regime"), ctx.get("vix"), ctx.get("alignment_pct"),
             ctx.get("dte"), 1 if ctx.get("held_overnight") else 0,
             ctx.get("entry_hour")))
        c.commit(); c.close()
        logger.info("Recorded manual outcome: %s pnl=%.0f", ctx.get("symbol"),
                    ctx.get("pnl", 0) or 0)
    except Exception as e:
        logger.debug("record_outcome: %s", e)


def _rate(rows: List) -> Dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy": 0.0}
    wins = sum(1 for r in rows if r[0])
    rs   = [float(r[1] or 0) for r in rows]
    pnls = [float(r[2] or 0) for r in rows]
    return {"n": n,
            "win_rate": round(100 * wins / n, 1),
            "avg_r": round(sum(rs) / n, 2),
            "expectancy": round(sum(pnls) / n, 0)}


def get_stats() -> Dict:
    """Overall + per-condition win-rate / expectancy (only buckets with data)."""
    out = {"overall": {"n": 0}, "by": {}}
    try:
        c = _conn()
        allrows = c.execute("SELECT win,r_multiple,pnl FROM outcomes").fetchall()
        out["overall"] = _rate(allrows)
        for label, col in (("option", "is_option"), ("side", "side"),
                           ("regime", "regime"), ("overnight", "held_overnight")):
            buckets = {}
            for val, in c.execute(f"SELECT DISTINCT {col} FROM outcomes"):
                rows = c.execute(
                    f"SELECT win,r_multiple,pnl FROM outcomes WHERE {col}=?",
                    (val,)).fetchall()
                buckets[str(val)] = _rate(rows)
            out["by"][label] = buckets
        c.close()
    except Exception as e:
        logger.debug("get_stats: %s", e)
    return out


def get_bias(ctx: Dict) -> Optional[Dict]:
    """
    Empirical nudge for a trade context — only when enough samples exist.

    Returns {"signal": "favorable"|"caution", "win_rate", "n", "note"} or None.
    Used to colour the EOD decision (e.g. this trader historically loses holding
    options overnight → lean CLOSE).
    """
    try:
        c = _conn()
        conds, params, parts = [], [], []
        if ctx.get("is_option") is not None:
            conds.append("is_option=?"); params.append(1 if ctx["is_option"] else 0)
            parts.append("option" if ctx["is_option"] else "equity")
        if ctx.get("held_overnight") is not None:
            conds.append("held_overnight=?"); params.append(1 if ctx["held_overnight"] else 0)
            parts.append("overnight" if ctx["held_overnight"] else "intraday")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        rows = c.execute(
            f"SELECT win,r_multiple,pnl FROM outcomes{where}", params).fetchall()
        c.close()
        st = _rate(rows)
        if st["n"] < MIN_SAMPLES:
            return None
        wr = st["win_rate"]
        if wr < 35:
            return {"signal": "caution", "win_rate": wr, "n": st["n"],
                    "note": f"history: {' '.join(parts)} trades win only {wr:.0f}% (n={st['n']})"}
        if wr > 60:
            return {"signal": "favorable", "win_rate": wr, "n": st["n"],
                    "note": f"history: {' '.join(parts)} trades win {wr:.0f}% (n={st['n']})"}
        return None
    except Exception as e:
        logger.debug("get_bias: %s", e)
        return None
