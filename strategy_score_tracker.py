
"""
strategy_score_tracker.py — Store ALL strategy scores for every scan cycle

Stores: symbol, strategy_name, score, regime, vix, timestamp
Used by EOD ML model to:
  - Analyze which strategies perform best in which conditions
  - Track score distributions across all capital ranges
  - Provide accuracy feedback on each strategy
"""
from __future__ import annotations
import logging, sqlite3, json, time
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
_DB = "trades.db"


def _get_conn():
    conn = sqlite3.connect(_DB, check_same_thread=False, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            score REAL NOT NULL,
            direction TEXT,
            regime TEXT,
            vix REAL,
            price REAL,
            reasons TEXT,
            metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fii_dii_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            fii_buy REAL, fii_sell REAL, fii_net REAL,
            dii_buy REAL, dii_sell REAL, dii_net REAL,
            fii_futures_oi REAL, fii_futures_net REAL,
            vix REAL,
            nifty_close REAL,
            source TEXT,
            UNIQUE(date, source)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eod_ml_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            strategy TEXT NOT NULL,
            total_signals INTEGER,
            avg_score REAL,
            win_rate REAL,
            avg_pnl REAL,
            accuracy REAL,
            regime TEXT,
            feedback TEXT,
            UNIQUE(date, strategy)
        )
    """)
    conn.commit()
    return conn


def record_strategy_score(
    symbol: str, strategy: str, score: float,
    direction: str = "", regime: str = "",
    vix: float = 0, price: float = 0,
    reasons: list = None, metadata: dict = None,
) -> None:
    """Record a strategy score for ML analysis."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO strategy_scores "
            "(timestamp, symbol, strategy, score, direction, regime, vix, price, reasons, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), symbol, strategy, score,
             direction, regime, vix, price,
             json.dumps(reasons or []), json.dumps(metadata or {}))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("score_record: %s", e)


def record_fii_dii(
    fii_buy=0, fii_sell=0, fii_net=0,
    dii_buy=0, dii_sell=0, dii_net=0,
    fii_futures_oi=0, fii_futures_net=0,
    vix=0, nifty_close=0, source="nse"
) -> None:
    """Record FII/DII data for the day."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO fii_dii_data "
            "(date,fii_buy,fii_sell,fii_net,dii_buy,dii_sell,dii_net,"
            "fii_futures_oi,fii_futures_net,vix,nifty_close,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (date.today().isoformat(), fii_buy, fii_sell, fii_net,
             dii_buy, dii_sell, dii_net,
             fii_futures_oi, fii_futures_net, vix, nifty_close, source)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("fii_dii_record: %s", e)


def run_eod_ml_analysis() -> str:
    """
    EOD analysis: evaluate all strategies from today's scores.
    Compares strategy signals against actual price movement.
    """
    try:
        conn = _get_conn()
        today = date.today().isoformat()

        # Get all strategies scored today
        rows = conn.execute(
            "SELECT strategy, COUNT(*) as cnt, AVG(score) as avg_score, "
            "GROUP_CONCAT(DISTINCT regime) as regimes "
            "FROM strategy_scores WHERE date(timestamp) = ? "
            "GROUP BY strategy ORDER BY avg_score DESC",
            (today,)
        ).fetchall()

        if not rows:
            return "No strategy scores recorded today"

        # Get closed trades today for accuracy check
        trades = conn.execute(
            "SELECT strategy, COUNT(*) as cnt, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins, "
            "AVG(realized_pnl) as avg_pnl "
            "FROM trades WHERE date(exit_time) = ? AND status = 'CLOSED' "
            "GROUP BY strategy",
            (today,)
        ).fetchall()
        trade_map = {r[0]: {"cnt": r[1], "wins": r[2], "avg_pnl": r[3]} for r in trades}

        lines = [f"🧠 <b>EOD ML STRATEGY ANALYSIS</b> — {today}", ""]
        for strategy, cnt, avg_score, regimes in rows:
            t = trade_map.get(strategy, {})
            wins = t.get("wins", 0)
            total = t.get("cnt", 0)
            wr = wins / total * 100 if total else 0
            avg_pnl = t.get("avg_pnl", 0) or 0

            # Record feedback
            conn.execute(
                "INSERT OR REPLACE INTO eod_ml_feedback "
                "(date, strategy, total_signals, avg_score, win_rate, avg_pnl, accuracy, regime, feedback) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (today, strategy, cnt, avg_score, wr, avg_pnl,
                 wr if total > 0 else -1,
                 regimes or "?",
                 "profitable" if avg_pnl > 0 else "loss" if total > 0 else "no_trades")
            )

            icon = "🟢" if avg_pnl > 0 else "🔴" if total > 0 else "⚪"
            lines.append(
                f"  {icon} {strategy:25} signals={cnt:3} "
                f"score={avg_score:.1f} "
                f"{'WR=' + str(int(wr)) + '%' if total else 'no trades'} "
                f"{'₹' + f'{avg_pnl:+,.0f}' if total else ''}"
            )

        conn.commit()
        conn.close()
        lines += ["", f"  Total strategies: {len(rows)}", f"  📱 /ml · /calibrate · /bt"]
        return "\n".join(lines)
    except Exception as e:
        return f"❌ EOD ML: {e}"


def get_fii_dii_history(days: int = 30) -> list:
    """Get FII/DII data for last N days."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM fii_dii_data ORDER BY date DESC LIMIT ?",
            (days,)
        ).fetchall()
        conn.close()
        return [dict(zip(
            ["id","date","fii_buy","fii_sell","fii_net",
             "dii_buy","dii_sell","dii_net",
             "fii_futures_oi","fii_futures_net","vix","nifty_close","source"],
            r
        )) for r in rows]
    except Exception:
        return []
