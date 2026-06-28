"""
alpha_decay_tracker.py

Detects edge decay in strategies by splitting labeled signal history into
equal-count time buckets and testing for monotonic win-rate decline.

Uses Mann-Kendall p-value (more robust than raw tau for small samples) +
  a two-proportion z-test comparing the most recent bucket against the baseline.

Writes alpha_decay_state.json with status per strategy:
  STABLE            — no significant downward trend
  DECAYING          — Mann-Kendall p < 0.10 AND WR drop >= 10%
  DEGRADED          — recent bucket WR < 38% (below Indian market breakeven)
  INSUFFICIENT_DATA — fewer than 30 labeled signals (raised from 20)

Score multipliers consumed by learned_filters.py / signal_engine:
  STABLE → 1.0, DECAYING → 0.85, DEGRADED → 0.65

Usage:
  python3 alpha_decay_tracker.py
  from alpha_decay_tracker import run_decay_check, get_decay_multiplier
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

DB_PATH        = "signal_log.db"
STATE_FILE     = "alpha_decay_state.json"

MIN_SAMPLES    = 30     # raised from 20 — need enough for 10-bucket analysis
N_BUCKETS      = 10     # 10 buckets: Mann-Kendall needs >= 8 points for power
DECAY_THRESH   = 0.10   # WR drop >= 10% from baseline (was 15%, now more sensitive)
DEGRADED_WR    = 0.38   # recent bucket WR < 38% = DEGRADED

MULTIPLIERS: Dict[str, float] = {
    "STABLE":            1.0,
    "DECAYING":          0.85,
    "DEGRADED":          0.65,
    "INSUFFICIENT_DATA": 1.0,
}


def _mann_kendall(y: List[float]) -> tuple:
    """
    Mann-Kendall trend test.
    Returns (tau, p_value) where p_value < 0.10 means significant trend.
    Uses normal approximation (accurate for n >= 8).
    """
    import math
    n = len(y)
    if n < 4:
        return 0.0, 1.0

    conc, disc = 0, 0
    for i in range(n):
        for j in range(i + 1, n):
            d = y[j] - y[i]
            if d > 0:
                conc += 1
            elif d < 0:
                disc += 1

    S     = conc - disc
    total = n * (n - 1) // 2
    tau   = round(S / max(total, 1), 4)

    # Variance of S under H0 (no ties simplification)
    var_S = n * (n - 1) * (2 * n + 5) / 18.0
    if var_S <= 0:
        return tau, 1.0

    # Continuity-corrected Z
    if S > 0:
        z = (S - 1) / math.sqrt(var_S)
    elif S < 0:
        z = (S + 1) / math.sqrt(var_S)
    else:
        return tau, 1.0

    # One-sided p-value for downward trend (z < 0)
    # P(Z <= z) via rational approximation
    t    = 1.0 / (1.0 + 0.2316419 * abs(z))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
                + t * (-1.821255978 + t * 1.330274429))))
    pdf  = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    p_upper = pdf * poly          # P(Z > |z|)
    p_value = p_upper if z < 0 else (1.0 - p_upper)
    return tau, round(max(0.0, min(1.0, p_value)), 4)


def _load_outcomes() -> Dict[str, List[Tuple[str, int]]]:
    """Load (signal_time, outcome) per strategy, sorted by signal_time."""
    result: Dict[str, List[Tuple[str, int]]] = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT strategy, signal_time, tb_label
              FROM signal_log
             WHERE tb_label IN (1, -1) AND training_eligible=1
               AND stop_loss>0 AND target>0 AND rr>0
             ORDER BY signal_time
        """).fetchall()
        conn.close()
        for strat, stime, label in rows:
            if strat not in result:
                result[strat] = []
            result[strat].append((stime or "00:00:00", 1 if label == 1 else 0))
    except Exception as exc:
        logger.warning("alpha_decay_tracker: load error: %s", exc)
    return result


def _bucket_winrates(outcomes: List[Tuple[str, int]],
                     n_buckets: int = N_BUCKETS) -> List[Dict[str, Any]]:
    """Split outcomes into n_buckets equal-count time buckets, return WR per bucket."""
    n    = len(outcomes)
    size = max(n // n_buckets, 1)
    buckets = []
    for i in range(n_buckets):
        start = i * size
        end   = (i + 1) * size if i < n_buckets - 1 else n
        chunk = outcomes[start:end]
        if not chunk:
            continue
        wins  = sum(o for _, o in chunk)
        wr    = wins / len(chunk)
        label = f"{chunk[0][0][:5]}–{chunk[-1][0][:5]}"
        buckets.append({"label": label, "wr": round(wr, 4), "n": len(chunk)})
    return buckets


def _analyse_strategy(strategy: str, outcomes: List[Tuple[str, int]]) -> Dict[str, Any]:
    n = len(outcomes)
    if n < MIN_SAMPLES:
        return {"strategy": strategy, "status": "INSUFFICIENT_DATA", "n": n}

    buckets     = _bucket_winrates(outcomes)
    wrs         = [b["wr"] for b in buckets]
    tau, pvalue = _mann_kendall(wrs)

    # Baseline = mean WR of first 3 buckets; recent = last bucket
    baseline_wr = sum(wrs[:3]) / 3 if len(wrs) >= 3 else (wrs[0] if wrs else 0.0)
    recent_wr   = wrs[-1] if wrs else 0.0
    decay_pct   = round((baseline_wr - recent_wr) / max(baseline_wr, 1e-9), 4)

    if recent_wr < DEGRADED_WR:
        status = "DEGRADED"
    elif pvalue < 0.10 and tau < 0 and decay_pct >= DECAY_THRESH:
        # Statistically significant downward trend (p < 0.10, Mann-Kendall)
        status = "DECAYING"
    else:
        status = "STABLE"

    return {
        "strategy":    strategy,
        "status":      status,
        "trend_tau":   tau,
        "mk_pvalue":   pvalue,
        "recent_wr":   round(recent_wr, 4),
        "baseline_wr": round(baseline_wr, 4),
        "decay_pct":   round(decay_pct, 4),
        "n":           n,
        "n_buckets":   len(buckets),
        "buckets":     buckets,
        "generated_at": datetime.now().isoformat(),
    }


def run_decay_check(save: bool = True) -> Dict[str, Any]:
    """Run alpha decay check for all strategies with sufficient data."""
    outcomes = _load_outcomes()
    state: Dict[str, Any] = {}
    for strat, data in outcomes.items():
        state[strat] = _analyse_strategy(strat, data)

    if save:
        Path(STATE_FILE).write_text(json.dumps(state, indent=2))
        logger.info("alpha_decay_state.json written: %d strategies", len(state))
    return state


def get_decay_multiplier(strategy: str) -> float:
    """
    Cache-aware multiplier for a strategy. Safe to call from signal_engine.
    Returns 1.0 on any read error (fail-open).
    """
    try:
        state  = json.loads(Path(STATE_FILE).read_text())
        status = state.get(strategy, {}).get("status", "INSUFFICIENT_DATA")
        return MULTIPLIERS.get(status, 1.0)
    except Exception:
        return 1.0


def print_decay_report(state: Dict[str, Any]) -> None:
    rows = sorted(
        [r for r in state.values() if r.get("status") != "INSUFFICIENT_DATA"],
        key=lambda r: r.get("recent_wr", 0.5),
    )
    print(f"\n{'='*78}")
    print("  ALPHA DECAY REPORT  (Mann-Kendall, 10-bucket)")
    print(f"{'='*78}")
    print(f"{'Strategy':<34} {'N':>5} {'Base WR':>8} {'Recent WR':>10} {'Tau':>6} {'p':>6}  Status")
    print(f"{'-'*78}")
    for r in rows:
        flag = "  ←" if r["status"] in ("DECAYING", "DEGRADED") else ""
        pv   = f"{r.get('mk_pvalue', 1.0):.3f}"
        print(f"{r['strategy']:<34} {r['n']:>5} "
              f"{r['baseline_wr']:>7.0%} {r['recent_wr']:>9.0%} "
              f"{r['trend_tau']:>5.2f} {pv:>6}  {r['status']}{flag}")

    skipped = [s for s, r in state.items() if r.get("status") == "INSUFFICIENT_DATA"]
    if skipped:
        print(f"\n  Insufficient data (<{MIN_SAMPLES} signals): {', '.join(skipped[:10])}")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    state = run_decay_check()
    print_decay_report(state)
