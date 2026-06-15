"""
eod_weight_engine.py

End-of-day performance learner for strategy and indicator weights.

Inputs:
  - trades.db/trades: realized P&L from closed paper/live trades.
  - signal_log.db/signal_log: triple-barrier labelled candidate signals.

Outputs:
  - trades.db/eod_strategy_weights
  - trades.db/eod_indicator_weights
  - eod_signal_weights.json, read by the live signal engine for next-day nudges.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

TRADES_DB = Path(os.getenv("TRADES_DB", "trades.db"))
SIGNAL_DB = Path(os.getenv("SIGNAL_LOG_DB", "signal_log.db"))
WEIGHTS_FILE = Path(os.getenv("EOD_WEIGHTS_FILE", "eod_signal_weights.json"))

MIN_SAMPLES = int(os.getenv("EOD_WEIGHT_MIN_SAMPLES", "20"))
# Hard floor: below this many recorded outcomes the weight stays exactly 1.0
# (neutral). The soft `confidence` damper alone still moved weights on 1-2
# trades, which is statistical noise.
HARD_MIN_SAMPLES = int(os.getenv("EOD_WEIGHT_HARD_MIN_SAMPLES", "30"))
LOOKBACK_DAYS = int(os.getenv("EOD_WEIGHT_LOOKBACK_DAYS", "30"))
MIN_WEIGHT = float(os.getenv("EOD_MIN_WEIGHT", "0.50"))
MAX_WEIGHT = float(os.getenv("EOD_MAX_WEIGHT", "1.50"))
MAX_SCORE_NUDGE = float(os.getenv("EOD_MAX_SCORE_NUDGE", "1.25"))


INDICATOR_SPECS: Dict[str, Tuple[str, str, float]] = {
    "bhav_delivery": ("bhav_delivery", "nonzero", 0.05),
    "cross_asset": ("cross_asset_mod", "nonzero", 0.05),
    "participant_oi": ("participant_mod", "nonzero", 0.05),
    "expiry_regime": ("expiry_mod", "nonzero", 0.05),
    "sip_boost": ("sip_boost", "nonzero", 0.05),
    "bulk_deal": ("bulk_deal_mod", "nonzero", 0.05),
    "theta": ("theta_mod", "nonzero", 0.05),
    "rebalancing": ("rebal_mod", "nonzero", 0.05),
    "news": ("news_mod", "nonzero", 0.05),
    "mtf_pivot": ("mtf_pivot_mod", "nonzero", 0.05),
    "time_bucket": ("time_bucket_wt", "away_from_one", 0.05),
    "ai_confidence": ("ai_score", "gte", 0.55),
    "rl_bias": ("rl_bias", "nonzero", 0.05),
    "weinstein": ("weinstein_mod", "nonzero", 0.05),
    "volume_confirmation": ("volume_ratio", "outside_band", 0.20),
    "weekly_pivot": ("above_weekly_pvt", "truthy", 0.0),
    "monthly_pivot": ("above_monthly_pvt", "truthy", 0.0),
}

_CACHE: Dict[str, Any] = {"mtime": 0.0, "weights": {}}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables() -> None:
    with _conn(TRADES_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eod_strategy_weights (
                date TEXT NOT NULL,
                strategy TEXT NOT NULL,
                samples INTEGER DEFAULT 0,
                executed_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_pnl REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_score REAL DEFAULT 0,
                weight REAL DEFAULT 1,
                source TEXT DEFAULT '',
                details TEXT DEFAULT '{}',
                PRIMARY KEY(date, strategy)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eod_indicator_weights (
                date TEXT NOT NULL,
                indicator TEXT NOT NULL,
                samples INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_pnl REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                weight REAL DEFAULT 1,
                details TEXT DEFAULT '{}',
                PRIMARY KEY(date, indicator)
            )
        """)
        conn.commit()


def _empty_stat() -> Dict[str, Any]:
    return {
        "samples": 0,
        "executed": 0,
        "wins": 0,
        "losses": 0,
        "pnl_sum": 0.0,
        "abs_pnl_sum": 0.0,
        "score_sum": 0.0,
        "sources": set(),
    }


def _add_observation(
    stats: Dict[str, Any],
    won: bool,
    pnl: float,
    score: float = 0.0,
    source: str = "",
    executed: bool = False,
) -> None:
    stats["samples"] += 1
    stats["wins"] += 1 if won else 0
    stats["losses"] += 0 if won else 1
    stats["pnl_sum"] += pnl
    stats["abs_pnl_sum"] += abs(pnl)
    stats["score_sum"] += score
    stats["executed"] += 1 if executed else 0
    if source:
        stats["sources"].add(source)


def _weight_from_stats(stats: Dict[str, Any]) -> float:
    samples = int(stats.get("samples", 0) or 0)
    if samples < HARD_MIN_SAMPLES:
        return 1.0          # neutral until the sample is statistically meaningful

    win_rate = float(stats.get("wins", 0) or 0) / max(samples, 1)
    avg_pnl = float(stats.get("pnl_sum", 0.0) or 0.0) / max(samples, 1)
    avg_abs = float(stats.get("abs_pnl_sum", 0.0) or 0.0) / max(samples, 1)
    confidence = min(1.0, samples / max(MIN_SAMPLES, 1))

    win_edge = (win_rate - 0.50) * 2.0
    pnl_edge = math.tanh(avg_pnl / max(avg_abs, 1.0))
    raw = 1.0 + confidence * ((0.35 * win_edge) + (0.15 * pnl_edge))
    return round(_clamp(raw, MIN_WEIGHT, MAX_WEIGHT), 3)


def _row_active_indicators(row: sqlite3.Row) -> List[str]:
    active = []
    keys = set(row.keys())
    for name, (column, mode, threshold) in INDICATOR_SPECS.items():
        if column not in keys:
            continue
        value = _safe_float(row[column], 0.0)
        if mode == "nonzero" and abs(value) >= threshold:
            active.append(name)
        elif mode == "away_from_one" and abs(value - 1.0) >= threshold:
            active.append(name)
        elif mode == "gte" and value >= threshold:
            active.append(name)
        elif mode == "outside_band" and value > 0 and abs(value - 1.0) >= threshold:
            active.append(name)
        elif mode == "truthy" and value > 0:
            active.append(name)
    return active


def extract_indicators_from_meta(meta: Dict[str, Any] | None) -> List[str]:
    """Extract live indicator names from signal metadata for score nudging."""
    if not isinstance(meta, dict):
        return []
    active = set()

    mods = meta.get("score_modifiers", {}) if isinstance(meta.get("score_modifiers"), dict) else {}
    for name in (
        "bhav_delivery", "cross_asset", "participant_oi", "expiry_regime",
        "sip_boost", "bulk_deal", "theta", "rebalancing", "news", "mtf_pivot",
    ):
        if abs(_safe_float(mods.get(name, meta.get(name)), 0.0)) >= 0.05:
            active.add(name)

    if abs(_safe_float(meta.get("time_bucket"), 1.0) - 1.0) >= 0.05:
        active.add("time_bucket")
    if _safe_float(meta.get("volume_ratio"), 1.0) > 0 and abs(_safe_float(meta.get("volume_ratio"), 1.0) - 1.0) >= 0.20:
        active.add("volume_confirmation")
    if _safe_float(meta.get("eod_indicator_weight"), 0.0) > 0:
        active.add("eod_indicator_weight")
    if _safe_float(meta.get("nr7"), 0.0) > 0 or _safe_float(meta.get("nr4"), 0.0) > 0:
        active.add("volume_confirmation")
    if _safe_float(meta.get("gex_modifier"), 0.0) != 0:
        active.add("mtf_pivot")

    return sorted(active)


def _load_labelled_signals(cutoff: str) -> List[sqlite3.Row]:
    if not SIGNAL_DB.exists():
        return []
    try:
        with _conn(SIGNAL_DB) as conn:
            return conn.execute("""
                SELECT *
                FROM signal_log
                WHERE signal_date >= ?
                  AND tb_label IN (1, -1)
            """, (cutoff,)).fetchall()
    except Exception as exc:
        logger.debug("EOD weights labelled signal load failed: %s", exc)
        return []


def _load_closed_trades(cutoff: str) -> List[sqlite3.Row]:
    if not TRADES_DB.exists():
        return []
    try:
        with _conn(TRADES_DB) as conn:
            return conn.execute("""
                SELECT strategy, realized_pnl, score, metadata, signal_metadata
                FROM trades
                WHERE status = 'CLOSED'
                  AND exit_time IS NOT NULL
                  AND date(exit_time, 'unixepoch', 'localtime') >= ?
            """, (cutoff,)).fetchall()
    except Exception as exc:
        logger.debug("EOD weights closed trade load failed: %s", exc)
        return []


def _metadata_indicators(*raw_values: Any) -> List[str]:
    active = set()
    for raw in raw_values:
        if not raw:
            continue
        try:
            meta = json.loads(raw) if isinstance(raw, str) else raw
            active.update(extract_indicators_from_meta(meta))
        except Exception:
            continue
    return sorted(active)


def _persist_weights(
    strategy_stats: Dict[str, Dict[str, Any]],
    indicator_stats: Dict[str, Dict[str, Any]],
    lookback_days: int,
) -> Dict[str, Any]:
    today = date.today().isoformat()
    strategy_rows = []
    indicator_rows = []

    for strategy, stats in sorted(strategy_stats.items()):
        if not strategy:
            continue
        samples = int(stats["samples"])
        wins = int(stats["wins"])
        losses = int(stats["losses"])
        win_rate = wins / max(samples, 1)
        total_pnl = float(stats["pnl_sum"])
        avg_pnl = total_pnl / max(samples, 1)
        avg_score = float(stats["score_sum"]) / max(samples, 1)
        weight = _weight_from_stats(stats)
        strategy_rows.append({
            "strategy": strategy,
            "samples": samples,
            "executed_trades": int(stats["executed"]),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "avg_pnl": round(avg_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_score": round(avg_score, 4),
            "weight": weight,
            "source": "+".join(sorted(stats["sources"])),
        })

    for indicator, stats in sorted(indicator_stats.items()):
        samples = int(stats["samples"])
        wins = int(stats["wins"])
        losses = int(stats["losses"])
        win_rate = wins / max(samples, 1)
        total_pnl = float(stats["pnl_sum"])
        avg_pnl = total_pnl / max(samples, 1)
        weight = _weight_from_stats(stats)
        indicator_rows.append({
            "indicator": indicator,
            "samples": samples,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "avg_pnl": round(avg_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "weight": weight,
        })

    _ensure_tables()
    with _conn(TRADES_DB) as conn:
        for row in strategy_rows:
            conn.execute("""
                INSERT OR REPLACE INTO eod_strategy_weights
                (date, strategy, samples, executed_trades, wins, losses, win_rate,
                 avg_pnl, total_pnl, avg_score, weight, source, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, row["strategy"], row["samples"], row["executed_trades"],
                row["wins"], row["losses"], row["win_rate"], row["avg_pnl"],
                row["total_pnl"], row["avg_score"], row["weight"], row["source"],
                json.dumps({"lookback_days": lookback_days}),
            ))
        for row in indicator_rows:
            conn.execute("""
                INSERT OR REPLACE INTO eod_indicator_weights
                (date, indicator, samples, wins, losses, win_rate,
                 avg_pnl, total_pnl, weight, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, row["indicator"], row["samples"], row["wins"], row["losses"],
                row["win_rate"], row["avg_pnl"], row["total_pnl"], row["weight"],
                json.dumps({"lookback_days": lookback_days}),
            ))
        conn.commit()

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "date": today,
        "lookback_days": lookback_days,
        "min_samples": MIN_SAMPLES,
        "bounds": {"min": MIN_WEIGHT, "max": MAX_WEIGHT},
        "strategy_weights": {r["strategy"]: r["weight"] for r in strategy_rows},
        "indicator_weights": {r["indicator"]: r["weight"] for r in indicator_rows},
        "top_strategies": sorted(strategy_rows, key=lambda r: r["weight"], reverse=True)[:10],
        "weak_strategies": sorted(strategy_rows, key=lambda r: r["weight"])[:10],
        "top_indicators": sorted(indicator_rows, key=lambda r: r["weight"], reverse=True)[:10],
        "weak_indicators": sorted(indicator_rows, key=lambda r: r["weight"])[:10],
    }
    WEIGHTS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))
    _CACHE["mtime"] = 0.0
    return payload


def run_eod_weight_update(alerts=None, lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    """
    Analyze P&L/labels and produce fresh strategy + indicator weights.
    Safe to run repeatedly; same-day rows are replaced.
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    strategy_stats: Dict[str, Dict[str, Any]] = defaultdict(_empty_stat)
    indicator_stats: Dict[str, Dict[str, Any]] = defaultdict(_empty_stat)

    labelled = _load_labelled_signals(cutoff)
    for row in labelled:
        strategy = str(row["strategy"] or "").strip()
        label = int(row["tb_label"])
        won = label == 1
        score = abs(_safe_float(row["score"], 0.0))
        pnl_proxy = (1.0 if won else -1.0) * max(score, 1.0)

        _add_observation(
            strategy_stats[strategy],
            won=won,
            pnl=pnl_proxy,
            score=score,
            source="triple_barrier",
            executed=False,
        )
        for indicator in _row_active_indicators(row):
            _add_observation(
                indicator_stats[indicator],
                won=won,
                pnl=pnl_proxy,
                score=score,
                source="triple_barrier",
                executed=False,
            )

    closed_trades = _load_closed_trades(cutoff)
    for row in closed_trades:
        strategy = str(row["strategy"] or "").strip()
        pnl = _safe_float(row["realized_pnl"], 0.0)
        score = abs(_safe_float(row["score"], 0.0))
        won = pnl > 0

        _add_observation(
            strategy_stats[strategy],
            won=won,
            pnl=pnl,
            score=score,
            source="closed_trades",
            executed=True,
        )
        for indicator in _metadata_indicators(row["metadata"], row["signal_metadata"]):
            _add_observation(
                indicator_stats[indicator],
                won=won,
                pnl=pnl,
                score=score,
                source="closed_trades",
                executed=True,
            )

    payload = _persist_weights(strategy_stats, indicator_stats, lookback_days)
    logger.info(
        "EOD weights updated: %d strategies, %d indicators, labelled=%d, trades=%d",
        len(payload.get("strategy_weights", {})),
        len(payload.get("indicator_weights", {})),
        len(labelled),
        len(closed_trades),
    )

    if alerts:
        try:
            top_s = payload.get("top_strategies", [])[:3]
            top_i = payload.get("top_indicators", [])[:3]
            lines = [
                "<b>EOD WEIGHTS UPDATED</b>",
                f"  Strategies: {len(payload.get('strategy_weights', {}))}",
                f"  Indicators: {len(payload.get('indicator_weights', {}))}",
                f"  Labelled signals: {len(labelled)}",
                f"  Closed trades: {len(closed_trades)}",
            ]
            if top_s:
                lines.append("  Top strategies: " + ", ".join(
                    f"{r['strategy']} {r['weight']:.2f}x" for r in top_s
                ))
            if top_i:
                lines.append("  Top indicators: " + ", ".join(
                    f"{r['indicator']} {r['weight']:.2f}x" for r in top_i
                ))
            alerts.send("\n".join(lines), dedup_key=f"eod_weights:{date.today()}")
        except Exception:
            pass

    return payload


def load_current_weights() -> Dict[str, Any]:
    try:
        if not WEIGHTS_FILE.exists():
            return {}
        mtime = WEIGHTS_FILE.stat().st_mtime
        if _CACHE.get("mtime") == mtime and _CACHE.get("weights"):
            return _CACHE["weights"]
        data = json.loads(WEIGHTS_FILE.read_text())
        _CACHE["mtime"] = mtime
        _CACHE["weights"] = data
        return data
    except Exception as exc:
        logger.debug("EOD weight load failed: %s", exc)
        return {}


def get_strategy_weight(strategy: str, default: float = 1.0) -> float:
    data = load_current_weights()
    weights = data.get("strategy_weights", {}) if isinstance(data, dict) else {}
    return _safe_float(weights.get(str(strategy or ""), default), default)


def get_indicator_weight(indicator: str, default: float = 1.0) -> float:
    data = load_current_weights()
    weights = data.get("indicator_weights", {}) if isinstance(data, dict) else {}
    return _safe_float(weights.get(str(indicator or ""), default), default)


def apply_learned_weight_nudge(
    score: float,
    strategy: str,
    signal_meta: Dict[str, Any] | None = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Convert EOD weights into a bounded additive score nudge.
    This avoids hard-disabling a strategy from one noisy EOD update.
    """
    base_score = _safe_float(score, 0.0)
    strategy_weight = get_strategy_weight(strategy, 1.0)
    indicators = extract_indicators_from_meta(signal_meta or {})
    indicator_weights = [get_indicator_weight(name, 1.0) for name in indicators]
    avg_indicator_weight = (
        sum(indicator_weights) / len(indicator_weights)
        if indicator_weights else 1.0
    )

    combined = (0.70 * strategy_weight) + (0.30 * avg_indicator_weight)
    nudge = _clamp((combined - 1.0) * 2.0, -MAX_SCORE_NUDGE, MAX_SCORE_NUDGE)
    adjusted = round(base_score + nudge, 4)
    return adjusted, {
        "strategy_weight": round(strategy_weight, 3),
        "indicator_weight": round(avg_indicator_weight, 3),
        "combined_weight": round(combined, 3),
        "nudge": round(nudge, 3),
        "active_indicators": indicators,
    }


if __name__ == "__main__":
    result = run_eod_weight_update()
    print(json.dumps({
        "strategies": len(result.get("strategy_weights", {})),
        "indicators": len(result.get("indicator_weights", {})),
        "updated_at": result.get("updated_at"),
    }, indent=2))
