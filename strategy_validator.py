"""
strategy_validator.py

Validates all strategies in signal_log.db using labeled outcomes (tb_label).
Produces strategy_validation_report.json with:
  - Win rate + 90% bootstrap CI
  - Profit factor proxy (unit-R: PF = wins / losses)
  - One-sided binomial p-value vs Indian market breakeven (42%)
  - Status: EDGE_CONFIRMED / MARGINAL / NEGATIVE_EDGE / INSUFFICIENT_DATA

Usage:
  python3 strategy_validator.py
  from strategy_validator import validate_all_strategies
"""
from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

DB_PATH          = "signal_log.db"
REPORT_FILE      = "strategy_validation_report.json"

MIN_SAMPLES      = 15     # minimum labeled signals for any verdict
MIN_EDGE_SAMPLES = 30     # need >= 30 to declare EDGE_CONFIRMED
MIN_SIGNAL_DAYS  = 3      # need signals from at least 3 distinct trading days
BREAKEVEN_WR     = 0.42   # Indian market breakeven after STT + brokerage
EDGE_WR          = 0.55   # win rate threshold for EDGE_CONFIRMED
MARGINAL_WR      = 0.45   # win rate threshold for MARGINAL
BOOTSTRAP_ITER   = 2000


def _bootstrap_ci(outcomes: List[int], n_iter: int = BOOTSTRAP_ITER,
                  ci: float = 0.90) -> Tuple[float, float]:
    """90% bootstrap CI for win rate. outcomes is list of 1 (win) or 0 (loss)."""
    n = len(outcomes)
    if n < 2:
        return 0.0, 1.0
    wrs = []
    for _ in range(n_iter):
        sample = [outcomes[random.randint(0, n - 1)] for _ in range(n)]
        wrs.append(sum(sample) / n)
    wrs.sort()
    alpha = (1 - ci) / 2
    return round(wrs[int(n_iter * alpha)], 4), round(wrs[int(n_iter * (1 - alpha))], 4)


def _binomial_pvalue(wins: int, n: int, p0: float = BREAKEVEN_WR) -> float:
    """
    One-sided binomial test: P(X >= wins | n, p0).
    Lower value = stronger evidence of positive edge above breakeven.
    Uses normal approximation (accurate for n >= 15).
    """
    if n < 1:
        return 1.0
    mean = n * p0
    std  = math.sqrt(n * p0 * (1 - p0))
    if std < 1e-9:
        return 1.0
    z = (wins - 0.5 - mean) / std   # continuity correction
    # P(Z > z) via rational approximation of the complementary error function
    t    = 1.0 / (1.0 + 0.2316419 * abs(z))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
                + t * (-1.821255978 + t * 1.330274429))))
    pdf  = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    p_tail = pdf * poly
    if z >= 0:
        return round(max(0.0, min(1.0, p_tail)), 5)
    return round(max(0.0, min(1.0, 1.0 - p_tail)), 5)


def _load_strategy_data() -> Dict[str, List[int]]:
    """Load labeled outcomes (1=win, 0=loss) from signal_log.db, keyed by strategy."""
    result: Dict[str, List[int]] = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT strategy, tb_label
              FROM signal_log
             WHERE tb_label IN (1, -1)
             ORDER BY id
        """).fetchall()
        conn.close()
        for strat, label in rows:
            if strat not in result:
                result[strat] = []
            result[strat].append(1 if label == 1 else 0)
    except Exception as exc:
        logger.warning("strategy_validator: signal_log load error: %s", exc)
    return result


def _load_strategy_day_counts() -> Dict[str, int]:
    """Count distinct signal_dates per strategy (labeled signals only)."""
    result: Dict[str, int] = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT strategy, COUNT(DISTINCT signal_date) AS n_days
              FROM signal_log
             WHERE tb_label IN (1, -1)
             GROUP BY strategy
        """).fetchall()
        conn.close()
        for strat, n_days in rows:
            result[strat] = int(n_days)
    except Exception as exc:
        logger.warning("strategy_validator: day_count load error: %s", exc)
    return result


def _validate_strategy(strategy: str, outcomes: List[int], n_days: int = 0) -> Dict[str, Any]:
    """Validate a single strategy given its binary outcome list."""
    n      = len(outcomes)
    wins   = sum(outcomes)
    losses = n - wins
    wr     = wins / n if n > 0 else 0.0

    ci_lo, ci_hi = _bootstrap_ci(outcomes)
    pvalue       = _binomial_pvalue(wins, n)
    pf           = round(wins / max(losses, 1), 3)
    edge_score   = round((wr - BREAKEVEN_WR) / BREAKEVEN_WR, 4)

    # Max consecutive losses
    max_consec = 0
    run = 0
    for o in outcomes:
        if o == 0:
            run += 1
            max_consec = max(max_consec, run)
        else:
            run = 0

    if n < MIN_SAMPLES:
        status = "INSUFFICIENT_DATA"
    elif wr < BREAKEVEN_WR:
        status = "NEGATIVE_EDGE"
    elif (wr >= EDGE_WR and n >= MIN_EDGE_SAMPLES and ci_lo >= BREAKEVEN_WR
          and (n_days == 0 or n_days >= MIN_SIGNAL_DAYS)):
        status = "EDGE_CONFIRMED"
    elif wr >= MARGINAL_WR and ci_lo >= BREAKEVEN_WR - 0.05:
        status = "MARGINAL"
    else:
        status = "MARGINAL"

    return {
        "strategy":            strategy,
        "status":              status,
        "n":                   n,
        "n_days":              n_days,
        "wins":                wins,
        "losses":              losses,
        "win_rate":            round(wr, 4),
        "ci_90_lo":            ci_lo,
        "ci_90_hi":            ci_hi,
        "profit_factor":       pf,
        "edge_score":          edge_score,
        "p_value":             pvalue,
        "significant_edge":    pvalue < 0.05,
        "max_consec_loss":     max_consec,
        "edge_above_breakeven": wr > BREAKEVEN_WR,
        "generated_at":        datetime.now().isoformat(),
    }


def validate_all_strategies(save: bool = True) -> Dict[str, Any]:
    """Validate all strategies in signal_log.db. Writes strategy_validation_report.json."""
    random.seed(42)
    strategy_data = _load_strategy_data()
    day_counts    = _load_strategy_day_counts()
    results = {}
    for strat, outcomes in strategy_data.items():
        results[strat] = _validate_strategy(strat, outcomes, n_days=day_counts.get(strat, 0))

    edge_confirmed = [s for s, r in results.items() if r["status"] == "EDGE_CONFIRMED"]
    marginal       = [s for s, r in results.items() if r["status"] == "MARGINAL"]
    negative_edge  = [s for s, r in results.items() if r["status"] == "NEGATIVE_EDGE"]
    insufficient   = [s for s, r in results.items() if r["status"] == "INSUFFICIENT_DATA"]

    report = {
        "generated_at":       datetime.now().isoformat(),
        "breakeven_wr":       BREAKEVEN_WR,
        "total_strategies":   len(results),
        "edge_confirmed":     edge_confirmed,
        "marginal":           marginal,
        "negative_edge":      negative_edge,
        "insufficient_data":  insufficient,
        "strategies":         results,
    }

    if save:
        Path(REPORT_FILE).write_text(json.dumps(report, indent=2))
        logger.info("strategy_validation_report.json: %d strategies validated", len(results))

    return report


def print_report(report: Dict[str, Any]) -> None:
    strats = sorted(report["strategies"].values(), key=lambda r: r["win_rate"], reverse=True)
    print(f"\n{'='*72}")
    print(f"  STRATEGY VALIDATION REPORT  ({report['generated_at'][:10]})")
    print(f"  Breakeven: {report['breakeven_wr']:.0%}  |  "
          f"Edge confirmed: {len(report['edge_confirmed'])}  |  "
          f"Negative edge: {len(report['negative_edge'])}")
    print(f"{'='*72}")
    print(f"{'Strategy':<36} {'N':>5} {'WR':>6} {'PF':>5} {'CI-lo':>6} {'p':>6}  Status")
    print(f"{'-'*72}")
    for r in strats:
        star = " ←" if r["status"] == "EDGE_CONFIRMED" else ""
        pv   = f"{r['p_value']:.3f}" if r["p_value"] < 0.1 else "  n.s."
        print(f"{r['strategy']:<36} {r['n']:>5} {r['win_rate']:>5.0%} "
              f"{r['profit_factor']:>5.2f} {r['ci_90_lo']:>5.0%} {pv:>6}  "
              f"{r['status']}{star}")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = validate_all_strategies()
    print_report(report)
