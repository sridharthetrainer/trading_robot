"""
option_cohort_edge_miner.py — day-holdout, Bonferroni-corrected search for
any option-flow cohort with a genuinely surviving edge (2026-07-13,
operator: "based on all our historical data improvise and increase the
profit").

Context: option_live_edge_policy.cohort_policy already buckets signals by
(flow, direction, score_band) and computes win-rate/avg-R/profit-factor —
but on the FULL sample with no significance test and no holdout split.
With ~40 simultaneous cohorts checked nightly, a promising-looking cohort
at n=30 (its own minimum) is exactly the multiple-testing regime that
produces false positives — and a false "PAPER_PROMISING" cohort gets a
real score boost + tradable=True downstream (option_multistrike_signals).

This module runs the SAME discipline already used elsewhere in this repo
(validation_harness / strategy_edge_analyzer._holdout_check /
structure_reverse_engineer.py): split by DAY (not by row, which would leak
correlated same-batch signals across the split), Bonferroni-correct across
every cohort tested, and require the holdout to independently confirm
sign and hold up before calling anything a candidate. Read-only report;
promotion (if anything survives) goes through the existing forward-holdout
ledger machinery, never straight into live scoring.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

TRAIN_FRAC = 0.70
MIN_TRAIN_N = 40
MIN_TRAIN_DAYS = 3
ALPHA = 0.05
REPORT_FILE = Path("option_cohort_edge_report.json")

_DTE_SQL = """
CASE
  WHEN julianday(expiry) - julianday(substr(snapshot_time,1,10)) <= 0 THEN 'dte:0'
  WHEN julianday(expiry) - julianday(substr(snapshot_time,1,10)) <= 2 THEN 'dte:1-2'
  WHEN julianday(expiry) - julianday(substr(snapshot_time,1,10)) <= 7 THEN 'dte:3-7'
  ELSE 'dte:8plus'
END
"""
_PREMIUM_SQL = """
CASE
  WHEN price < 5 THEN 'premium:lt5'
  WHEN price < 15 THEN 'premium:5-15'
  WHEN price < 35 THEN 'premium:15-35'
  ELSE 'premium:ge35'
END
"""
_SCORE_SQL = """
CASE
  WHEN score < 50 THEN 'score:lt50'
  WHEN score < 70 THEN 'score:50-70'
  WHEN score < 85 THEN 'score:70-85'
  ELSE 'score:85plus'
END
"""

# (label, group-by SQL expr) — single dimensions + a few curated pairwise
# combos already meaningful to the live cohort_policy (flow x score, flow x
# dte). Kept small deliberately: every combo tested inflates the Bonferroni
# correction, and this dataset is only 16 trading days.
_DIMENSIONS: List[Tuple[str, str]] = [
    ("underlying", "underlying"),
    ("flow", "flow"),
    ("option_type", "option_type"),
    ("direction", "direction"),
    ("signal", "signal"),
    ("dte_bucket", _DTE_SQL),
    ("premium_bucket", _PREMIUM_SQL),
    ("score_band", _SCORE_SQL),
    ("tradable", "tradable"),
    # 2026-07-16: surfaces strategy-level verdicts for the multi-strategy
    # option catalog (option_strategy_registry.py). Rows written before this
    # were backfilled to 'single_strike_flow' (option_multistrike_signals.py
    # ensure_multistrike_schema), so that cohort is measured here too, not
    # silently excluded.
    ("strategy", "strategy"),
    ("flow_x_score", f"flow || ':' || ({_SCORE_SQL})"),
    ("flow_x_dte", f"flow || ':' || ({_DTE_SQL})"),
    ("underlying_x_flow", "underlying || ':' || flow"),
]


def _stat(rets: List[float]) -> Dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0}
    mean = sum(rets) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in rets) / (n - 1)
        sd = math.sqrt(var)
    else:
        sd = 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    win = sum(1 for x in rets if x > 0) / n
    return {"n": n, "mean_net_r": round(mean, 5), "win_rate": round(win, 3),
            "t": round(t, 2), "p": round(p, 5)}


def run(db_path: str = "option_chain_snapshots.db") -> Dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT substr(snapshot_time,1,10) FROM option_strike_signals "
            "WHERE outcome_label IN (-1,0,1) ORDER BY 1")]
        if len(days) < 6:
            return {"error": f"only {len(days)} labelled trading days — too thin to holdout-split"}
        cut_idx = max(1, int(len(days) * TRAIN_FRAC) - 1)
        cutoff_day = days[cut_idx]

        results: List[Dict[str, Any]] = []
        n_tests = 0
        per_dim_cohorts: Dict[str, List[Tuple[Any, List[float], List[float]]]] = {}
        for dim_label, expr in _DIMENSIONS:
            rows = conn.execute(
                f"""SELECT {expr} AS cohort, substr(snapshot_time,1,10) AS d, net_r
                      FROM option_strike_signals
                     WHERE outcome_label IN (-1,0,1) AND net_r IS NOT NULL""").fetchall()
            by_cohort: Dict[Any, Tuple[List[float], List[float]]] = {}
            for cohort, d, net_r in rows:
                train, holdout = by_cohort.setdefault(cohort, ([], []))
                (train if d <= cutoff_day else holdout).append(float(net_r))
            per_dim_cohorts[dim_label] = [
                (cohort, tr, ho) for cohort, (tr, ho) in by_cohort.items()
            ]
            n_tests += sum(1 for _, tr, _ in per_dim_cohorts[dim_label] if len(tr) >= MIN_TRAIN_N)

        bonferroni = max(1, n_tests)
        for dim_label, cohorts in per_dim_cohorts.items():
            for cohort, train, holdout in cohorts:
                tr = _stat(train)
                if tr.get("n", 0) < MIN_TRAIN_N:
                    continue
                ho = _stat(holdout)
                sig_train = tr["p"] * bonferroni < ALPHA
                held = ho.get("n", 0) >= 15 and ho.get("mean_net_r", -1) > 0
                if sig_train and tr["mean_net_r"] > 0 and held:
                    verdict = "CANDIDATE"
                elif sig_train and tr["mean_net_r"] > 0:
                    verdict = "TRAIN_ONLY_OVERFIT"
                elif sig_train and tr["mean_net_r"] < 0:
                    verdict = "HURTS"
                else:
                    verdict = "NOISE"
                results.append({"dimension": dim_label, "cohort": str(cohort),
                                "train": tr, "holdout": ho, "verdict": verdict})

        # Rows are not independent: several strikes in one minute express one
        # underlying decision. Publish a cluster-level summary alongside the
        # legacy per-row tests so apparent sample size is never mistaken for
        # independent evidence.
        cluster_rows = [
            {
                "underlying": row[0], "snapshot_time": row[1],
                "direction": row[2], "net_r": row[3],
            }
            for row in conn.execute(
                """SELECT underlying,snapshot_time,direction,net_r
                     FROM option_strike_signals
                    WHERE outcome_label IN (-1,0,1) AND net_r IS NOT NULL"""
            )
        ]

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["train"].get("t", 0) or 0)))
    from option_institutional_controls import clustered_evidence
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "labelled_days": len(days), "train_days": cut_idx + 1,
        "holdout_days": len(days) - cut_idx - 1, "cutoff_day": cutoff_day,
        "bonferroni_tests": bonferroni,
        "clustered_evidence": clustered_evidence(cluster_rows),
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "hurts": [r for r in results if r["verdict"] == "HURTS"][:15],
        "all_tested": results,
        "caveat": "Per-row t-stats remain for continuity, but promotion must "
                  "use clustered_evidence plus the day holdout because "
                  "same-direction strikes in one minute are one decision.",
    }
    return report


def main() -> int:
    rep = run()
    if rep.get("error"):
        print(rep["error"])
        return 1
    print(f"=== OPTION COHORT EDGE MINER | {rep['labelled_days']} days "
          f"(train {rep['train_days']} / holdout {rep['holdout_days']}, "
          f"cutoff {rep['cutoff_day']}) | Bonferroni x{rep['bonferroni_tests']} ===")
    cands = rep["candidates"]
    print(f"\nCANDIDATES surviving train-significance + holdout: {len(cands)}")
    for r in cands:
        print(f"  ✅ {r['dimension']}={r['cohort']}: train n={r['train']['n']} "
              f"R={r['train']['mean_net_r']} t={r['train']['t']} p={r['train']['p']} | "
              f"holdout n={r['holdout'].get('n')} R={r['holdout'].get('mean_net_r')}")
    print("\nTop by |t| (regardless of verdict):")
    for r in sorted(rep["all_tested"], key=lambda r: -abs(r["train"].get("t", 0) or 0))[:10]:
        print(f"  {r['verdict']:>18} {r['dimension']}={r['cohort']}: "
              f"train n={r['train']['n']} R={r['train']['mean_net_r']} "
              f"t={r['train']['t']} p={r['train']['p']} | "
              f"holdout n={r['holdout'].get('n')} R={r['holdout'].get('mean_net_r')}")
    REPORT_FILE.write_text(json.dumps(rep, indent=2))
    print(f"\nreport -> {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
