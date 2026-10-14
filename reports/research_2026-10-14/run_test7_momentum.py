"""Test 7 in PREREGISTRATION.md: survivorship-free 12-1 cross-sectional momentum.

Read-only research; needs adjusted_returns.db from build_adjusted_returns.py.
    .venv/bin/python reports/research_2026-10-14/run_test7_momentum.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
from run_tests import nifty_daily  # noqa: E402

ETF = re.compile(r"BEES|ETF|IETF|LIQUID|NIFTY|SENSEX|GOLD|SILVER|NV20|BHARAT22|CPSE")
UNIVERSE, TOP_N, SKIP, LIQ_WINDOW = 200, 30, 21, 126
COST, EXIT_HAIRCUT, HOLDOUT = 0.003, 0.20, 0.30


def load():
    con = sqlite3.connect(f"file:{HERE / 'adjusted_returns.db'}?mode=ro", uri=True)
    d = pd.read_sql("SELECT symbol, date, close, ret, traded_value FROM daily", con)
    con.close()
    d = d[~d["symbol"].str.contains(ETF)]
    R = d.pivot(index="date", columns="symbol", values="ret").sort_index()
    C = d.pivot(index="date", columns="symbol", values="close").reindex(R.index)
    TV = d.pivot(index="date", columns="symbol", values="traded_value").reindex(R.index)
    return R, C, TV


def holding_return(r: np.ndarray) -> float:
    """Buy-and-hold over the period; a series that stops is exited at its last
    close minus the haircut, the rest of the period in cash."""
    if np.isnan(r).any():
        valid = r[: int(np.argmax(np.isnan(r)))]
        return float(np.prod(1 + valid) * (1 - EXIT_HAIRCUT) - 1)
    return float(np.prod(1 + r) - 1)


def select(Rv, Cv, TVv, p: int, lookback: int = 252):
    """Universe and momentum picks (column indices) at month-end row p.
    Shared by the backtest and momentum_paper_tracker.py so both use one rule."""
    window = Rv[p - 251:p + 1]
    ok = ~np.isnan(window).any(axis=0) & (Cv[p] >= 20)
    liq = np.nanmedian(TVv[p - LIQ_WINDOW + 1:p + 1], axis=0)
    cand = np.where(ok)[0]
    uni = cand[np.argsort(-liq[cand])[:UNIVERSE]]
    sig = np.prod(1 + Rv[p - lookback + 1:p - SKIP + 1, uni], axis=0) - 1
    mom = uni[np.argsort(-sig)[:TOP_N]]
    return uni, mom


def month_end_rows(index) -> np.ndarray:
    month = pd.to_datetime(index).to_period("M")
    return np.where(month[:-1] != month[1:])[0]          # last trading day of each month


def simulate(R, C, TV, lookback: int, cost: float):
    dates = R.index.to_numpy()
    Rv, Cv, TVv = R.to_numpy(), C.to_numpy(), TV.to_numpy()
    month_end = month_end_rows(R.index)
    rows, prev_mom, prev_uni = [], set(), set()
    for k in range(len(month_end) - 1):
        p, q = month_end[k], month_end[k + 1]
        if p < 252 or q + 1 >= len(dates):
            continue
        uni, mom = select(Rv, Cv, TVv, p, lookback)
        period = Rv[p + 2:q + 2]                                # next-day close to next month's next-day close
        mom_ret = np.mean([holding_return(period[:, j]) for j in mom])
        uni_ret = np.mean([holding_return(period[:, j]) for j in uni])
        mom_set, uni_set = set(mom), set(uni)
        mom_turn = 1.0 if not prev_mom else len(mom_set - prev_mom) / TOP_N
        uni_turn = 1.0 if not prev_uni else len(uni_set - prev_uni) / UNIVERSE
        rows.append({"start": dates[p + 1], "end": dates[q + 1],
                     "momentum": mom_ret - 2 * cost * mom_turn + (cost if not prev_mom else 0),
                     "equal_weight": uni_ret - 2 * cost * uni_turn + (cost if not prev_uni else 0),
                     "turnover": mom_turn, "names": [R.columns[j] for j in mom]})
        prev_mom, prev_uni = mom_set, uni_set
    return pd.DataFrame(rows)


def ann(r) -> float:
    r = np.asarray(r)
    return float(np.prod(1 + r) ** (12 / len(r)) - 1)


def sharpe(r) -> float:
    r = np.asarray(r)
    return float(r.mean() / r.std(ddof=1) * np.sqrt(12))


def max_dd(r) -> float:
    eq = np.cumprod(1 + np.asarray(r))
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def main() -> None:
    R, C, TV = load()
    df = simulate(R, C, TV, 252, COST)
    _, spot = cbr._load()
    nifty = nifty_daily(spot)
    df["nifty"] = [float(nifty.asof(pd.Timestamp(e)) / nifty.asof(pd.Timestamp(s)) - 1)
                   for s, e in zip(df["start"], df["end"])]
    split = int(len(df) * (1 - HOLDOUT))
    hold = df.iloc[split:]

    excess = (df["momentum"] - df["equal_weight"]).to_numpy()
    rng = np.random.default_rng(20261014)
    n, block = len(excess), 12
    boots = [excess[np.concatenate([(s + np.arange(block)) % n
                                    for s in rng.integers(0, n, int(np.ceil(n / block)))])[:n]].mean() * 12
             for _ in range(5000)]
    lower90 = float(np.quantile(boots, 0.05))

    df2 = simulate(R, C, TV, 252, 2 * COST)
    excess_2x = ann(df2["momentum"]) - ann(df2["equal_weight"])
    stability = {}
    for lb, label in ((126, "6-1"), (189, "9-1")):
        d = simulate(R, C, TV, lb, COST)
        stability[label] = {"momentum_pct": round(ann(d["momentum"]) * 100, 2),
                            "equal_weight_pct": round(ann(d["equal_weight"]) * 100, 2)}

    res = {
        "months": len(df), "start": str(df["start"].iloc[0]), "end": str(df["end"].iloc[-1]),
        "holdout_start": str(hold["start"].iloc[0]), "avg_monthly_turnover": round(df["turnover"].mean(), 3),
        "full": {k: {"ann_return_pct": round(ann(df[k]) * 100, 2), "sharpe": round(sharpe(df[k]), 3),
                     "max_dd_pct": round(max_dd(df[k]) * 100, 1)}
                 for k in ("momentum", "equal_weight", "nifty")},
        "holdout": {k: round(ann(hold[k]) * 100, 2) for k in ("momentum", "equal_weight", "nifty")},
        "bootstrap_lower90_excess_vs_ew_pct": round(lower90 * 100, 2),
        "excess_vs_ew_costs_doubled_pct": round(excess_2x * 100, 2),
        "stability": stability,
        "latest_portfolio": df["names"].iloc[-1],
    }
    f = res["full"]
    checks = {
        "holdout_beats_ew_and_nifty_by_2pp": res["holdout"]["momentum"] >= res["holdout"]["equal_weight"] + 2
        and res["holdout"]["momentum"] >= res["holdout"]["nifty"] + 2,
        "sharpe_above_nifty": f["momentum"]["sharpe"] > f["nifty"]["sharpe"],
        "bootstrap_lower90_positive": lower90 > 0,
        "costs_doubled_excess_nonnegative": excess_2x >= 0,
        "max_dd_within_1_5x_nifty": f["momentum"]["max_dd_pct"] >= 1.5 * f["nifty"]["max_dd_pct"],
        "stable_6_1_and_9_1": all(v["momentum_pct"] > v["equal_weight_pct"] for v in stability.values()),
    }
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test7.json").write_text(json.dumps(res, indent=2, default=str))
    df.drop(columns=["names"]).to_csv(HERE / "results_test7_monthly.csv", index=False)
    print(json.dumps({k: v for k, v in res.items() if k != "latest_portfolio"}, indent=2, default=str))


if __name__ == "__main__":
    main()
