"""
score_inverse_falsification.py — pre-registered falsification battery for
the score_inverse_3hr candidate (2026-07-17, from three external AI
critiques that converged on the same spuriousness risks). Run BEFORE the
forward ledger matures so the checks can't be cherry-picked after a
confirmation. Diagnostics on the DISCOVERY sample only; the forward ledger
(option_signal_research_ledger.py) remains the sole promotion authority.

Battery:
  1. BASELINE      — Spearman rho(signed_score, signed_3h_ret), should
                     reproduce the discovery finding (~-0.14).
  2. GAP PARTIAL   — partial Spearman controlling for the day's overnight
                     gap%%. If the inverse relation is just NIFTY digesting
                     overnight/global moves (scores high on gap days, drift
                     fading by afternoon), it vanishes here.
  3. GAP SPLIT     — rho on flat-gap days vs gappy days (median |gap|
                     split). Spurious-macro criterion: |rho| < 0.05 in the
                     flat cohort while the gappy cohort carries everything.
  4. HORIZON DECAY — rho at 60/120/180/240 min. A structural effect decays
                     smoothly; an artifact spikes at exactly one horizon.
  5. LEAVE-ONE-DAY-OUT — drop each day, recompute rho. Fragile if any
                     single day's removal halves |rho|.
  6. VIX SPLIT     — rho on low-VIX vs high-VIX days (median split from
                     vix_history.csv). Regime-conditional if one side goes
                     flat.

Verdict field summarizes which flags tripped; tripped flags do NOT auto-
quarantine the ledger candidate (human call, house rule) but are written
alongside it in score_inverse_falsification.json.
"""
from __future__ import annotations

import csv
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from option_underlying_decomposition import _load_candles, _parse_snapshot_time, _forward_return
from option_decomposition_followups import _load_scored_observations, _spearman

logger = logging.getLogger(__name__)

REPORT_FILE = Path("score_inverse_falsification.json")
UNDERLYINGS = ("NIFTY", "BANKNIFTY", "FINNIFTY")
HORIZONS_MIN = (60, 120, 180, 240)
CANDIDATE_HORIZON = 180
FLAT_RHO_THRESHOLD = 0.05    # |rho| below this in a control cohort = vanished


def _ranks(vals: List[float]) -> List[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def _partial_spearman(xs: List[float], ys: List[float], zs: List[float]) -> float:
    """Partial rank correlation of x,y controlling z (Pearson on ranks)."""
    rx, ry, rz = _ranks(xs), _ranks(ys), _ranks(zs)
    rxy, rxz, ryz = _pearson(rx, ry), _pearson(rx, rz), _pearson(ry, rz)
    denom = math.sqrt(max(1e-12, (1 - rxz ** 2) * (1 - ryz ** 2)))
    return (rxy - rxz * ryz) / denom


def _daily_gaps(underlying: str) -> Dict[str, float]:
    """day -> overnight gap%% from 1d candles (open vs prev close)."""
    import sqlite3
    conn = sqlite3.connect("candle_cache.db")
    rows = conn.execute(
        "SELECT timestamp, open, close FROM candles WHERE symbol=? AND interval='1d' "
        "ORDER BY timestamp", (underlying,)).fetchall()
    conn.close()
    gaps: Dict[str, float] = {}
    for i in range(1, len(rows)):
        day = str(rows[i][0])[:10]
        prev_close = float(rows[i - 1][2] or 0)
        today_open = float(rows[i][1] or 0)
        if prev_close > 0 and today_open > 0:
            gaps[day] = (today_open - prev_close) / prev_close * 100.0
    return gaps


def _daily_vix() -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        with open("vix_history.csv") as f:
            for row in csv.DictReader(f):
                try:
                    out[row["date"]] = float(row["vix"])
                except (KeyError, ValueError):
                    continue
    except OSError as exc:
        logger.debug("vix_history.csv: %s", exc)
    return out


def _build_samples() -> List[Dict[str, Any]]:
    obs = _load_scored_observations(UNDERLYINGS)
    candles = {u: _load_candles(u) for u in UNDERLYINGS}
    gaps = {u: _daily_gaps(u) for u in UNDERLYINGS}
    samples: List[Dict[str, Any]] = []
    for o in obs:
        try:
            entry_ts = _parse_snapshot_time(o["snapshot_time"])
        except Exception:
            continue
        day = o["snapshot_time"][:10]
        gap = gaps[o["underlying"]].get(day)
        if gap is None:
            continue
        sign = 1.0 if o["direction"] == "BULLISH" else -1.0
        rets = {}
        for h in HORIZONS_MIN:
            r = _forward_return(candles[o["underlying"]], entry_ts, h)
            if r is not None:
                rets[h] = sign * r
        if CANDIDATE_HORIZON not in rets:
            continue
        samples.append({"day": day, "signed_score": sign * o["score"],
                        "gap": gap, "rets": rets})
    return samples


def run() -> Dict[str, Any]:
    samples = _build_samples()
    days = sorted({s["day"] for s in samples})
    xs = [s["signed_score"] for s in samples]
    ys = [s["rets"][CANDIDATE_HORIZON] for s in samples]
    zs = [s["gap"] for s in samples]

    rho_base, n = _spearman(xs, ys)
    rho_partial = _partial_spearman(xs, ys, zs)

    # 3. flat-vs-gappy split by median |gap| across days
    day_gap = {s["day"]: abs(s["gap"]) for s in samples}
    median_gap = sorted(day_gap.values())[len(day_gap) // 2]
    flat = [s for s in samples if abs(s["gap"]) <= median_gap]
    gappy = [s for s in samples if abs(s["gap"]) > median_gap]
    rho_flat, n_flat = _spearman([s["signed_score"] for s in flat],
                                  [s["rets"][CANDIDATE_HORIZON] for s in flat])
    rho_gappy, n_gappy = _spearman([s["signed_score"] for s in gappy],
                                    [s["rets"][CANDIDATE_HORIZON] for s in gappy])

    # 4. horizon decay
    horizon_rhos = {}
    for h in HORIZONS_MIN:
        sub = [s for s in samples if h in s["rets"]]
        r, hn = _spearman([s["signed_score"] for s in sub], [s["rets"][h] for s in sub])
        horizon_rhos[f"{h}min"] = {"rho": round(r, 4), "n": hn}

    # 5. leave-one-day-out
    lodo = []
    for d in days:
        sub = [s for s in samples if s["day"] != d]
        r, _ = _spearman([s["signed_score"] for s in sub],
                          [s["rets"][CANDIDATE_HORIZON] for s in sub])
        lodo.append({"dropped_day": d, "rho": round(r, 4)})
    lodo_min_abs = min(abs(x["rho"]) for x in lodo)
    most_influential = min(lodo, key=lambda x: abs(x["rho"]))

    # 6. VIX split (day-level)
    vix = _daily_vix()
    vix_days = {d: vix[d] for d in days if d in vix}
    rho_lowvix = rho_highvix = None
    n_lowvix = n_highvix = 0
    if len(vix_days) >= 8:
        median_vix = sorted(vix_days.values())[len(vix_days) // 2]
        low = [s for s in samples if vix_days.get(s["day"], 1e9) <= median_vix]
        high = [s for s in samples if vix_days.get(s["day"], -1e9) > median_vix]
        rho_lowvix, n_lowvix = _spearman([s["signed_score"] for s in low],
                                          [s["rets"][CANDIDATE_HORIZON] for s in low])
        rho_highvix, n_highvix = _spearman([s["signed_score"] for s in high],
                                            [s["rets"][CANDIDATE_HORIZON] for s in high])

    flags = []
    if abs(rho_partial) < FLAT_RHO_THRESHOLD:
        flags.append("VANISHES_CONTROLLING_OVERNIGHT_GAP")
    if abs(rho_flat) < FLAT_RHO_THRESHOLD and abs(rho_gappy) >= abs(rho_base):
        flags.append("CONCENTRATED_IN_GAPPY_DAYS")
    if lodo_min_abs < abs(rho_base) / 2:
        flags.append(f"SINGLE_DAY_FRAGILE({most_influential['dropped_day']})")
    h_seq = [horizon_rhos[f"{h}min"]["rho"] for h in HORIZONS_MIN]
    if all(abs(r) < abs(rho_base) * 0.4 for i, r in enumerate(h_seq) if HORIZONS_MIN[i] != CANDIDATE_HORIZON):
        flags.append("SPIKES_AT_SINGLE_HORIZON")
    if rho_lowvix is not None and (
            (abs(rho_lowvix) < FLAT_RHO_THRESHOLD) != (abs(rho_highvix) < FLAT_RHO_THRESHOLD)):
        flags.append("VIX_REGIME_CONDITIONAL")

    report = {
        "candidate": "score_inverse_3hr", "n": n, "days": len(days),
        "baseline_rho_180min": round(rho_base, 4),
        "partial_rho_controlling_gap": round(rho_partial, 4),
        "gap_split": {"median_abs_gap_pct": round(median_gap, 3),
                       "flat_days_rho": round(rho_flat, 4), "flat_n": n_flat,
                       "gappy_days_rho": round(rho_gappy, 4), "gappy_n": n_gappy},
        "horizon_decay": horizon_rhos,
        "leave_one_day_out": {"min_abs_rho": round(lodo_min_abs, 4),
                                "most_influential_day": most_influential},
        "vix_split": ({"low_vix_rho": round(rho_lowvix, 4), "low_n": n_lowvix,
                        "high_vix_rho": round(rho_highvix, 4), "high_n": n_highvix}
                       if rho_lowvix is not None else "insufficient_vix_coverage"),
        "flags": flags,
        "verdict": "FLAGS_TRIPPED" if flags else "SURVIVES_BATTERY",
        "note": "Diagnostics on the discovery sample; forward ledger remains the "
                "promotion authority. Tripped flags are a human-review signal, "
                "not an auto-quarantine.",
    }
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("report write failed: %s", exc)
    return report


def main() -> int:
    rep = run()
    print(f"=== SCORE_INVERSE_3HR FALSIFICATION BATTERY | n={rep['n']} | {rep['days']} days ===\n")
    print(f"  baseline rho@180min:            {rep['baseline_rho_180min']}")
    print(f"  partial rho (control gap):      {rep['partial_rho_controlling_gap']}")
    gs = rep["gap_split"]
    print(f"  flat-gap days rho:              {gs['flat_days_rho']} (n={gs['flat_n']})")
    print(f"  gappy days rho:                 {gs['gappy_days_rho']} (n={gs['gappy_n']})")
    print(f"  horizon decay:                  " + "  ".join(
        f"{h}:{v['rho']}" for h, v in rep["horizon_decay"].items()))
    print(f"  LODO min |rho|:                 {rep['leave_one_day_out']['min_abs_rho']} "
          f"(worst drop: {rep['leave_one_day_out']['most_influential_day']['dropped_day']})")
    print(f"  vix split:                      {rep['vix_split']}")
    print(f"\n  flags: {rep['flags'] or 'NONE'}")
    print(f"  verdict: {rep['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
