"""
eod_setup_edge_analyzer.py — day-holdout, Bonferroni-corrected significance
test for eod_signal_miner's setups and factors (2026-07-14, operator: "take
all indicators, change parameters, finalize... score it rank it and we can
test... is there any system").

eod_signal_miner.py scans every symbol/bar and reports "Best Setups"/"Best
Factors" ranked by raw avg_return — but with NO significance test and NO
day-holdout split, and (until persist_candidates was added the same day) no
accumulation across nights at all, so there was never enough history to
run one. This module is the missing rigor layer, same discipline already
used in modifier_edge_analyzer.py / option_live_edge_policy.py /
option_cohort_edge_miner.py:

  1. Day-split (not row-split — avoids leaking a correlated same-day batch
     across train/holdout).
  2. After-cost return test per setup and per factor.
  3. Bonferroni correction across every setup+factor tested.
  4. CANDIDATE only when train clears corrected significance AND the unseen
     holdout has a positive one-sided 95% lower confidence bound after costs.

Read-only report (eod_setup_edge_report.json). Promotion of any surviving
candidate goes through the same forward-holdout ledger discipline as the
rest of this system — this module measures, it does not wire.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from eod_signal_miner import MINER_DB, ensure_miner_schema

logger = logging.getLogger(__name__)

REPORT_FILE = Path("eod_setup_edge_report.json")
TRAIN_FRAC = 0.70
MIN_TRAIN_N = 30
MIN_HOLDOUT_N = 15
MIN_HOLDOUT_DAYS = 2
ALPHA = 0.05
COST_PCT = float(os.getenv("EDGE_ANALYZER_COST_PCT", "0.12"))


def _stat(rets: List[float], cost_pct: float = COST_PCT) -> Dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0}
    gross_mean = sum(rets) / n
    net_rets = [value - cost_pct for value in rets]
    net_mean = sum(net_rets) / n
    sd = 0.0
    if n > 1:
        var = sum((x - net_mean) ** 2 for x in net_rets) / (n - 1)
        sd = math.sqrt(var)
    standard_error = sd / math.sqrt(n) if sd > 0 else 0.0
    t = net_mean / standard_error if standard_error > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    gross_win = sum(1 for x in rets if x > 0) / n
    net_win = sum(1 for x in net_rets if x > 0) / n
    return {"n": n, "mean_return_pct": round(gross_mean, 4),
            "net_mean_return_pct": round(net_mean, 4),
            "net_return_lcb95_pct": round(net_mean - 1.645 * standard_error, 4),
            "win_rate": round(gross_win, 3), "net_win_rate": round(net_win, 3),
            "t": round(t, 2), "p": round(p, 5)}


def _verdict(train: Dict[str, Any], holdout: Dict[str, Any], bonferroni: int) -> str:
    if train.get("n", 0) < MIN_TRAIN_N:
        return "INSUFFICIENT_DATA"
    sig = train["p"] * bonferroni < ALPHA
    train_net = train.get("net_mean_return_pct", 0)
    holdout_net = holdout.get("net_mean_return_pct", 0)
    held = (holdout.get("n", 0) >= MIN_HOLDOUT_N
            and holdout_net * train_net > 0
            and holdout.get("net_return_lcb95_pct", float("-inf")) > 0)
    if sig and train_net > 0 and held:
        return "CANDIDATE"
    if sig and train_net > 0:
        return "TRAIN_ONLY_OVERFIT"
    if sig and train_net < 0:
        return "HURTS"
    return "NOISE"


def _write_gated_report(reason: str, days_available: int) -> Dict[str, Any]:
    """Persist a report even while gated on insufficient days — otherwise
    the artifact goes stale forever (indistinguishable from a broken
    pipeline) instead of honestly reporting 'accumulating, not enough data
    yet' every run. Same fix applied to data_quality_watchdog.py same day."""
    report = {"error": reason, "days_available": days_available,
              "generated_at": datetime.now().isoformat(timespec="seconds")}
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("gated report write: %s", exc)
    return report


def run(db_path: str = MINER_DB, min_days: int = 6,
        cost_pct: float = COST_PCT) -> Dict[str, Any]:
    if not math.isfinite(cost_pct) or cost_pct < 0:
        raise ValueError("cost_pct must be a finite, non-negative percentage")
    with sqlite3.connect(db_path) as conn:
        ensure_miner_schema(conn)
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT candidate_date FROM eod_mined_candidates "
            "WHERE label IN (-1,0,1) ORDER BY 1")]
        if len(days) < min_days:
            return _write_gated_report(
                f"only {len(days)} distinct mined trading days "
                f"(need >= {min_days}) — accumulating, check back later",
                len(days))
        cut_idx = max(1, int(len(days) * TRAIN_FRAC) - 1)
        cutoff = days[cut_idx]
        holdout_days = len(days) - cut_idx - 1
        if holdout_days < MIN_HOLDOUT_DAYS:
            return _write_gated_report(
                f"only {holdout_days} holdout day(s) after a "
                f"{TRAIN_FRAC:.0%} split (need >= {MIN_HOLDOUT_DAYS}) — "
                "accumulating, check back later",
                len(days))

        rows = conn.execute(
            "SELECT setup, factors, return_pct, candidate_date FROM eod_mined_candidates "
            "WHERE label IN (-1,0,1)").fetchall()

    setups: Dict[str, Tuple[List[float], List[float]]] = {}
    factors: Dict[str, Tuple[List[float], List[float]]] = {}
    for setup, factor_str, ret, d in rows:
        train_slot = d <= cutoff
        s_tr, s_ho = setups.setdefault(setup, ([], []))
        (s_tr if train_slot else s_ho).append(float(ret))
        for f in (factor_str or "").split(","):
            if not f:
                continue
            f_tr, f_ho = factors.setdefault(f, ([], []))
            (f_tr if train_slot else f_ho).append(float(ret))

    n_tests = sum(1 for tr, _ in setups.values() if len(tr) >= MIN_TRAIN_N)
    n_tests += sum(1 for tr, _ in factors.values() if len(tr) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)

    results: List[Dict[str, Any]] = []
    for kind, group in (("setup", setups), ("factor", factors)):
        for name, (tr, ho) in group.items():
            tr_stat = _stat(tr, cost_pct)
            if tr_stat.get("n", 0) < MIN_TRAIN_N:
                continue
            ho_stat = _stat(ho, cost_pct)
            verdict = _verdict(tr_stat, ho_stat, bonferroni)
            results.append({"kind": kind, "name": name, "train": tr_stat,
                            "holdout": ho_stat, "verdict": verdict})

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["train"].get("t", 0) or 0)))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cost_pct": cost_pct,
        "mined_days": len(days), "train_days": cut_idx + 1, "holdout_days": holdout_days,
        "cutoff_day": cutoff, "bonferroni_tests": bonferroni,
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "hurts": [r for r in results if r["verdict"] == "HURTS"][:15],
        "all_tested": results,
    }
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("report write: %s", exc)
    return report


def main() -> int:
    rep = run()
    if rep.get("error"):
        print(rep["error"])
        return 1
    print(f"=== EOD SETUP/FACTOR EDGE | {rep['mined_days']} mined days "
          f"(train {rep['train_days']} / holdout {rep['holdout_days']}) | "
              f"Bonferroni x{rep['bonferroni_tests']} | cost {rep['cost_pct']:.3f}% ===")
    cands = rep["candidates"]
    print(f"\nCANDIDATES surviving train-significance + holdout: {len(cands)}")
    for r in cands:
        print(f"  ✅ {r['kind']}:{r['name']} train n={r['train']['n']} "
              f"net={r['train']['net_mean_return_pct']}% t={r['train']['t']} p={r['train']['p']} | "
              f"holdout n={r['holdout'].get('n')} net={r['holdout'].get('net_mean_return_pct')}%")
    print("\nTop by |t| (regardless of verdict):")
    for r in sorted(rep["all_tested"], key=lambda r: -abs(r["train"].get("t", 0) or 0))[:10]:
        print(f"  {r['verdict']:>18} {r['kind']}:{r['name']}: train n={r['train']['n']} "
              f"net={r['train']['net_mean_return_pct']}% t={r['train']['t']} p={r['train']['p']} | "
              f"holdout n={r['holdout'].get('n')} net={r['holdout'].get('net_mean_return_pct')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
