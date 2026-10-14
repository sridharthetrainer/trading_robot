"""Test 17 in PREREGISTRATION.md: volatility-scaled momentum (Barroso & Santa-Clara 2015)
vs unscaled Test 7 momentum. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test17_volscaled_momentum.py
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

import run_test7_momentum as t  # noqa: E402

t.ETF = re.compile(t.ETF.pattern + "|SILV|MON100|MAFANG|HNGSNG|NASDAQ|SP500|N100")
TARGET, LOOKBACK, CASH = 0.12, 126, 0.065


def daily_series(R, df) -> pd.Series:
    """The strategy's daily returns (mean of each month's holdings), by row of R.
    Shared with momentum_paper_tracker.py."""
    cols = {c: i for i, c in enumerate(R.columns)}
    idx = {str(d): i for i, d in enumerate(R.index)}
    Rv = R.to_numpy()
    daily = pd.Series(np.nan, index=range(len(R)))
    for _, row in df.iterrows():                       # strategy's daily returns, month by month
        a, b = idx[str(row["start"])] + 1, idx[str(row["end"])] + 1
        daily.iloc[a:b] = np.nanmean(Rv[a:b, [cols[s] for s in row["names"]]], axis=1)
    return daily


def weight_before(daily: pd.Series, a: int) -> float:
    """Exposure for a holding month whose first return row is a (uses rows < a only)."""
    hist = daily.iloc[:a].dropna().iloc[-LOOKBACK:]
    return min(1.0, TARGET / (hist.std(ddof=1) * np.sqrt(252))) if len(hist) >= LOOKBACK else 1.0


def scaled(R, df, cost):
    idx = {str(d): i for i, d in enumerate(R.index)}
    daily = daily_series(R, df)
    out, w_prev = [], None
    for _, row in df.iterrows():
        a = idx[str(row["start"])] + 1
        w = weight_before(daily, a)
        trade = 0.0 if w_prev is None else abs(w - w_prev) * cost
        out.append(w * row["momentum"] + (1 - w) * CASH / 12 - trade)
        w_prev = w
    return np.array(out)


def main():
    R, C, TV = t.load()
    res, runs = {}, {}
    for label, cost in (("base", t.COST), ("costs_doubled", 2 * t.COST)):
        df = t.simulate(R, C, TV, 252, cost)
        runs[label] = (df["momentum"].to_numpy(), scaled(R, df, cost), df["equal_weight"].to_numpy())
    un, sc, ew = runs["base"]
    split = int(len(un) * 0.7)
    res = {"months": len(un),
           "unscaled": {"ann_pct": round(t.ann(un) * 100, 2), "sharpe": round(t.sharpe(un), 3),
                        "max_dd_pct": round(t.max_dd(un) * 100, 1), "holdout_sharpe": round(t.sharpe(un[split:]), 3)},
           "scaled": {"ann_pct": round(t.ann(sc) * 100, 2), "sharpe": round(t.sharpe(sc), 3),
                      "max_dd_pct": round(t.max_dd(sc) * 100, 1), "holdout_sharpe": round(t.sharpe(sc[split:]), 3)},
           "equal_weight_ann_pct": round(t.ann(ew) * 100, 2),
           "costs_doubled_sharpe": {"unscaled": round(t.sharpe(runs["costs_doubled"][0]), 3),
                                    "scaled": round(t.sharpe(runs["costs_doubled"][1]), 3)}}
    u, s = res["unscaled"], res["scaled"]
    checks = {"sharpe_higher": s["sharpe"] > u["sharpe"], "max_dd_smaller": s["max_dd_pct"] > u["max_dd_pct"],
              "holdout_sharpe_ge": s["holdout_sharpe"] >= u["holdout_sharpe"],
              "return_ge_equal_weight": s["ann_pct"] >= res["equal_weight_ann_pct"],
              "sharpe_higher_costs_doubled": res["costs_doubled_sharpe"]["scaled"] > res["costs_doubled_sharpe"]["unscaled"]}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test17.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
