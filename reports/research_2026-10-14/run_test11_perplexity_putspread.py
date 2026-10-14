"""Test 11 in PREREGISTRATION.md (Perplexity): weekly NIFTY put spread, sell 3% OTM /
buy 6% OTM, entered the first trading day of the expiry week, post-Nov-2024 only.
Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test11_perplexity_putspread.py
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
sys.path.insert(0, str(HERE.parents[1]))

import condor_backtest_real as cbr  # noqa: E402
from nse_cost_model import get_cost_model  # noqa: E402

START, SHORT_OTM, LONG_OTM, QTY = "2024-11-20", 0.03, 0.06, 65
COSTS = get_cost_model()


def load():
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    opt = pd.read_sql("SELECT date, expiry, strike, close FROM options_eod "
                      "WHERE opt_type='PE' AND close > 0 AND date >= ?", con, params=(START,))
    con.close()
    px = {(d, e, float(k)): float(c) for d, e, k, c in opt.itertuples(index=False)}
    strikes = opt.groupby(["date", "expiry"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    _, spot = cbr._load()
    dates = sorted(set(opt["date"]) & set(spot))
    expiries = sorted(e for e in opt["expiry"].unique() if START <= e <= max(dates) and e in spot)
    return px, strikes, spot, dates, expiries


def fee(premium, side, d):
    return COSTS.single_leg_cost(premium * QTY, "OPT_SELL" if side == "SELL" else "OPT_BUY", side,
                                 include_slippage=False, trade_date=date.fromisoformat(d)).total


def run(px, strikes, spot, dates, expiries, slip):
    trades, skipped_cost = [], 0
    for e in expiries:
        week = date.fromisoformat(e).isocalendar()[:2]
        d = next((x for x in dates if date.fromisoformat(x).isocalendar()[:2] == week), None)
        if d is None or d >= e:
            continue
        ks = strikes.get((d, e), np.array([]))
        s_c, l_c = ks[ks <= spot[d] * (1 - SHORT_OTM)], ks[ks <= spot[d] * (1 - LONG_OTM)]
        if not len(s_c) or not len(l_c):
            continue
        k_s, k_l = float(s_c.max()), float(l_c.max())
        p_s, p_l = px.get((d, e, k_s)), px.get((d, e, k_l))
        if p_s is None or p_l is None or k_l >= k_s or p_s <= p_l:
            continue
        credit = p_s - p_l
        entry_cost = slip * (p_s + p_l) * QTY + fee(p_s, "SELL", d) + fee(p_l, "BUY", d)
        if entry_cost > 0.25 * credit * QTY:
            skipped_cost += 1
            continue
        owed = min(max(k_s - spot[e], 0.0), k_s - k_l)
        net = (credit - owed) * QTY - entry_cost
        max_loss = (k_s - k_l - credit) * QTY
        trades.append({"entry": d, "expiry": e, "credit": round(credit, 2), "max_loss_rs": round(max_loss),
                       "net_rs": round(net, 2), "r": net / max_loss,
                       "nifty_ret": spot[e] / spot[d] - 1})
    return pd.DataFrame(trades), skipped_cost


def summarise(df):
    if df.empty:
        return {"trades": 0}
    r, n = df["r"].to_numpy(), df["net_rs"].to_numpy()
    split = int(len(r) * 0.7)
    hr = r[split:]
    pf = lambda x: float(x[x > 0].sum() / -x[x < 0].sum()) if (x < 0).any() else float("inf")
    eq = np.cumsum(n)
    beta, alpha = np.polyfit(df["nifty_ret"].to_numpy(), r, 1) if len(r) > 2 else (np.nan, np.nan)
    return {"trades": int(len(r)), "total_net_rs": round(float(n.sum())), "expectancy_R": round(float(r.mean()), 4),
            "profit_factor": round(pf(r), 3), "win_rate": round(float((r > 0).mean()), 3),
            "holdout_start": df["entry"].iloc[split], "holdout_expectancy_R": round(float(hr.mean()), 4),
            "holdout_profit_factor": round(pf(hr), 3),
            "max_dd_rs": round(float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max())),
            "total_R_excl_best3": round(float(np.sort(r)[:-3].sum()), 3) if len(r) > 3 else None,
            "beta_intercept_R": round(float(alpha), 4), "beta_slope": round(float(beta), 3),
            "median_max_loss_per_lot_rs": round(float(df["max_loss_rs"].median())),
            "median_credit_pts": round(float(df["credit"].median()), 2)}


def main():
    data = load()
    primary, skipped = run(*data, slip=0.04)
    stress, _ = run(*data, slip=0.07)
    res = {"primary_slip4": summarise(primary), "skipped_by_25pct_cost_rule": skipped,
           "stress_slip7": summarise(stress),
           "sizing_note": "Perplexity caps risk at ₹5,000/trade; median one-lot max loss shown above."}
    p = res["primary_slip4"]
    checks = {"trades_ge_100": p.get("trades", 0) >= 100,
              "holdout_expectancy_gt_0_05R": p.get("holdout_expectancy_R", -1) > 0.05,
              "holdout_pf_gt_1_15": p.get("holdout_profit_factor", 0) > 1.15,
              "positive_at_7pct_slippage": res["stress_slip7"].get("total_net_rs", -1) > 0,
              "max_dd_lt_100k": p.get("max_dd_rs", 1e9) < 100_000,
              "positive_without_best3": (p.get("total_R_excl_best3") or -1) > 0,
              "beta_controlled_intercept_positive": p.get("beta_intercept_R", -1) > 0}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test11.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
