"""Test 10 in PREREGISTRATION.md (DeepSeek): VRP-conditioned NIFTY weekly iron
condor on real EOD prices 2020-2026. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test10_vrp_condor.py
"""
from __future__ import annotations

import json
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

OTM, WING, QTY, SLIP, VRP_MIN = 0.015, 300, 65, 0.02, 3.0
COSTS = get_cost_model()


def load():
    # Only entry days are needed (expiry is settled from spot), so load just
    # those rows -- loading all 3.27M rows into a dict was killed (OOM).
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    all_dates = [r[0] for r in con.execute("SELECT DISTINCT date FROM options_eod ORDER BY date")]
    expiry_set = {r[0] for r in con.execute("SELECT DISTINCT expiry FROM options_eod")}
    entry_days = [all_dates[i] for i in range(1, len(all_dates)) if all_dates[i - 1] in expiry_set]
    opt = pd.read_sql("SELECT date, expiry, strike, opt_type, close FROM options_eod WHERE close > 0 "
                      "AND date IN (%s)" % ",".join("?" * len(entry_days)), con, params=entry_days)
    con.close()
    px = {(d, e, float(k), t): float(c) for d, e, k, t, c in opt.itertuples(index=False)}
    strikes = opt.groupby(["date", "expiry", "opt_type"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    expiries = opt.groupby("date")["expiry"].apply(lambda s: sorted(s.unique())).to_dict()
    _, spot = cbr._load()
    dates = sorted(set(all_dates) & set(spot))
    nifty = nifty_daily(spot)
    rv5 = np.log(nifty).diff().rolling(5).std() * np.sqrt(252) * 100
    vix = pd.read_csv(HERE / "india_vix_2019_2026.csv", parse_dates=["date"]).set_index("date")["vix_close"]
    return px, strikes, expiries, expiry_set, spot, dates, rv5, vix


def fee(premium, side, d):
    return COSTS.single_leg_cost(premium * QTY, "OPT_SELL" if side == "SELL" else "OPT_BUY", side,
                                 include_slippage=False, trade_date=date.fromisoformat(d)).total


def run(px, strikes, expiries, expiry_set, spot, dates, rv5, vix, *, use_filter=True, slip=SLIP):
    trades = []
    for i in range(1, len(dates)):
        d = dates[i]
        if dates[i - 1] not in expiry_set:          # first trading day after a weekly expiry
            continue
        exp = next((e for e in expiries.get(d, []) if e > d and e in spot), None)
        if exp is None:
            continue
        ts = pd.Timestamp(d)
        vrp = float(vix.asof(ts)) - float(rv5.asof(ts)) if not pd.isna(rv5.asof(ts)) else np.nan
        if use_filter and not (vrp > VRP_MIN):
            continue
        ce, pe = strikes.get((d, exp, "CE"), np.array([])), strikes.get((d, exp, "PE"), np.array([]))
        s = spot[d]
        sc = ce[ce >= s * (1 + OTM)]
        sp = pe[pe <= s * (1 - OTM)]
        if not len(sc) or not len(sp):
            continue
        k_sc, k_sp = float(sc.min()), float(sp.max())
        lc, lp = ce[ce >= k_sc + WING], pe[pe <= k_sp - WING]
        if not len(lc) or not len(lp):
            continue
        k_lc, k_lp = float(lc.min()), float(lp.max())
        legs = {"sc": px.get((d, exp, k_sc, "CE")), "lc": px.get((d, exp, k_lc, "CE")),
                "sp": px.get((d, exp, k_sp, "PE")), "lp": px.get((d, exp, k_lp, "PE"))}
        if any(v is None for v in legs.values()):
            continue
        credit = legs["sc"] - legs["lc"] + legs["sp"] - legs["lp"]
        if credit <= 0:
            continue
        cost = slip * sum(legs.values()) * QTY + fee(legs["sc"], "SELL", d) + fee(legs["sp"], "SELL", d) \
            + fee(legs["lc"], "BUY", d) + fee(legs["lp"], "BUY", d)
        s_exp = spot[exp]
        owed = min(max(s_exp - k_sc, 0.0), k_lc - k_sc) + min(max(k_sp - s_exp, 0.0), k_sp - k_lp)
        net = (credit - owed) * QTY - cost
        trades.append({"entry": d, "expiry": exp, "vrp": round(vrp, 2), "credit": round(credit, 2),
                       "net_rs": round(net, 2)})
    return pd.DataFrame(trades)


def stats(df):
    n = df["net_rs"].to_numpy()
    w, l = n[n > 0].sum(), -n[n < 0].sum()
    split = int(len(n) * 0.8)
    eq = np.cumsum(n)
    return {"trades": int(len(n)), "total_net_rs": round(float(n.sum())),
            "profit_factor": round(float(w / l), 3) if l else None, "win_rate": round(float((n > 0).mean()), 3),
            "avg_net_rs": round(float(n.mean()), 1),
            "max_dd_rs": round(float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max())),
            "holdout_start": df["entry"].iloc[split] if len(n) else None,
            "holdout_avg_net_rs": round(float(n[split:].mean()), 1) if len(n[split:]) else None,
            "worst_trades": df.nsmallest(3, "net_rs")[["entry", "net_rs"]].to_dict("records")}


def main():
    data = load()
    res = {"primary_vrp_filter": stats(run(*data)),
           "slippage_4pct": stats(run(*data, slip=0.04)),
           "no_filter_same_costs": stats(run(*data, use_filter=False))}
    p = res["primary_vrp_filter"]
    checks = {"profit_factor_gt_1_15": (p["profit_factor"] or 0) > 1.15,
              "trades_ge_200": p["trades"] >= 200,
              "holdout_expectancy_positive": (p["holdout_avg_net_rs"] or 0) > 0,
              "pf_gt_1_at_4pct_slippage": (res["slippage_4pct"]["profit_factor"] or 0) > 1.0}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test10.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
