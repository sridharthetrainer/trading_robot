"""
option_decomposition_followups.py — two cheap follow-ups on the SAME
7,827-observation decomposition dataset, testing genuinely different
hypotheses than the original binary-direction test (2026-07-15,
following an external audit's suggestions):

1. CONTINUOUS RANK CORRELATION: the bot's thresholded BULLISH/BEARISH
   call destroys monotone information by construction if the underlying
   continuous score variable actually has a graded relationship with
   forward returns. Spearman rank correlation between score and signed
   forward return, day-holdout split, tested at the same horizons.

2. MAGNITUDE/VOLATILITY TEST: does signal score predict the SIZE of the
   subsequent move (unsigned |return|), regardless of whether direction
   was called correctly? If so, the correct expression is a volatility
   filter or premium-selling gate, not a directional buy — a genuinely
   different, untested cell (option structures were tested standalone,
   never conditioned on this score).

Both reuse the exact same observation/candle-loading and day-split
machinery as option_underlying_decomposition.py for consistency.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List, Tuple

from option_underlying_decomposition import (
    SNAPSHOT_DB, HORIZONS_MIN, TRAIN_FRAC, MIN_TRAIN_N, MIN_HOLDOUT_N, ALPHA,
    _load_candles, _parse_snapshot_time, _forward_return, _stat,
)


def _load_scored_observations(underlyings: Tuple[str, ...]) -> List[Dict[str, Any]]:
    with sqlite3.connect(SNAPSHOT_DB) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT underlying, snapshot_time, direction, score
                  FROM option_strike_signals
                 WHERE underlying IN ({','.join('?' for _ in underlyings)})
                   AND score > 0
                 ORDER BY underlying, snapshot_time""",
            underlyings,
        ).fetchall()
    return [{"underlying": r[0], "snapshot_time": r[1], "direction": r[2], "score": float(r[3])}
            for r in rows]


def _spearman(xs: List[float], ys: List[float]) -> Tuple[float, int]:
    """Spearman rank correlation + n, no scipy dependency."""
    n = len(xs)
    if n < 3:
        return 0.0, n

    def _ranks(vals: List[float]) -> List[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    sx = math.sqrt(sum((r - mean_rx) ** 2 for r in rx))
    sy = math.sqrt(sum((r - mean_ry) ** 2 for r in ry))
    if sx == 0 or sy == 0:
        return 0.0, n
    return cov / (sx * sy), n


def _corr_significance(rho: float, n: int) -> float:
    if n < 4 or abs(rho) >= 1.0:
        return 0.0 if abs(rho) >= 1.0 else 1.0
    t = rho * math.sqrt((n - 2) / (1 - rho ** 2))
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))


def run_rank_correlation(underlyings=("NIFTY", "BANKNIFTY", "FINNIFTY")) -> Dict[str, Any]:
    obs = _load_scored_observations(underlyings)
    candle_cache = {u: _load_candles(u) for u in underlyings}
    days = sorted({o["snapshot_time"][:10] for o in obs})
    if len(days) < 6:
        return {"error": f"only {len(days)} days"}
    cutoff = days[max(1, int(len(days) * TRAIN_FRAC) - 1)]

    per_horizon: Dict[int, Tuple[List[float], List[float], List[float], List[float]]] = {}
    for o in obs:
        try:
            entry_ts = _parse_snapshot_time(o["snapshot_time"])
        except Exception:
            continue
        sign = 1.0 if o["direction"] == "BULLISH" else -1.0
        signed_score = sign * o["score"]  # continuous, signed by direction
        day = o["snapshot_time"][:10]
        train_slot = day <= cutoff
        for h in HORIZONS_MIN:
            ret = _forward_return(candle_cache[o["underlying"]], entry_ts, h)
            if ret is None:
                continue
            signed_ret = sign * ret
            tr_s, ho_s, tr_r, ho_r = per_horizon.setdefault(h, ([], [], [], []))
            if train_slot:
                tr_s.append(signed_score)
                tr_r.append(signed_ret)
            else:
                ho_s.append(signed_score)
                ho_r.append(signed_ret)

    results = []
    n_tests = sum(1 for tr_s, _, _, _ in per_horizon.values() if len(tr_s) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)
    for h, (tr_s, ho_s, tr_r, ho_r) in per_horizon.items():
        if len(tr_r) < MIN_TRAIN_N:
            continue
        rho_train, n_train = _spearman(tr_s, tr_r)
        p_train = _corr_significance(rho_train, n_train) * bonferroni
        rho_holdout, n_holdout = _spearman(ho_s, ho_r) if len(ho_r) >= MIN_HOLDOUT_N else (0.0, 0)
        sig = p_train < ALPHA
        held = n_holdout >= MIN_HOLDOUT_N and rho_holdout * rho_train > 0
        verdict = "CANDIDATE" if (sig and rho_train > 0 and held) else (
            "TRAIN_ONLY_OVERFIT" if sig and rho_train > 0 else
            "NEGATIVE_CORR" if sig and rho_train < 0 else "NOISE")
        results.append({"horizon_min": h, "train_rho": round(rho_train, 4), "train_n": n_train,
                        "train_p_corrected": round(p_train, 5),
                        "holdout_rho": round(rho_holdout, 4), "holdout_n": n_holdout,
                        "verdict": verdict})
    return {"bonferroni_tests": bonferroni, "results": results}


def run_magnitude_test(underlyings=("NIFTY", "BANKNIFTY", "FINNIFTY")) -> Dict[str, Any]:
    obs = _load_scored_observations(underlyings)
    candle_cache = {u: _load_candles(u) for u in underlyings}
    days = sorted({o["snapshot_time"][:10] for o in obs})
    if len(days) < 6:
        return {"error": f"only {len(days)} days"}
    cutoff = days[max(1, int(len(days) * TRAIN_FRAC) - 1)]

    # Split by score tercile to compare high-score vs low-score realized |move|
    scores_sorted = sorted(o["score"] for o in obs)
    lo_cut = scores_sorted[len(scores_sorted) // 3]
    hi_cut = scores_sorted[2 * len(scores_sorted) // 3]

    per_horizon: Dict[int, Dict[str, Tuple[List[float], List[float]]]] = {}
    for o in obs:
        try:
            entry_ts = _parse_snapshot_time(o["snapshot_time"])
        except Exception:
            continue
        tier = "low" if o["score"] <= lo_cut else "high" if o["score"] >= hi_cut else "mid"
        day = o["snapshot_time"][:10]
        train_slot = day <= cutoff
        for h in HORIZONS_MIN:
            ret = _forward_return(candle_cache[o["underlying"]], entry_ts, h)
            if ret is None:
                continue
            unsigned = abs(ret)
            bucket = per_horizon.setdefault(h, {}).setdefault(tier, ([], []))
            (bucket[0] if train_slot else bucket[1]).append(unsigned)

    results = []
    n_tests = sum(1 for h, tiers in per_horizon.items() for tr, _ in tiers.values() if len(tr) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)
    for h, tiers in per_horizon.items():
        lo_tr, lo_ho = tiers.get("low", ([], []))
        hi_tr, hi_ho = tiers.get("high", ([], []))
        if len(lo_tr) < MIN_TRAIN_N or len(hi_tr) < MIN_TRAIN_N:
            continue
        lo_stat, hi_stat = _stat(lo_tr), _stat(hi_tr)
        diff_train = hi_stat["mean_bps"] - lo_stat["mean_bps"]
        lo_ho_stat, hi_ho_stat = _stat(lo_ho), _stat(hi_ho)
        diff_holdout = (hi_ho_stat.get("mean_bps", 0) - lo_ho_stat.get("mean_bps", 0)
                       if hi_ho_stat.get("n", 0) >= MIN_HOLDOUT_N and lo_ho_stat.get("n", 0) >= MIN_HOLDOUT_N
                       else None)
        results.append({
            "horizon_min": h,
            "low_score_train_avg_abs_move_bps": lo_stat["mean_bps"], "low_n": lo_stat["n"],
            "high_score_train_avg_abs_move_bps": hi_stat["mean_bps"], "high_n": hi_stat["n"],
            "train_diff_bps": round(diff_train, 2),
            "holdout_diff_bps": round(diff_holdout, 2) if diff_holdout is not None else None,
        })
    return {"bonferroni_tests": bonferroni, "score_terciles": {"low_cutoff": lo_cut, "high_cutoff": hi_cut},
            "results": results}


def main() -> int:
    print("=== FOLLOW-UP 1: continuous score vs forward return, rank correlation ===\n")
    rc = run_rank_correlation()
    if rc.get("error"):
        print(rc["error"])
    else:
        for r in sorted(rc["results"], key=lambda r: -abs(r["train_rho"])):
            print(f"  h={r['horizon_min']:>3}min  verdict={r['verdict']:<20} "
                  f"train_rho={r['train_rho']:>7} (n={r['train_n']}, p_corr={r['train_p_corrected']}) | "
                  f"holdout_rho={r['holdout_rho']:>7} (n={r['holdout_n']})")

    print("\n=== FOLLOW-UP 2: does score predict move MAGNITUDE (unsigned), not direction? ===\n")
    mt = run_magnitude_test()
    if mt.get("error"):
        print(mt["error"])
    else:
        print(f"score terciles: low<={mt['score_terciles']['low_cutoff']:.1f}, "
              f"high>={mt['score_terciles']['high_cutoff']:.1f}\n")
        for r in sorted(mt["results"], key=lambda r: -abs(r["train_diff_bps"])):
            print(f"  h={r['horizon_min']:>3}min  low_score_avg|move|={r['low_score_train_avg_abs_move_bps']}bps "
                  f"(n={r['low_n']}) high_score_avg|move|={r['high_score_train_avg_abs_move_bps']}bps "
                  f"(n={r['high_n']}) train_diff={r['train_diff_bps']}bps holdout_diff={r['holdout_diff_bps']}bps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
