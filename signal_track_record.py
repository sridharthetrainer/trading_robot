"""
signal_track_record.py — Public Signal Track Record System

Reads from signal_log.db and walk_forward_results.json to build a
per-strategy performance card with:

  - Out-of-sample win rate (from triple-barrier labelled signals)
  - OOS Sharpe ratio (from walk_forward_results.json)
  - Regime-conditional performance (trending vs ranging)
  - Average score of winning vs losing signals
  - Last 30-day trend (improving / deteriorating / stable)
  - Minimum sample guard: never report until >= 30 labelled signals

This is the "proof wall" — the public-facing track record that converts
skeptical users. Updated nightly by idle_engine.py or on-demand.

Output: track_record.json — served by dashboard_server.py
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_DB_PATH       = Path("signal_log.db")
_WF_PATH       = Path("walk_forward_results.json")
_OUTPUT_PATH   = Path("track_record.json")
_TABLE         = "signal_log"
_MIN_SAMPLES   = 30    # minimum labelled signals before reporting
_RECENT_DAYS   = 30    # days for "recent" performance window


def _query(sql: str, params: tuple = ()) -> List[Dict]:
    """Execute a SELECT query on signal_log.db and return rows as dicts."""
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.debug("signal_track_record query: %s", e)
        return []


def _sharpe(returns: List[float], annualise: bool = True) -> float:
    """Sharpe ratio from a list of per-trade returns (P&L)."""
    if len(returns) < 3:
        return 0.0
    arr  = np.array(returns, dtype=float)
    mean = float(np.mean(arr))
    std  = float(np.std(arr, ddof=1))
    if std < 1e-9:
        return 0.0
    s = mean / std
    if annualise:
        s *= np.sqrt(252)   # daily Sharpe scale
    return round(s, 3)


def _win_rate(labels: List[int]) -> float:
    wins   = labels.count(1)
    losses = labels.count(-1)
    total  = wins + losses
    return round(wins / total, 4) if total > 0 else 0.0


def per_strategy_record(min_samples: int = _MIN_SAMPLES) -> Dict[str, Any]:
    """
    Compute per-strategy performance from signal_log.db.
    Returns dict keyed by strategy name.
    Only strategies with >= min_samples labelled signals are included.
    """
    rows = _query(
        f"SELECT strategy, tb_label, score, regime, signal_date "
        f"FROM {_TABLE} WHERE tb_label != -99 AND strategy IS NOT NULL "
        f"ORDER BY signal_date ASC"
    )
    if not rows:
        return {}

    # Group by strategy
    by_strategy: Dict[str, List[Dict]] = {}
    for row in rows:
        strat = str(row.get("strategy", "unknown"))
        by_strategy.setdefault(strat, []).append(row)

    cutoff = (datetime.now() - timedelta(days=_RECENT_DAYS)).date().isoformat()

    result: Dict[str, Any] = {}

    for strategy, trades in by_strategy.items():
        labels = [int(t["tb_label"]) for t in trades if int(t["tb_label"]) in (1, -1)]
        if len(labels) < min_samples:
            continue

        scores      = [float(t.get("score", 0) or 0) for t in trades]
        win_labels  = [float(t.get("score",0) or 0) for t in trades if int(t["tb_label"]) == 1]
        loss_labels = [float(t.get("score",0) or 0) for t in trades if int(t["tb_label"]) == -1]

        # Recent performance (last 30 days)
        recent = [t for t in trades if str(t.get("signal_date","")) >= cutoff]
        recent_labels = [int(t["tb_label"]) for t in recent if int(t["tb_label"]) in (1, -1)]

        # Regime-conditional performance
        regime_stats: Dict[str, Dict] = {}
        for regime_val in set(str(t.get("regime","")) for t in trades):
            reg_trades = [t for t in trades if str(t.get("regime","")) == regime_val]
            reg_labels = [int(t["tb_label"]) for t in reg_trades if int(t["tb_label"]) in (1,-1)]
            if len(reg_labels) >= 5:
                regime_stats[regime_val] = {
                    "n":        len(reg_labels),
                    "win_rate": _win_rate(reg_labels),
                }

        # Trend: is the strategy improving or deteriorating?
        # Compare win rate of last 20 vs overall
        recent20 = [int(t["tb_label"]) for t in trades[-20:] if int(t["tb_label"]) in (1,-1)]
        trend    = "stable"
        if len(recent20) >= 10 and len(labels) >= 20:
            recent_wr  = _win_rate(recent20)
            overall_wr = _win_rate(labels)
            if recent_wr > overall_wr + 0.05:  trend = "improving"
            elif recent_wr < overall_wr - 0.05: trend = "deteriorating"

        result[strategy] = {
            "total_signals":  len(labels),
            "win_rate":       _win_rate(labels),
            "wins":           labels.count(1),
            "losses":         labels.count(-1),
            "avg_score_wins": round(float(np.mean(win_labels)),  2) if win_labels  else 0,
            "avg_score_loss": round(float(np.mean(loss_labels)), 2) if loss_labels else 0,
            "recent_30d": {
                "n":        len(recent_labels),
                "win_rate": _win_rate(recent_labels) if recent_labels else None,
            },
            "by_regime":   regime_stats,
            "trend":       trend,
            "sufficient_samples": True,
        }

    return result


def load_wf_sharpe() -> Dict[str, float]:
    """Load per-strategy OOS Sharpe from walk_forward_results.json."""
    if not _WF_PATH.exists():
        return {}
    try:
        data = json.loads(_WF_PATH.read_text(encoding="utf-8"))
        results = data.get("results", {})
        return {
            strategy: round(float(v.get("avg_sharpe", 0)), 3)
            for strategy, v in results.items()
        }
    except Exception as e:
        logger.debug("load_wf_sharpe: %s", e)
        return {}


def load_wf_by_regime() -> Dict[str, Any]:
    """Load regime-stratified Sharpe from walk_forward_results.json."""
    if not _WF_PATH.exists():
        return {}
    try:
        data = json.loads(_WF_PATH.read_text(encoding="utf-8"))
        return data.get("by_regime", {})
    except Exception:
        return {}


def build_track_record(min_samples: int = _MIN_SAMPLES) -> Dict[str, Any]:
    """
    Build the complete public track record.
    Merges:
      - Per-signal win rates from signal_log.db (live, labelled by triple barrier)
      - OOS Sharpe from walk_forward_results.json (backtest-validated)
      - Regime-stratified performance from both sources

    Returns a dict suitable for JSON serialization and dashboard display.
    """
    per_strat = per_strategy_record(min_samples)
    wf_sharpe = load_wf_sharpe()
    wf_regime = load_wf_by_regime()

    # Merge walk-forward Sharpe into per-strategy records
    for strategy, record in per_strat.items():
        record["oos_sharpe"]    = wf_sharpe.get(strategy, None)
        record["wf_by_regime"]  = wf_regime.get(strategy, {})

    # Also include WF-only strategies (no live signals yet but backtest exists)
    for strategy, sharpe in wf_sharpe.items():
        if strategy not in per_strat:
            per_strat[strategy] = {
                "total_signals": 0,
                "win_rate":      None,
                "wins":          0,
                "losses":        0,
                "oos_sharpe":    sharpe,
                "wf_by_regime":  wf_regime.get(strategy, {}),
                "trend":         "insufficient_data",
                "sufficient_samples": False,
            }

    # Overall system stats
    all_labels  = _query(
        f"SELECT tb_label FROM {_TABLE} WHERE tb_label != -99"
    )
    all_executed = _query(
        f"SELECT tb_label FROM {_TABLE} WHERE tb_label != -99 AND executed = 1"
    )
    total_l = [int(r["tb_label"]) for r in all_labels if int(r["tb_label"]) in (1,-1)]
    exec_l  = [int(r["tb_label"]) for r in all_executed if int(r["tb_label"]) in (1,-1)]

    summary = {
        "generated_at":      datetime.now().isoformat(),
        "generated_date":    date.today().isoformat(),
        "total_signals":     len(total_l),
        "overall_win_rate":  _win_rate(total_l),
        "executed_win_rate": _win_rate(exec_l),
        "strategies_tracked":len([s for s in per_strat if per_strat[s]["sufficient_samples"]]),
        "strategies_total":  len(per_strat),
        "note": (
            f"Win rates based on triple-barrier labelling of live signals. "
            f"OOS Sharpe from walk-forward validation. "
            f"Minimum {min_samples} signals required before reporting a strategy."
        ),
    }

    return {
        "summary":    summary,
        "strategies": per_strat,
    }


def save_track_record(output_path: Path = _OUTPUT_PATH) -> Dict[str, Any]:
    """Build and save track_record.json. Returns the record dict."""
    record = build_track_record()
    try:
        output_path.write_text(
            json.dumps(record, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(
            "Track record saved: %d strategies, overall win_rate=%.1f%%",
            record["summary"]["strategies_tracked"],
            record["summary"]["overall_win_rate"] * 100,
        )
    except Exception as e:
        logger.warning("save_track_record failed: %s", e)
    return record


def get_strategy_card(strategy: str) -> Dict[str, Any]:
    """
    Return a single strategy's track record card.
    Used by Telegram /trackrecord STRATEGY command.
    """
    record = build_track_record(min_samples=5)   # lower threshold for single-card view
    card   = record.get("strategies", {}).get(strategy)
    if not card:
        return {"error": f"No data for strategy '{strategy}'. "
                         f"Available: {list(record['strategies'].keys())[:10]}"}

    # Format for human readability
    wr  = card.get("win_rate")
    shr = card.get("oos_sharpe")
    n   = card.get("total_signals", 0)
    return {
        "strategy":     strategy,
        "win_rate_pct": f"{wr*100:.1f}%" if wr is not None else "N/A",
        "oos_sharpe":   f"{shr:.2f}"     if shr is not None else "N/A (run walk-forward first)",
        "n_signals":    n,
        "trend":        card.get("trend", "unknown"),
        "by_regime":    card.get("by_regime", {}),
        "warning": (
            None if card.get("sufficient_samples")
            else f"Only {n} signals — {_MIN_SAMPLES} required for statistical reliability."
        ),
    }
