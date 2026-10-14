"""Test 8 in PREREGISTRATION.md: NIFTY near-the-money call calendar spreads
(Slivka, Liang & Xue 2020) on real EOD prices 2020-2026. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test8_calendar.py
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
from nse_cost_model import get_cost_model  # noqa: E402

R_RATE, MIBOR, QTY, HOLDOUT = 0.065, 0.065, 65, 0.30
COSTS = get_cost_model()


def bs_call(s, k, t, vol, r=R_RATE):
    if t <= 0 or vol <= 0:
        return max(0.0, s - k)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * n(d1) - k * math.exp(-r * t) * n(d2)


def implied_vol(price, s, k, t):
    lo, hi = 0.01, 3.0
    if not (bs_call(s, k, t, lo) < price < bs_call(s, k, t, hi)):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bs_call(s, k, t, mid) < price else (lo, mid)
    return (lo + hi) / 2


def load():
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    opt = pd.read_sql("SELECT date, expiry, strike, close FROM options_eod "
                      "WHERE opt_type='CE' AND close > 0", con)
    con.close()
    px = {(d, e, float(k)): float(c) for d, e, k, c in opt.itertuples(index=False)}
    strikes = opt.groupby(["date", "expiry"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    _, spot = cbr._load()
    dates = sorted(set(opt["date"]) & set(spot))
    exps = pd.to_datetime(pd.Series(sorted(opt["expiry"].unique())))
    monthly = sorted(exps.groupby(exps.dt.to_period("M")).max().dt.strftime("%Y-%m-%d"))
    return px, strikes, spot, dates, monthly


def leg_cost(premium, side, d, slip):
    turnover = premium * QTY
    fees = COSTS.single_leg_cost(turnover, "OPT_SELL" if side == "SELL" else "OPT_BUY", side,
                                 include_slippage=False, trade_date=date.fromisoformat(d)).total
    return slip * turnover + fees


def run(px, strikes, spot, dates, monthly, x_target, slip, rule1=True, rule2=True):
    trades, dropped = [], 0
    pos = {d: i for i, d in enumerate(dates)}
    for d in dates:
        later = [e for e in monthly if e > d]
        if len(later) < 2:
            continue
        t1, t2 = later[0], later[1]
        if t1 not in pos or sum(1 for x in dates if d < x <= t1) < 5:
            continue
        s0 = spot[d]
        common = np.intersect1d(strikes.get((d, t1), []), strikes.get((d, t2), []))
        if not len(common):
            continue
        k = float(common[np.argmin(np.abs(common - s0))])
        c1, c2 = px.get((d, t1, k)), px.get((d, t2, k))
        if c1 is None or c2 is None or c2 <= c1:
            continue
        d0 = c2 - c1
        days1 = (date.fromisoformat(t1) - date.fromisoformat(d)).days
        days21 = (date.fromisoformat(t2) - date.fromisoformat(t1)).days
        vol = implied_vol(c2, s0, k, (date.fromisoformat(t2) - date.fromisoformat(d)).days / 365)
        if vol is None:
            continue
        profit_at_t1 = lambda i: bs_call(i, k, days21 / 365, vol) - max(0.0, i - k) - d0
        pmax = profit_at_t1(k)
        rss = profit_at_t1(s0) * (365 / days1) / d0
        if pmax <= 0 or rss <= MIBOR:
            continue

        def root(a, b):
            for _ in range(60):
                m = (a + b) / 2
                if (profit_at_t1(a) > 0) == (profit_at_t1(m) > 0):
                    a = m
                else:
                    b = m
            return (a + b) / 2
        lbe = root(k, k * 0.6) if profit_at_t1(k * 0.6) < 0 else k * 0.6
        ube = root(k, k * 1.4) if profit_at_t1(k * 1.4) < 0 else k * 1.4

        last_day = dates[pos[t1] - 1]
        exit_day, exit_c1, exit_c2, reason = None, None, None, None
        for t in dates[pos[d] + 1:pos[last_day] + 1]:
            e1, e2 = px.get((t, t1, k)), px.get((t, t2, k))
            if t == last_day:
                exit_day, exit_c1, exit_c2, reason = t, e1, e2, "rule3"
                break
            if e1 is None or e2 is None:
                continue
            p_t = (e2 - e1) - d0
            held = (date.fromisoformat(t) - date.fromisoformat(d)).days
            if rule2 and not (lbe <= spot[t] <= ube):
                exit_day, exit_c1, exit_c2, reason = t, e1, e2, "rule2"
                break
            if rule1 and p_t * (365 / held) / d0 > MIBOR and p_t >= x_target * pmax:
                exit_day, exit_c1, exit_c2, reason = t, e1, e2, "rule1"
                break
        if exit_c1 is None or exit_c2 is None:
            dropped += 1
            continue
        gross = ((exit_c2 - exit_c1) - d0) * QTY
        cost = (leg_cost(c2, "BUY", d, slip) + leg_cost(c1, "SELL", d, slip)
                + leg_cost(exit_c2, "SELL", exit_day, slip) + leg_cost(exit_c1, "BUY", exit_day, slip))
        trades.append({"entry": d, "exit": exit_day, "reason": reason, "strike": k,
                       "debit_rs": round(d0 * QTY, 2), "net_rs": round(gross - cost, 2)})
    return pd.DataFrame(trades), dropped


def pf(x):
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def summarise(df):
    n = df["net_rs"]
    split = int(len(df) * (1 - HOLDOUT))
    hold = df.iloc[split:]["net_rs"]
    return {"trades": len(df), "total_net_rs": round(float(n.sum())), "profit_factor": round(pf(n), 3),
            "win_rate": round(float((n > 0).mean()), 3), "avg_net_rs": round(float(n.mean()), 1),
            "holdout_start": df.iloc[split]["entry"], "holdout_total_net_rs": round(float(hold.sum())),
            "holdout_profit_factor": round(pf(hold), 3),
            "exit_reasons": df["reason"].value_counts().to_dict()}


def main():
    data = load()
    primary, dropped = run(*data, x_target=0.50, slip=0.02)
    primary = primary.sort_values("entry").reset_index(drop=True)
    res = {"primary_X50_slip2": summarise(primary), "dropped_unpriceable": dropped}
    res["stress_X50_slip5"] = summarise(run(*data, x_target=0.50, slip=0.05)[0].sort_values("entry").reset_index(drop=True))
    base = run(*data, x_target=0.50, slip=0.02, rule1=False, rule2=False)[0].sort_values("entry").reset_index(drop=True)
    res["baseline_rule3_only"] = summarise(base)
    for x in (0.25, 0.75):
        res[f"X{int(x*100)}_slip2"] = summarise(run(*data, x_target=x, slip=0.02)[0].sort_values("entry").reset_index(drop=True))
    res["no_rule1_slip2"] = summarise(run(*data, x_target=0.5, slip=0.02, rule1=False)[0].sort_values("entry").reset_index(drop=True))

    monthly = primary.assign(m=primary["exit"].str[:7]).groupby("m")["net_rs"].sum().to_numpy()
    rng = np.random.default_rng(20261014)
    n, block = len(monthly), 12
    boots = [monthly[np.concatenate([(s + np.arange(block)) % n
                                     for s in rng.integers(0, n, int(np.ceil(n / block)))])[:n]].mean()
             for _ in range(5000)]
    res["bootstrap_lower90_mean_monthly_rs"] = round(float(np.quantile(boots, 0.05)), 1)
    # capital: sum of open debits on the busiest day
    days = sorted(set(primary["entry"]) | set(primary["exit"]))
    res["max_open_debit_rs"] = round(max(primary[(primary["entry"] <= d) & (primary["exit"] > d)]["debit_rs"].sum()
                                         for d in days))
    p = res["primary_X50_slip2"]
    checks = {"trades_ge_100": p["trades"] >= 100,
              "pf_full_and_holdout_ge_1_2": p["profit_factor"] >= 1.2 and p["holdout_profit_factor"] >= 1.2,
              "holdout_net_positive": p["holdout_total_net_rs"] > 0,
              "net_positive_at_5pct_slippage": res["stress_X50_slip5"]["total_net_rs"] > 0,
              "beats_rule3_only_baseline": p["total_net_rs"] > res["baseline_rule3_only"]["total_net_rs"],
              "bootstrap_lower90_positive": res["bootstrap_lower90_mean_monthly_rs"] > 0}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test8.json").write_text(json.dumps(res, indent=2, default=str))
    primary.to_csv(HERE / "results_test8_trades.csv", index=False)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
