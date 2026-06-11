
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
            "FROM trades WHERE date(exit_time, 'unixepoch', 'localtime') = ? AND status = 'CLOSED' "
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

        # Win-rate degradation guard — log warning for any strategy
        # with >= 10 trades today and win rate below 45%
        degraded = [
            (strategy, int(wr), t.get("cnt", 0))
            for strategy, cnt, avg_score, regimes in rows
            for t in [trade_map.get(strategy, {})]
            for wr in [t.get("wins", 0) / t.get("cnt", 1) * 100 if t.get("cnt", 0) else 0]
            if t.get("cnt", 0) >= 10 and wr < 45
        ]
        if degraded:
            import logging as _wlog
            _wlog.getLogger(__name__).warning(
                "DEGRADED STRATEGIES (win_rate < 45%% with >= 10 trades): %s — "
                "consider disabling or retraining via /bt or /ml",
                ", ".join(f"{s}({wr}%%/{n}t)" for s, wr, n in degraded),
            )
            lines.append("")
            lines.append("⚠️ <b>Degraded strategies</b> (WR < 45%%, ≥10 trades):")
            for s, wr, n in degraded:
                lines.append(f"  🔴 {s}: {wr}%% win rate over {n} trades")

        conn.close()
        try:
            from eod_weight_engine import run_eod_weight_update
            weights = run_eod_weight_update()
            top_s = weights.get("top_strategies", [])[:3]
            top_i = weights.get("top_indicators", [])[:3]
            if top_s or top_i:
                lines.append("")
                lines.append("<b>Learned weights for next session</b>")
                if top_s:
                    lines.append("  Strategies: " + ", ".join(
                        f"{r['strategy']} {r['weight']:.2f}x" for r in top_s
                    ))
                if top_i:
                    lines.append("  Indicators: " + ", ".join(
                        f"{r['indicator']} {r['weight']:.2f}x" for r in top_i
                    ))
        except Exception as e:
            logger.debug("EOD weight update from ML analysis: %s", e)

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


def store_global_snapshot() -> None:
    """Store daily global market data for correlation analysis."""
    try:
        import sqlite3, json
        from datetime import date
        from cross_asset import get_cross_asset_data
        conn = sqlite3.connect("trades.db", check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS global_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                sp500 REAL, dxy REAL, gold REAL, brent REAL,
                us_vix REAL, usdinr REAL, us10y REAL,
                india_vix REAL, nifty_close REAL,
                UNIQUE(date)
            )
        """)
        data = get_cross_asset_data(force=True)
        if data:
            conn.execute(
                "INSERT OR REPLACE INTO global_daily "
                "(date,sp500,dxy,gold,brent,us_vix,usdinr,us10y) VALUES (?,?,?,?,?,?,?,?)",
                (date.today().isoformat(),
                 float(data.get("SP500",{}).get("price",0) or 0),
                 float(data.get("DXY",{}).get("price",0) or 0),
                 float(data.get("GOLD",{}).get("price",0) or 0),
                 float(data.get("BRENT",{}).get("price",0) or 0),
                 float(data.get("USVIX",{}).get("price",0) or 0),
                 float(data.get("USDINR",{}).get("price",0) or 0),
                 float(data.get("US10Y",{}).get("price",0) or 0))
            )
            conn.commit()
        conn.close()
    except Exception as e:
        import logging; logging.getLogger(__name__).debug("global_snapshot: %s", e)
