"""Test 18 in PREREGISTRATION.md: GGR (2006) distance-method pairs trading on
survivorship-free daily data. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test18_pairs.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_test7_momentum as t  # noqa: E402

t.ETF = re.compile(t.ETF.pattern + "|SILV|MON100|MAFANG|HNGSNG|NASDAQ|SP500|N100")
FORM, TRADE, N_PAIRS, Z = 252, 126, 20, 2.0


def cycle(Rv, Cv, TVv, f0, rt_cost):
    end_f = f0 + FORM - 1
    uni, _ = t.select(Rv, Cv, TVv, end_f)
    P = np.cumprod(1 + Rv[f0:end_f + 1, uni], axis=0)
    sq = (P * P).sum(axis=0)
    ssd = sq[:, None] + sq[None, :] - 2 * P.T @ P
    iu = np.triu_indices(len(uni), 1)
    best = np.argsort(ssd[iu])[:N_PAIRS]
    pairs = [(iu[0][b], iu[1][b]) for b in best]
    Rt = Rv[end_f + 1:end_f + 1 + TRADE]
    total, trades = 0.0, 0
    for a, b in pairs:
        sigma = np.std(P[:, a] - P[:, b], ddof=1)
        ra, rb = Rt[:, uni[a]], Rt[:, uni[b]]
        valid = ~(np.isnan(ra) | np.isnan(rb))
        last = int(np.argmin(valid)) if not valid.all() else len(valid)
        qa, qb = np.cumprod(1 + np.nan_to_num(ra[:last])), np.cumprod(1 + np.nan_to_num(rb[:last]))
        spread = qa - qb
        open_at, side = None, 0
        for d in range(len(spread)):
            if open_at is None and abs(spread[d]) > Z * sigma:
                open_at, side = d, (1 if spread[d] < 0 else -1)      # +1: long a / short b
            elif open_at is not None and (np.sign(spread[d]) != np.sign(spread[open_at]) or d == len(spread) - 1):
                la, lb = qa[d] / qa[open_at], qb[d] / qb[open_at]
                total += (la - lb if side == 1 else lb - la) - rt_cost
                trades += 1
                open_at = None
    return total / N_PAIRS, trades


def run(rt_cost):
    R, C, TV = t.load()
    Rv, Cv, TVv = R.to_numpy(), C.to_numpy(), TV.to_numpy()
    out = []
    f0 = 0
    while f0 + FORM + TRADE <= len(R):
        if f0 + FORM - 1 >= 252:
            r, n = cycle(Rv, Cv, TVv, f0, rt_cost)
            out.append({"trade_start": str(R.index[f0 + FORM]), "cycle_return": r, "trades": n})
        f0 += TRADE
    return out


def summary(rows):
    r = np.array([x["cycle_return"] for x in rows])
    split = int(len(r) * 0.7)
    tstat = float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 1 else 0.0
    return {"cycles": int(len(r)), "mean_cycle_return_pct": round(float(r.mean()) * 100, 3),
            "t": round(tstat, 2), "holdout_mean_pct": round(float(r[split:].mean()) * 100, 3),
            "trades_total": int(sum(x["trades"] for x in rows)),
            "cycles_detail": [{"start": x["trade_start"], "ret_pct": round(x["cycle_return"] * 100, 2)} for x in rows]}


def main():
    base, dbl = summary(run(0.012)), summary(run(0.024))
    checks = {"cycles_ge_10": base["cycles"] >= 10,
              "mean_positive_t_gt_2": base["mean_cycle_return_pct"] > 0 and base["t"] > 2,
              "holdout_positive": base["holdout_mean_pct"] > 0,
              "costs_doubled_positive": dbl["mean_cycle_return_pct"] > 0}
    res = {"base": base, "costs_doubled": {k: v for k, v in dbl.items() if k != "cycles_detail"},
           "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL"}
    (HERE / "results_test18.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
