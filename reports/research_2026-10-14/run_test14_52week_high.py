"""Test 14 in PREREGISTRATION.md: 52-week-high nearness (George & Hwang 2004) on the
survivorship-free adjusted returns -- Test 7's framework with a different signal.
Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test14_52week_high.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
import run_test7_momentum as t  # noqa: E402
from run_tests import nifty_daily  # noqa: E402

t.ETF = re.compile(t.ETF.pattern + "|SILV|MON100|MAFANG|HNGSNG|NASDAQ|SP500|N100")


def make_select(window: int, side: str = "high"):
    def select(Rv, Cv, TVv, p, lookback=252):
        win = Rv[p - 251:p + 1]
        ok = ~np.isnan(win).any(axis=0) & (Cv[p] >= 20)
        liq = np.nanmedian(TVv[p - t.LIQ_WINDOW + 1:p + 1], axis=0)
        cand = np.where(ok)[0]
        uni = cand[np.argsort(-liq[cand])[:t.UNIVERSE]]
        path = np.cumprod(1 + Rv[p - window + 1:p + 1, uni], axis=0)
        near_high = path[-1] / path.max(axis=0)
        near_low = path[-1] / path.min(axis=0)
        order = np.argsort(-near_high) if side == "high" else np.argsort(near_low)
        return uni, uni[order[:t.TOP_N]]
    return select


def run(R, C, TV, window, side="high", cost=t.COST):
    original = t.select
    t.select = make_select(window, side)
    try:
        return t.simulate(R, C, TV, 252, cost)
    finally:
        t.select = original


def main():
    R, C, TV = t.load()
    df = run(R, C, TV, 252)
    _, spot = cbr._load()
    nifty = nifty_daily(spot)
    df["nifty"] = [float(nifty.asof(pd.Timestamp(e)) / nifty.asof(pd.Timestamp(s)) - 1)
                   for s, e in zip(df["start"], df["end"])]
    split = int(len(df) * (1 - t.HOLDOUT))
    hold = df.iloc[split:]
    excess = (df["momentum"] - df["equal_weight"]).to_numpy()
    rng = np.random.default_rng(20261014)
    n, block = len(excess), 12
    boots = [excess[np.concatenate([(s + np.arange(block)) % n for s in rng.integers(0, n, int(np.ceil(n / block)))])[:n]].mean() * 12
             for _ in range(5000)]
    lower90 = float(np.quantile(boots, 0.05))
    d2 = run(R, C, TV, 252, cost=2 * t.COST)
    excess_2x = t.ann(d2["momentum"]) - t.ann(d2["equal_weight"])
    stability = {}
    for w in (126, 189):
        d = run(R, C, TV, w)
        stability[f"{w}d_high"] = {"strategy_pct": round(t.ann(d["momentum"]) * 100, 2),
                                   "equal_weight_pct": round(t.ann(d["equal_weight"]) * 100, 2)}
    low = run(R, C, TV, 252, side="low")
    mom = t.simulate(R, C, TV, 252, t.COST)
    corr = float(np.corrcoef(df["momentum"], mom["momentum"])[0, 1])
    res = {"months": len(df), "holdout_start": str(hold["start"].iloc[0]),
           "avg_monthly_turnover": round(df["turnover"].mean(), 3),
           "full": {k: {"ann_return_pct": round(t.ann(df[k]) * 100, 2), "sharpe": round(t.sharpe(df[k]), 3),
                        "max_dd_pct": round(t.max_dd(df[k]) * 100, 1)} for k in ("momentum", "equal_weight", "nifty")},
           "holdout": {k: round(t.ann(hold[k]) * 100, 2) for k in ("momentum", "equal_weight", "nifty")},
           "bootstrap_lower90_excess_vs_ew_pct": round(lower90 * 100, 2),
           "excess_vs_ew_costs_doubled_pct": round(excess_2x * 100, 2), "stability": stability,
           "DIAGNOSTIC_near_52w_low_pct": round(t.ann(low["momentum"]) * 100, 2),
           "DIAGNOSTIC_corr_with_test7_momentum": round(corr, 3),
           "note": "'momentum' column = the 52-week-high portfolio (reused Test 7 code)"}
    f = res["full"]
    checks = {"holdout_beats_ew_and_nifty_by_2pp": res["holdout"]["momentum"] >= res["holdout"]["equal_weight"] + 2
              and res["holdout"]["momentum"] >= res["holdout"]["nifty"] + 2,
              "sharpe_above_nifty": f["momentum"]["sharpe"] > f["nifty"]["sharpe"],
              "bootstrap_lower90_positive": lower90 > 0,
              "costs_doubled_excess_nonnegative": excess_2x >= 0,
              "max_dd_within_1_5x_nifty": f["momentum"]["max_dd_pct"] >= 1.5 * f["nifty"]["max_dd_pct"],
              "stable_126d_and_189d": all(v["strategy_pct"] > v["equal_weight_pct"] for v in stability.values())}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test14.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
