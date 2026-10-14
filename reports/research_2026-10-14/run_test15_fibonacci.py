"""Test 15 in PREREGISTRATION.md: Fibonacci 61.8% retracement bounce vs placebo
levels (0.55, 0.70) on survivorship-free daily stock data. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test15_fibonacci.py
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
SWING, MIN_SWING, TOL, HOLD, COST_RT = 60, 0.15, 0.01, 10, 0.006


def universe_by_day(Rv, Cv, TVv, index):
    """Top-200 liquid universe from each month-end, applied to the next month's days."""
    members = np.full(len(index), None, dtype=object)
    ends = t.month_end_rows(index)
    for k, p in enumerate(ends):
        if p < 252:
            continue
        uni, _ = t.select(Rv, Cv, TVv, p)
        stop = ends[k + 1] if k + 1 < len(ends) else len(index) - 1
        for d in range(p + 1, stop + 1):
            members[d] = uni
    return members


def run(Rv, members, f):
    n_days = Rv.shape[0]
    next_free = np.zeros(Rv.shape[1], dtype=int)
    rows = []
    for d in range(SWING, n_days - HOLD - 2):
        uni = members[d]
        if uni is None:
            continue
        win = Rv[d - SWING + 1:d + 1, uni]
        ok = ~np.isnan(win).any(axis=0)
        path = np.cumprod(1 + np.nan_to_num(win), axis=0)
        hi, lo = path.max(axis=0), path.min(axis=0)
        i_hi, i_lo = path.argmax(axis=0), path.argmin(axis=0)
        level = hi - f * (hi - lo)
        today, yday = path[-1], path[-2]
        trig = (ok & (i_lo < i_hi) & (hi / lo - 1 >= MIN_SWING)
                & (np.abs(today / level - 1) <= TOL) & (today > yday))
        if not trig.any():
            continue
        fwd = Rv[d + 2:d + 2 + HOLD]                            # next-day-close entry, 10-day hold
        uni_ret = np.nanmean(np.prod(1 + fwd[:, uni], axis=0) - 1)
        for j in uni[trig]:
            if d < next_free[j]:
                continue
            r = t.holding_return(fwd[:, j])
            rows.append({"d": d, "stock": j, "excess_net": r - uni_ret - COST_RT})
            next_free[j] = d + HOLD + 1
    return pd.DataFrame(rows)


def stats(df, dates):
    if df.empty:
        return {"signals": 0}
    daily = df.groupby("d")["excess_net"].mean()
    tstat = float(daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))) if len(daily) > 1 else 0.0
    split = int(len(df) * 0.7)
    df = df.sort_values("d")
    return {"signals": int(len(df)), "mean_net_excess_pct": round(float(df["excess_net"].mean()) * 100, 3),
            "win_rate_vs_universe": round(float((df["excess_net"] > 0).mean()), 3),
            "date_clustered_t": round(tstat, 2), "signal_days": int(len(daily)),
            "holdout_start": str(dates[int(df["d"].iloc[split])]),
            "holdout_mean_net_excess_pct": round(float(df["excess_net"].iloc[split:].mean()) * 100, 3)}


def main():
    R, C, TV = t.load()
    Rv, Cv, TVv = R.to_numpy(), C.to_numpy(), TV.to_numpy()
    members = universe_by_day(Rv, Cv, TVv, R.index)
    dates = [str(x) for x in R.index]
    res = {f"f_{f}": stats(run(Rv, members, f), dates) for f in (0.618, 0.55, 0.70)}
    fib, p1, p2 = res["f_0.618"], res["f_0.55"], res["f_0.7"]
    checks = {"signals_ge_200": fib.get("signals", 0) >= 200,
              "mean_positive_t_gt_2": fib.get("mean_net_excess_pct", -1) > 0 and fib.get("date_clustered_t", 0) > 2,
              "holdout_positive": fib.get("holdout_mean_net_excess_pct", -1) > 0,
              "beats_both_placebos": fib.get("mean_net_excess_pct", -1e9) > max(p1.get("mean_net_excess_pct", -1e9),
                                                                              p2.get("mean_net_excess_pct", -1e9))}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test15.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
