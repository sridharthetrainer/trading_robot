"""Test 6 in PREREGISTRATION.md: NIFTY 10-month moving-average timing.

Read-only research; writes results_test6.json next to this file.
    .venv/bin/python reports/research_2026-10-14/run_test6_ma10.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
from run_tests import nifty_daily  # noqa: E402

DIV_YIELD, CASH_YIELD, COST = 0.013, 0.065, 0.0015
HOLDOUT = 0.30


def ann_return(r: np.ndarray) -> float:
    return float(np.prod(1 + r) ** (12 / len(r)) - 1)


def max_dd(r: np.ndarray) -> float:
    eq = np.cumprod(1 + r)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def returns(cost: float):
    _, spot = cbr._load()
    m = nifty_daily(spot).resample("ME").last()
    m = m[m.index < "2026-10-01"]           # drop the incomplete October
    sma = m.rolling(10).mean()
    hold = (m > sma).shift(1)                # decided at prior month-end
    eq = m.pct_change() + DIV_YIELD / 12
    cash = CASH_YIELD / 12
    df = pd.DataFrame({"eq": eq, "hold": hold}).dropna()
    df["hold"] = df["hold"].astype(float)
    switches = df["hold"].diff().abs().fillna(0)
    df["strat"] = df["hold"] * df["eq"] + (1 - df["hold"]) * cash - switches * cost
    return df, int(switches.sum())


def main() -> None:
    df, n_switches = returns(COST)
    w = df["strat"].std() / df["eq"].std()
    df["mix"] = w * df["eq"] + (1 - w) * CASH_YIELD / 12
    split = int(len(df) * (1 - HOLDOUT))
    hold = df.iloc[split:]

    excess = (df["strat"] - df["mix"]).to_numpy()
    rng = np.random.default_rng(20261014)
    block, n = 12, len(excess)
    boots = []
    for _ in range(5000):
        starts = rng.integers(0, n, size=int(np.ceil(n / block)))
        idx = np.concatenate([(s + np.arange(block)) % n for s in starts])[:n]
        boots.append(excess[idx].mean() * 12)
    lower90 = float(np.quantile(boots, 0.05))

    df2, _ = returns(2 * COST)
    df2["mix"] = w * df2["eq"] + (1 - w) * CASH_YIELD / 12
    excess_2x = ann_return(df2["strat"].to_numpy()) - ann_return(df2["mix"].to_numpy())

    s, mx, bh = (df[c].to_numpy() for c in ("strat", "mix", "eq"))
    res = {
        "months": len(df), "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
        "holdout_start": str(hold.index[0].date()), "switches": n_switches,
        "pct_months_in_nifty": round(df["hold"].mean() * 100, 1), "static_mix_weight": round(w, 3),
        "full": {k: {"ann_return_pct": round(ann_return(v) * 100, 2), "max_dd_pct": round(max_dd(v) * 100, 1)}
                 for k, v in (("strategy", s), ("static_mix", mx), ("buy_hold", bh))},
        "holdout": {k: round(ann_return(hold[c].to_numpy()) * 100, 2)
                    for k, c in (("strategy", "strat"), ("static_mix", "mix"), ("buy_hold", "eq"))},
        "bootstrap_lower90_excess_vs_mix_pct": round(lower90 * 100, 2),
        "excess_vs_mix_costs_doubled_pct": round(excess_2x * 100, 2),
    }
    checks = {
        "holdout_beats_mix_by_1pp": res["holdout"]["strategy"] >= res["holdout"]["static_mix"] + 1.0,
        "dd_le_mix_and_20pct_below_bh": (max_dd(s) >= max_dd(mx)) and (abs(max_dd(s)) <= 0.8 * abs(max_dd(bh))),
        "bootstrap_lower90_positive": lower90 > 0,
        "costs_doubled_excess_nonnegative": excess_2x >= 0,
    }
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test6.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
