"""
modifier_edge_holdout.py — day-holdout confirmation for modifier_edge_analyzer's
verdicts (2026-07-29, operator: "based on today's data improvise our system").

Context: modifier_edge_analyzer.py already tests every confluence modifier
(endorsed vs silent, Welch t-test, Bonferroni-corrected, temporal sign-stability
check) but its own conclusion string says explicitly: "Prune/flip is a human
call after a locked-holdout pass — report only." No holdout pass existed yet.
This is that pass, applying the SAME day-based train/holdout discipline already
used in option_cohort_edge_miner.py and strategy_pair_edge_miner.py: split by
DAY (not row, which would leak correlated same-day signals across the split),
re-test significance on the TRAIN half only, then require the HOLDOUT half to
independently confirm the same sign before calling anything CONFIRMED.

This is a read-only measurement tool, same as its two siblings. It does not
prune or re-weight anything -- see pruning.py for the deliberate, reversible,
operator-controlled mechanism that consumes evidence like this.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from modifier_edge_analyzer import _MOD_COLS, _load_clean, EPS, MIN_SAMPLES, MIN_COVERAGE, ALPHA

TRAIN_FRAC = 0.70
MIN_HOLDOUT_N = 15
REPORT_FILE = Path("modifier_edge_holdout_report.json")


def _stat(vals: List[float]) -> Dict[str, Any]:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in vals) / (n - 1)
        sd = math.sqrt(var)
    else:
        sd = 0.0
    return {"n": n, "mean": round(mean, 5), "sd": round(sd, 5)}


def _welch(a: List[float], b: List[float]) -> Tuple[float, float]:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0, 1.0
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return 0.0, 1.0
    t = (ma - mb) / se
    # Welch-Satterthwaite df, normal-approx p-value (matches option_cohort_edge_miner's
    # own one-sample normal-approx convention rather than pulling in scipy here).
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    return t, p


def run(db_path: str = "signal_log.db", days: int = 400) -> Dict[str, Any]:
    df, qc = _load_clean(days)
    if df is None or len(df) == 0:
        return {"error": "no clean signals", "qc": qc}

    all_days = sorted(df["signal_date"].astype(str).unique())
    if len(all_days) < 6:
        return {"error": f"only {len(all_days)} labelled trading days — too thin to holdout-split"}
    cut_idx = max(1, int(len(all_days) * TRAIN_FRAC) - 1)
    cutoff_day = all_days[cut_idx]
    train_df = df[df["signal_date"].astype(str) <= cutoff_day]
    holdout_df = df[df["signal_date"].astype(str) > cutoff_day]

    present = [c for c in _MOD_COLS if c in df.columns]
    results: List[Dict[str, Any]] = []
    n_tests = 0
    per_col: Dict[str, Dict[str, Any]] = {}

    for col in present:
        n_total = len(train_df)
        fired = train_df[train_df[col].abs() > EPS]
        coverage = len(fired) / n_total if n_total else 0.0
        if coverage < MIN_COVERAGE:
            per_col[col] = {"verdict": "DEAD", "train_coverage": round(coverage, 4)}
            continue
        tr_endorsed = train_df[train_df[col] > EPS]["ret"].tolist()
        tr_silent = train_df[train_df[col].abs() <= EPS]["ret"].tolist()
        if len(tr_endorsed) < MIN_SAMPLES or len(tr_silent) < MIN_SAMPLES:
            per_col[col] = {"verdict": "INSUFFICIENT", "train_coverage": round(coverage, 4)}
            continue
        n_tests += 1
        per_col[col] = {
            "coverage": round(coverage, 4),
            "tr_endorsed": tr_endorsed, "tr_silent": tr_silent,
        }

    bonferroni = max(1, n_tests)
    for col, block in per_col.items():
        if block.get("verdict") in ("DEAD", "INSUFFICIENT"):
            results.append({"modifier": col, **block})
            continue
        tr_e, tr_s = block["tr_endorsed"], block["tr_silent"]
        t, p = _welch(tr_e, tr_s)
        train_stat = _stat(tr_e)
        train_lift = train_stat["mean"] - _stat(tr_s)["mean"]
        sig_train = p * bonferroni < ALPHA

        ho_e = holdout_df[holdout_df[col] > EPS]["ret"].tolist()
        ho_s = holdout_df[holdout_df[col].abs() <= EPS]["ret"].tolist()
        ho_lift = (sum(ho_e) / len(ho_e) - sum(ho_s) / len(ho_s)) if ho_e and ho_s else None
        holdout_ok = len(ho_e) >= MIN_HOLDOUT_N and len(ho_s) >= MIN_HOLDOUT_N
        sign_confirmed = bool(
            holdout_ok and ho_lift is not None
            and ((train_lift > 0) == (ho_lift > 0)) and ho_lift != 0
        )

        if sig_train and holdout_ok and sign_confirmed:
            verdict = "CONFIRMED_HELPS" if train_lift > 0 else "CONFIRMED_HURTS"
        elif sig_train and holdout_ok and not sign_confirmed:
            verdict = "TRAIN_ONLY_SIGN_FLIPPED"
        elif sig_train:
            verdict = "TRAIN_ONLY_INSUFFICIENT_HOLDOUT"
        else:
            verdict = "NOISE"

        results.append({
            "modifier": col,
            "coverage": block["coverage"],
            "train": {"n_endorsed": len(tr_e), "n_silent": len(tr_s),
                      "lift": round(train_lift, 4), "t": round(t, 3), "p": round(p, 6)},
            "holdout": {"n_endorsed": len(ho_e), "n_silent": len(ho_s),
                       "lift": round(ho_lift, 4) if ho_lift is not None else None},
            "verdict": verdict,
        })

    results.sort(key=lambda r: (r["verdict"] not in ("CONFIRMED_HELPS", "CONFIRMED_HURTS"),
                                -abs(r.get("train", {}).get("t", 0) or 0)))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "labelled_days": len(all_days), "train_days": cut_idx + 1,
        "holdout_days": len(all_days) - cut_idx - 1, "cutoff_day": cutoff_day,
        "bonferroni_tests": bonferroni,
        "confirmed_helps": [r for r in results if r["verdict"] == "CONFIRMED_HELPS"],
        "confirmed_hurts": [r for r in results if r["verdict"] == "CONFIRMED_HURTS"],
        "all_tested": results,
        "caveat": (
            "Day-split holdout, same discipline as option_cohort_edge_miner.py. "
            f"Only {len(all_days)} labelled days -- treat as directional confirmation, "
            "not proof; re-run as more days accrue before any further action."
        ),
    }
    return report


def main() -> int:
    rep = run()
    if rep.get("error"):
        print(rep["error"])
        return 1
    print(f"=== MODIFIER EDGE HOLDOUT | {rep['labelled_days']} days "
          f"(train {rep['train_days']} / holdout {rep['holdout_days']}, "
          f"cutoff {rep['cutoff_day']}) | Bonferroni x{rep['bonferroni_tests']} ===")
    for r in rep["confirmed_helps"]:
        print(f"  ✅ CONFIRMED_HELPS {r['modifier']}: train_lift={r['train']['lift']} "
              f"t={r['train']['t']} p={r['train']['p']} | holdout_lift={r['holdout']['lift']}")
    for r in rep["confirmed_hurts"]:
        print(f"  ❌ CONFIRMED_HURTS {r['modifier']}: train_lift={r['train']['lift']} "
              f"t={r['train']['t']} p={r['train']['p']} | holdout_lift={r['holdout']['lift']}")
    print("\nAll tested:")
    for r in rep["all_tested"]:
        print(f"  {r['verdict']:>28} {r['modifier']}")
    import json
    REPORT_FILE.write_text(json.dumps(rep, indent=2))
    print(f"\nreport -> {REPORT_FILE}")
    print(rep["caveat"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
