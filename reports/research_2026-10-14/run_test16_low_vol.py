"""Test 16 in PREREGISTRATION.md: low-volatility factor on the survivorship-free
adjusted returns -- Test 7's framework with a different signal. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test16_low_vol.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_test14_52week_high as base  # noqa: E402  (shared Test 7 harness + broader ETF filter)

t = base.t


def make_select(window: int):
    def select(Rv, Cv, TVv, p, lookback=252):
        win = Rv[p - 251:p + 1]
        ok = ~np.isnan(win).any(axis=0) & (Cv[p] >= 20)
        liq = np.nanmedian(TVv[p - t.LIQ_WINDOW + 1:p + 1], axis=0)
        cand = np.where(ok)[0]
        uni = cand[np.argsort(-liq[cand])[:t.UNIVERSE]]
        vol = np.std(Rv[p - window + 1:p + 1, uni], axis=0, ddof=1)
        return uni, uni[np.argsort(vol)[:t.TOP_N]]
    return select


base.make_select = lambda window, side="high": make_select(window)


def main():
    import pandas as pd
    R, C, TV = t.load()
    df = base.run(R, C, TV, 252)
    _, spot = base.cbr._load()
    nifty = base.nifty_daily(spot)
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
    d2 = base.run(R, C, TV, 252, cost=2 * t.COST)
    excess_2x = t.ann(d2["momentum"]) - t.ann(d2["equal_weight"])
    stability = {f"{w}d_vol": {"strategy_pct": round(t.ann(d["momentum"]) * 100, 2),
                               "equal_weight_pct": round(t.ann(d["equal_weight"]) * 100, 2)}
                 for w, d in ((w, base.run(R, C, TV, w)) for w in (126, 63))}
    res = {"months": len(df), "holdout_start": str(hold["start"].iloc[0]),
           "avg_monthly_turnover": round(df["turnover"].mean(), 3),
           "full": {k: {"ann_return_pct": round(t.ann(df[k]) * 100, 2), "sharpe": round(t.sharpe(df[k]), 3),
                        "max_dd_pct": round(t.max_dd(df[k]) * 100, 1)} for k in ("momentum", "equal_weight", "nifty")},
           "holdout": {k: round(t.ann(hold[k]) * 100, 2) for k in ("momentum", "equal_weight", "nifty")},
           "bootstrap_lower90_excess_vs_ew_pct": round(lower90 * 100, 2),
           "excess_vs_ew_costs_doubled_pct": round(excess_2x * 100, 2), "stability": stability,
           "note": "'momentum' column = the low-volatility portfolio (reused Test 7 code)"}
    f = res["full"]
    checks = {"holdout_beats_ew_and_nifty_by_2pp": res["holdout"]["momentum"] >= res["holdout"]["equal_weight"] + 2
              and res["holdout"]["momentum"] >= res["holdout"]["nifty"] + 2,
              "sharpe_above_nifty": f["momentum"]["sharpe"] > f["nifty"]["sharpe"],
              "bootstrap_lower90_positive": lower90 > 0,
              "costs_doubled_excess_nonnegative": excess_2x >= 0,
              "max_dd_within_1_5x_nifty": f["momentum"]["max_dd_pct"] >= 1.5 * f["nifty"]["max_dd_pct"],
              "stable_126d_and_63d": all(v["strategy_pct"] > v["equal_weight_pct"] for v in stability.values())}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test16.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
