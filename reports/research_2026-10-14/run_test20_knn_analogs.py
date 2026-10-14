"""Test 20 in PREREGISTRATION.md: nearest-neighbour pattern matching on NIFTY daily.
Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test20_knn_analogs.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
from run_tests import nifty_daily  # noqa: E402

W, H, K, MIN_HIST, CASH = 20, 5, 20, 500, 0.065


def sharpe(x):
    x = np.asarray(x)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(252 / H)) if x.std(ddof=1) > 0 else 0.0


def main():
    _, spot = cbr._load()
    px = nifty_daily(spot)
    r = np.diff(np.log(px.to_numpy()))
    n = len(r)
    pats = np.full((n, W), np.nan)
    for t in range(W - 1, n):
        w = r[t - W + 1:t + 1]
        sd = w.std(ddof=1)
        pats[t] = (w - w.mean()) / sd if sd > 0 else np.nan
    fwd = np.full(n, np.nan)
    for t in range(n - H):
        fwd[t] = r[t + 1:t + 1 + H].sum()
    blocks = []
    for t in range(W - 1 + MIN_HIST, n - H, H):
        lib = np.arange(W - 1, t - H)                    # outcomes fully known by t
        lib = lib[~np.isnan(pats[lib]).any(axis=1)]
        d = np.sqrt(((pats[lib] - pats[t]) ** 2).sum(axis=1))
        nn = lib[np.argsort(d)[:K]]
        blocks.append((t, float(fwd[nn].mean()), float(fwd[t])))
    f = np.array([b[1] for b in blocks]); a = np.array([b[2] for b in blocks])
    bh = np.exp(a) - 1
    cash = (1 + CASH) ** (H / 252) - 1

    def strat(cost, short=False):
        pos = np.where(f > 0, 1.0, -1.0 if short else 0.0)
        switches = np.abs(np.diff(np.r_[0.0, pos]))
        return pos * bh + (pos == 0) * cash - switches * cost

    s1, s2, ls = strat(0.001), strat(0.002), strat(0.001, short=True)
    hit = float(((f > 0) == (a > 0)).mean())
    base = float(max((a > 0).mean(), 1 - (a > 0).mean()))
    z = (hit - base) / math.sqrt(base * (1 - base) / len(a))
    split = int(len(a) * 0.7)
    res = {"blocks": len(a), "start": str(px.index[blocks[0][0]].date()), "end": str(px.index[blocks[-1][0]].date()),
           "hit_rate": round(hit, 4), "always_up_rate": round(base, 4), "hit_z": round(z, 2),
           "pct_time_long": round(float((f > 0).mean()), 3),
           "sharpe": {"strategy": round(sharpe(s1), 3), "buy_hold": round(sharpe(bh), 3),
                      "strategy_costs_doubled": round(sharpe(s2), 3),
                      "holdout_strategy": round(sharpe(s1[split:]), 3), "holdout_buy_hold": round(sharpe(bh[split:]), 3),
                      "DIAGNOSTIC_long_short": round(sharpe(ls), 3)},
           "ann_return_pct": {"strategy": round(((1 + s1).prod() ** (252 / H / len(s1)) - 1) * 100, 2),
                              "buy_hold": round(((1 + bh).prod() ** (252 / H / len(bh)) - 1) * 100, 2)}}
    sh = res["sharpe"]
    checks = {"hit_rate_z_gt_2": z > 2, "sharpe_above_buy_hold": sh["strategy"] > sh["buy_hold"],
              "holdout_sharpe_above_buy_hold": sh["holdout_strategy"] > sh["holdout_buy_hold"],
              "costs_doubled_above_buy_hold": sh["strategy_costs_doubled"] > sh["buy_hold"]}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test20.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
