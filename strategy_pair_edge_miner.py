"""
strategy_pair_edge_miner.py — day-holdout, Bonferroni-corrected search for any
STRATEGY-COMBINATION co-occurrence cohort (N strategies agreeing together in
signal_log.agreeing_strats) with a genuinely surviving cost-adjusted edge
(2026-07-23, operator: "consider all indicators/strategies as separate
entities ... have confluence to finalize ... lots of combinations" --
extended same day to combos beyond pairs, operator: "go beyond pairs").

Context: signal_engine.generate_signal already blends ~130 strategy functions
into ONE confluence score per signal, and modifier_edge_analyzer.py already
measures each of ~28 individual confluence MODIFIERS' edge nightly. Neither
asks the specific question this module asks: does a SPECIFIC COMBINATION of
strategies, when they co-fire (all appear in that signal's agreeing_strats
list), show a real, cost-clearing net_R edge -- distinct from any of them
firing alone?

This reuses the exact discipline of option_cohort_edge_miner.py (day-level
holdout split so within-day-correlated signals never leak across train/test,
Bonferroni correction across every combination actually tested, a
CANDIDATE/TRAIN_ONLY_OVERFIT/HURTS/NOISE verdict ladder) rather than
inventing a new one -- testing many combinations against the same fixed
history is exactly the multiple-testing trap that discipline exists to
catch, and this dataset (17 trading days as of 2026-07-23) is exactly as
thin as the option miner's own 16-day one. combo_size=3 (C(25,3)=2300
candidate triples before the min-n filter) makes the Bonferroni-corrected
alpha far harsher than combo_size=2 (300 pairs) -- that's the correct,
unavoidable price of testing more combinations against the same fixed
history, not a bug to work around.

Read-only report. Nothing here re-weights confluence or promotes anything
into live scoring -- same as every other miner in this repo. Standalone
script (no run_nightly / no pipeline wiring) -- same precedent as
option_cohort_edge_miner.py, which is also unwired.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

TOP_N_STRATEGIES = 25
TRAIN_FRAC = 0.70
MIN_TRAIN_N = 40
ALPHA = 0.05

_LOAD_SQL = """
    SELECT signal_date, agreeing_strats, tb_r_multiple_net
      FROM signal_log
     WHERE tb_label IN (1,0,-1) AND training_eligible=1
       AND tb_r_multiple_net IS NOT NULL
"""


def _report_file(combo_size: int) -> Path:
    suffix = {2: "pair", 3: "triple"}.get(combo_size, f"combo{combo_size}")
    return Path(f"strategy_{suffix}_edge_report.json")


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


def _parse_row(raw: Optional[str]) -> Set[str]:
    if not raw:
        return set()
    try:
        lst = json.loads(raw)
    except Exception:
        return set()
    if not isinstance(lst, list):
        return set()
    return {str(s) for s in lst if s}


def _load(conn: sqlite3.Connection) -> List[Tuple[str, Set[str], float]]:
    rows = conn.execute(_LOAD_SQL).fetchall()
    out: List[Tuple[str, Set[str], float]] = []
    for signal_date, agreeing_raw, net_r in rows:
        strategies = _parse_row(agreeing_raw)
        if not strategies:
            continue
        out.append((str(signal_date), strategies, float(net_r)))
    return out


def _top_strategies(loaded: List[Tuple[str, Set[str], float]],
                     n: int = TOP_N_STRATEGIES) -> List[str]:
    """Candidate universe chosen algorithmically by co-occurrence frequency,
    not hand-picked -- caps all-combos at C(n,combo_size) so the Bonferroni
    correction stays passable given only ~17 labelled trading days."""
    counter: Counter = Counter()
    for _, strategies, _ in loaded:
        counter.update(strategies)
    return [s for s, _ in counter.most_common(n)]


def _index_by_strategy(rows: List[Tuple[Set[str], float]],
                        candidates: List[str]) -> Dict[str, Set[int]]:
    """row-index sets per candidate strategy, so a combo's cohort is a fast
    set-intersection instead of a full re-scan of `rows` per combo tested
    (matters once combo_size=3 pushes candidate combos into the thousands)."""
    cand_set = set(candidates)
    idx: Dict[str, Set[int]] = {s: set() for s in candidates}
    for i, (strategies, _net_r) in enumerate(rows):
        for s in strategies & cand_set:
            idx[s].add(i)
    return idx


def run(db_path: str = "signal_log.db",
        top_n: int = TOP_N_STRATEGIES,
        combo_size: int = 2) -> Dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        loaded = _load(conn)
    if not loaded:
        return {"error": "no labelled, cost-scored signals found"}

    days = sorted({d for d, _, _ in loaded})
    if len(days) < 6:
        return {"error": f"only {len(days)} labelled trading days — too thin to holdout-split"}
    cut_idx = max(1, int(len(days) * TRAIN_FRAC) - 1)
    cutoff_day = days[cut_idx]

    candidates = _top_strategies(loaded, top_n)
    train_rows = [(strategies, net_r) for d, strategies, net_r in loaded if d <= cutoff_day]
    holdout_rows = [(strategies, net_r) for d, strategies, net_r in loaded if d > cutoff_day]

    train_idx = _index_by_strategy(train_rows, candidates)
    holdout_idx = _index_by_strategy(holdout_rows, candidates)

    per_combo: List[Dict[str, Any]] = []
    n_tests = 0
    for combo in itertools.combinations(sorted(candidates), combo_size):
        train_ids = set.intersection(*(train_idx[s] for s in combo))
        if len(train_ids) < MIN_TRAIN_N:
            continue
        n_tests += 1
        holdout_ids = set.intersection(*(holdout_idx[s] for s in combo))
        train_all = [train_rows[i][1] for i in train_ids]
        holdout_all = [holdout_rows[i][1] for i in holdout_ids]

        alone_stats: Dict[str, Dict[str, Any]] = {}
        for s in combo:
            others_ids: Set[int] = set()
            for o in combo:
                if o != s:
                    others_ids |= train_idx[o]
            solo_ids = train_idx[s] - others_ids
            alone_stats[s] = _stat([train_rows[i][1] for i in solo_ids])

        per_combo.append({
            "combo": combo, "train_all": train_all, "holdout_all": holdout_all,
            "alone_stats": alone_stats,
        })

    bonferroni = max(1, n_tests)
    results: List[Dict[str, Any]] = []
    for item in per_combo:
        combo = item["combo"]
        tr = _stat(item["train_all"])
        ho = _stat(item["holdout_all"])
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

        alone_means = [st["mean_net_r"] for st in item["alone_stats"].values() if st.get("n", 0)]
        synergy = round(tr["mean_net_r"] - max(alone_means), 5) if alone_means else None

        results.append({
            "combo": "+".join(combo), "combo_size": len(combo),
            "train": tr, "holdout": ho,
            "either_alone": item["alone_stats"],
            "synergy_vs_best_alone": synergy,
            "verdict": verdict,
        })

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["train"].get("t", 0) or 0)))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "combo_size": combo_size,
        "labelled_days": len(days), "train_days": cut_idx + 1,
        "holdout_days": len(days) - cut_idx - 1, "cutoff_day": cutoff_day,
        "candidate_strategies": candidates,
        "combos_tested": n_tests, "bonferroni_tests": bonferroni,
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "hurts": [r for r in results if r["verdict"] == "HURTS"][:15],
        "all_tested": results,
        "caveat": "signals within a snapshot/day are correlated (co-firing "
                  "strategies on the same trending day are especially "
                  "correlated by construction); day-level holdout guards "
                  "discovery but per-row t-stats here are optimistic versus "
                  "a fully clustered-SE test. synergy_vs_best_alone < 0 means "
                  "the combo is likely redundant (correlated proxies of the "
                  "same underlying signal), not new information. Larger "
                  "combo_size means far more tests are attempted against the "
                  "same fixed history -- the Bonferroni correction below is "
                  "harsher accordingly, by design.",
    }
    return report


def format_report(rep: Dict[str, Any]) -> str:
    if rep.get("error"):
        return rep["error"]
    lines = [
        f"=== STRATEGY-{rep['combo_size']}-COMBO EDGE MINER | {rep['labelled_days']} days "
        f"(train {rep['train_days']} / holdout {rep['holdout_days']}, "
        f"cutoff {rep['cutoff_day']}) | top-{len(rep['candidate_strategies'])} strategies "
        f"| combos tested x{rep['combos_tested']} Bonferroni x{rep['bonferroni_tests']} ===",
        "",
        f"CANDIDATES surviving train-significance + holdout: {len(rep['candidates'])}",
    ]
    for r in rep["candidates"]:
        lines.append(f"  ✅ {r['combo']}: train n={r['train']['n']} R={r['train']['mean_net_r']} "
                      f"t={r['train']['t']} p={r['train']['p']} | holdout n={r['holdout'].get('n')} "
                      f"R={r['holdout'].get('mean_net_r')} | synergy={r['synergy_vs_best_alone']}")
    lines.append("\nTop by |t| (regardless of verdict):")
    for r in sorted(rep["all_tested"], key=lambda r: -abs(r["train"].get("t", 0) or 0))[:10]:
        lines.append(f"  {r['verdict']:>18} {r['combo']}: train n={r['train']['n']} "
                      f"R={r['train']['mean_net_r']} t={r['train']['t']} p={r['train']['p']} | "
                      f"holdout n={r['holdout'].get('n')} R={r['holdout'].get('mean_net_r')} | "
                      f"synergy={r['synergy_vs_best_alone']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Strategy co-occurrence edge miner")
    ap.add_argument("--combo-size", type=int, default=2,
                     help="strategies per combination tested (2=pairs, 3=triples, ...)")
    ap.add_argument("--top-n", type=int, default=TOP_N_STRATEGIES,
                     help="candidate strategy universe size (by co-occurrence frequency)")
    args = ap.parse_args()

    rep = run(top_n=args.top_n, combo_size=args.combo_size)
    if rep.get("error"):
        print(rep["error"])
        return 1
    print(format_report(rep))
    out = _report_file(args.combo_size)
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
