"""Test 9 in PREREGISTRATION.md (Grok #2): NIFTY put credit spread only when
NIFTY > 200-DMA and India VIX is in the top tercile of its trailing year.
Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test9_put_vix.py
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
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
from nse_cost_model import get_cost_model  # noqa: E402
from run_tests import nifty_daily  # noqa: E402
from validation_harness import deflated_sharpe_ratio  # noqa: E402

OTM, WING, MIN_DTE, QTY, SLIP, N_TRIALS = 0.015, 300, 3, 65, 0.02, 8
COSTS = get_cost_model()


def load():
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    opt = pd.read_sql("SELECT date, expiry, strike, close FROM options_eod "
                      "WHERE opt_type='PE' AND close > 0", con)
    con.close()
    px = {(d, e, float(k)): float(c) for d, e, k, c in opt.itertuples(index=False)}
    strikes = opt.groupby(["date", "expiry"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    expiries = opt.groupby("date")["expiry"].apply(lambda s: sorted(s.unique())).to_dict()
    _, spot = cbr._load()
    dates = sorted(set(opt["date"]) & set(spot))
    nifty = nifty_daily(spot)
    sma200 = nifty.rolling(200).mean()
    vix = pd.read_csv(HERE / "india_vix_2019_2026.csv", parse_dates=["date"]).set_index("date")["vix_close"]
    vix_p67 = vix.rolling(252).quantile(0.67)
    return px, strikes, expiries, spot, dates, nifty, sma200, vix, vix_p67


def fees(premium, side, d, mult=1.0):
    return mult * COSTS.single_leg_cost(premium * QTY, "OPT_SELL" if side == "SELL" else "OPT_BUY", side,
                                        include_slippage=False, trade_date=date.fromisoformat(d)).total


def run(px, strikes, expiries, spot, dates, nifty, sma200, vix, vix_p67, *, use_filter=True,
        take_profit=False, slip=SLIP, fee_mult=1.0):
    pos = {d: i for i, d in enumerate(dates)}
    trades, seen = [], set()
    for d in dates:
        wk = date.fromisoformat(d).isocalendar()[:2]
        if wk in seen:
            continue
        seen.add(wk)                       # first trading day of the week decides; no re-entry
        exp = next((e for e in expiries.get(d, []) if e > d and e in pos and pos[e] - pos[d] >= MIN_DTE), None)
        if exp is None:
            continue
        ts = pd.Timestamp(d)
        if use_filter:
            s200, v, p67 = sma200.asof(ts), vix.asof(ts), vix_p67.asof(ts)
            if any(pd.isna(x) for x in (s200, v, p67)) or not (nifty.asof(ts) > s200 and v >= p67):
                continue
        ks = strikes.get((d, exp), np.array([]))
        short_c = ks[ks <= spot[d] * (1 - OTM)]
        if not len(short_c):
            continue
        ks_short = float(short_c.max())
        long_c = ks[ks <= ks_short - WING]
        if not len(long_c):
            continue
        ks_long = float(long_c.max())
        p_s, p_l = px.get((d, exp, ks_short)), px.get((d, exp, ks_long))
        if p_s is None or p_l is None or p_s <= p_l:
            continue
        credit = p_s - p_l
        cost = slip * (p_s + p_l) * QTY + fees(p_s, "SELL", d, fee_mult) + fees(p_l, "BUY", d, fee_mult)
        exit_day, buyback = exp, None
        if take_profit:
            for t in dates[pos[d] + 1:pos[exp]]:
                b_s, b_l = px.get((t, exp, ks_short)), px.get((t, exp, ks_long))
                if b_s is not None and b_l is not None and (b_s - b_l) <= 0.5 * credit:
                    exit_day, buyback = t, b_s - b_l
                    cost += slip * (b_s + b_l) * QTY + fees(b_s, "BUY", t, fee_mult) + fees(b_l, "SELL", t, fee_mult)
                    break
        if buyback is None:
            s_exp = spot[exp]
            buyback = min(max(ks_short - s_exp, 0.0), ks_short - ks_long)
        net = (credit - buyback) * QTY - cost
        max_loss = (ks_short - ks_long - credit) * QTY
        trades.append({"entry": d, "expiry": exp, "exit": exit_day, "credit": round(credit, 2),
                       "net_rs": round(net, 2), "r": net / max_loss if max_loss > 0 else 0.0})
    return pd.DataFrame(trades)


def stats(df):
    n = df["net_rs"].to_numpy()
    w, l = n[n > 0].sum(), -n[n < 0].sum()
    eq = np.cumsum(n)
    dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()) if len(n) else 0.0
    r = df["r"].to_numpy()
    sr = float(r.mean() / r.std(ddof=1) * math.sqrt(52)) if len(r) > 1 and r.std(ddof=1) > 0 else 0.0
    sk = float(pd.Series(r).skew()) if len(r) > 2 else 0.0
    ku = float(pd.Series(r).kurt() + 3) if len(r) > 3 else 3.0
    split = int(len(n) * 0.7)
    yr = df["entry"].str[:4].astype(int)
    regimes = {lab: round(float(n[(yr >= a) & (yr <= b)].sum()))
               for lab, (a, b) in (("2020-21", (2020, 2021)), ("2022-23", (2022, 2023)), ("2024-26", (2024, 2026)))}
    return {"trades": int(len(n)), "total_net_rs": round(float(n.sum())), "profit_factor": round(w / l, 3) if l else None,
            "win_rate": round(float((n > 0).mean()), 3), "expectancy_R": round(float(r.mean()), 4),
            "max_dd_rs": round(dd), "annual_sharpe": round(sr, 3),
            "deflated_sharpe": round(deflated_sharpe_ratio(sr, len(n), N_TRIALS, sk, ku), 4),
            "holdout_net_rs": round(float(n[split:].sum())), "regimes_net_rs": regimes}


def main():
    data = load()
    res = {"primary_filtered_hold": stats(run(*data)),
           "filtered_takeprofit50": stats(run(*data, take_profit=True)),
           "unfiltered_hold_same_costs": stats(run(*data, use_filter=False)),
           "filtered_hold_costs_doubled": stats(run(*data, slip=2 * SLIP, fee_mult=2.0))}
    p = res["primary_filtered_hold"]
    checks = {"deflated_sharpe_gt_0_5": p["deflated_sharpe"] > 0.5,
              "profit_factor_gt_1_2": (p["profit_factor"] or 0) > 1.2,
              "max_dd_lt_125k": p["max_dd_rs"] < 125_000,
              "positive_every_regime": all(v > 0 for v in p["regimes_net_rs"].values()),
              "positive_costs_doubled": res["filtered_hold_costs_doubled"]["total_net_rs"] > 0,
              "holdout_positive": p["holdout_net_rs"] > 0}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test9.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
