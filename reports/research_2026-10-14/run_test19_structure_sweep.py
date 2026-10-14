"""Test 19 in PREREGISTRATION.md: one mechanical rule applied to every option
structure from the pasted 62-strategy catalog that can be built from EOD data.
Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test19_structure_sweep.py
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

QTY, T_BONF = 65, 3.2
COSTS = get_cost_model()

# leg = (kind, offset, expiry, qty): kind C/P/U, offset vs spot, expiry F(front)/B(back)
S = {
    "01_long_atm_call": [("C", 0, "F", 1)], "02_long_otm_call": [("C", .02, "F", 1)],
    "03_long_itm_call": [("C", -.02, "F", 1)], "04_long_atm_put": [("P", 0, "F", 1)],
    "05_long_otm_put": [("P", -.02, "F", 1)], "06_long_itm_put": [("P", .02, "F", 1)],
    "07_short_otm_call": [("C", .02, "F", -1)], "08_short_otm_put": [("P", -.02, "F", -1)],
    "09_bull_call_debit": [("C", 0, "F", 1), ("C", .02, "F", -1)],
    "10_bull_put_credit": [("P", 0, "F", -1), ("P", -.02, "F", 1)],
    "11_bear_put_debit": [("P", 0, "F", 1), ("P", -.02, "F", -1)],
    "12_bear_call_credit": [("C", 0, "F", -1), ("C", .02, "F", 1)],
    "13_synthetic_long": [("C", 0, "F", 1), ("P", 0, "F", -1)],
    "14_synthetic_short": [("P", 0, "F", 1), ("C", 0, "F", -1)],
    "15_short_straddle": [("C", 0, "F", -1), ("P", 0, "F", -1)],
    "16_short_strangle": [("C", .02, "F", -1), ("P", -.02, "F", -1)],
    "17_iron_condor": [("C", .02, "F", -1), ("C", .04, "F", 1), ("P", -.02, "F", -1), ("P", -.04, "F", 1)],
    "18_iron_butterfly": [("C", 0, "F", -1), ("P", 0, "F", -1), ("C", .02, "F", 1), ("P", -.02, "F", 1)],
    "19_long_call_fly": [("C", -.02, "F", 1), ("C", 0, "F", -2), ("C", .02, "F", 1)],
    "20_long_put_fly": [("P", .02, "F", 1), ("P", 0, "F", -2), ("P", -.02, "F", 1)],
    "21_broken_wing_call_fly": [("C", -.02, "F", 1), ("C", 0, "F", -2), ("C", .04, "F", 1)],
    "22_long_iron_butterfly": [("C", 0, "F", 1), ("P", 0, "F", 1), ("C", .02, "F", -1), ("P", -.02, "F", -1)],
    "23_long_iron_condor": [("C", .02, "F", 1), ("C", .04, "F", -1), ("P", -.02, "F", 1), ("P", -.04, "F", -1)],
    "24_short_call_fly": [("C", -.02, "F", -1), ("C", 0, "F", 2), ("C", .02, "F", -1)],
    "25_short_put_fly": [("P", .02, "F", -1), ("P", 0, "F", 2), ("P", -.02, "F", -1)],
    "26_short_call_condor": [("C", -.04, "F", -1), ("C", -.02, "F", 1), ("C", .02, "F", 1), ("C", .04, "F", -1)],
    "27_long_straddle": [("C", 0, "F", 1), ("P", 0, "F", 1)],
    "28_long_strangle": [("C", .02, "F", 1), ("P", -.02, "F", 1)],
    "29_long_strip": [("C", 0, "F", 1), ("P", 0, "F", 2)],
    "30_long_strap": [("C", 0, "F", 2), ("P", 0, "F", 1)],
    "31_call_ratio_backspread": [("C", 0, "F", -1), ("C", .02, "F", 2)],
    "32_put_ratio_backspread": [("P", 0, "F", -1), ("P", -.02, "F", 2)],
    "37_long_call_calendar": [("C", 0, "F", -1), ("C", 0, "B", 1)],
    "38_long_put_calendar": [("P", 0, "F", -1), ("P", 0, "B", 1)],
    "39_reverse_call_calendar": [("C", 0, "F", 1), ("C", 0, "B", -1)],
    "40_reverse_put_calendar": [("P", 0, "F", 1), ("P", 0, "B", -1)],
    "41_double_calendar": [("C", .02, "F", -1), ("C", .02, "B", 1), ("P", -.02, "F", -1), ("P", -.02, "B", 1)],
    "42_bull_call_diagonal": [("C", .02, "F", -1), ("C", 0, "B", 1)],
    "43_bear_call_diagonal": [("C", 0, "F", -1), ("C", .02, "B", 1)],
    "44_bull_put_diagonal": [("P", 0, "F", -1), ("P", -.02, "B", 1)],
    "45_bear_put_diagonal": [("P", -.02, "F", -1), ("P", 0, "B", 1)],
    "46_ratio_calendar": [("C", 0, "F", -2), ("C", 0, "B", 1)],
    "49_call_ratio_spread": [("C", 0, "F", 1), ("C", .02, "F", -2)],
    "50_put_ratio_spread": [("P", 0, "F", 1), ("P", -.02, "F", -2)],
    "53_covered_call": [("U", 0, "F", 1), ("C", .02, "F", -1)],
    "55_protective_put": [("U", 0, "F", 1), ("P", -.02, "F", 1)],
    "57_collar": [("U", 0, "F", 1), ("P", -.02, "F", 1), ("C", .02, "F", -1)],
    "60_risk_reversal": [("C", .02, "F", 1), ("P", -.02, "F", -1)],
}


def load():
    _, spot = cbr._load()
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    all_dates = sorted(set(r[0] for r in con.execute("SELECT DISTINCT date FROM options_eod")) & set(spot))
    expiries = sorted(r[0] for r in con.execute("SELECT DISTINCT expiry FROM options_eod"))
    exp_set = set(expiries)
    plan = []
    for i in range(1, len(all_dates)):
        d = all_dates[i]
        if all_dates[i - 1] not in exp_set:
            continue
        later = [e for e in expiries if e > d and e in spot]
        if len(later) < 2:
            continue
        plan.append((d, later[0], later[1]))
    need = sorted({x for d, f, b in plan for x in (d, f)})
    opt = pd.read_sql("SELECT date, expiry, strike, opt_type, close FROM options_eod WHERE close > 0 "
                      "AND date IN (%s)" % ",".join("?" * len(need)), con, params=need)
    con.close()
    px = opt.set_index(["date", "expiry", "strike", "opt_type"])["close"].sort_index()
    strikes = opt.groupby(["date", "expiry", "opt_type"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    return plan, px, strikes, spot


def price(px, *k):
    try:
        return float(px.loc[k])
    except KeyError:
        return None


def fee(turnover, instrument, side, d):
    return COSTS.single_leg_cost(abs(turnover), instrument, side, include_slippage=False,
                                 trade_date=date.fromisoformat(d)).total


def run_structure(legs, plan, px, strikes, spot, slip):
    out = []
    for d, front, back in plan:
        s0 = spot[d]
        pnl, cost, ok = 0.0, 0.0, True
        for kind, off, which, q in legs:
            if kind == "U":
                s1 = spot[front]
                pnl += q * (s1 - s0) * QTY
                cost += fee(s0 * QTY * abs(q), "FUT", "BUY" if q > 0 else "SELL", d)
                cost += fee(s1 * QTY * abs(q), "FUT", "SELL" if q > 0 else "BUY", front)
                continue
            exp = front if which == "F" else back
            ks = strikes.get((d, exp, "CE" if kind == "C" else "PE"))
            if ks is None or not len(ks):
                ok = False
                break
            k = float(ks[np.argmin(np.abs(ks - s0 * (1 + off)))])
            p0 = price(px, d, exp, k, "CE" if kind == "C" else "PE")
            if p0 is None:
                ok = False
                break
            side_in = "BUY" if q > 0 else "SELL"
            cost += slip * p0 * QTY * abs(q) + fee(p0 * QTY * abs(q), "OPT_" + side_in, side_in, d)
            if which == "F":
                p1 = max(spot[front] - k, 0.0) if kind == "C" else max(k - spot[front], 0.0)
            else:
                p1 = price(px, front, back, k, "CE" if kind == "C" else "PE")
                if p1 is None:
                    ok = False
                    break
                side_out = "SELL" if q > 0 else "BUY"
                cost += slip * p1 * QTY * abs(q) + fee(p1 * QTY * abs(q), "OPT_" + side_out, side_out, front)
            pnl += q * (p1 - p0) * QTY
        if ok:
            out.append({"entry": d, "net_rs": pnl - cost})
    return pd.DataFrame(out)


def stats(df):
    if df.empty:
        return {"trades": 0}
    n = df["net_rs"].to_numpy()
    split = int(len(n) * 0.7)
    sd = n.std(ddof=1)
    return {"trades": int(len(n)), "mean_rs": round(float(n.mean()), 1),
            "t": round(float(n.mean() / (sd / np.sqrt(len(n)))), 2) if sd > 0 else 0.0,
            "profit_factor": round(float(n[n > 0].sum() / -n[n < 0].sum()), 3) if (n < 0).any() else None,
            "win_rate": round(float((n > 0).mean()), 3), "worst_rs": round(float(n.min())),
            "holdout_mean_rs": round(float(n[split:].mean()), 1)}


def main():
    plan, px, strikes, spot = load()
    rows = {}
    for name, legs in S.items():
        a = stats(run_structure(legs, plan, px, strikes, spot, 0.02))
        b = stats(run_structure(legs, plan, px, strikes, spot, 0.04))
        checks = {"trades_ge_100": a.get("trades", 0) >= 100,
                  "mean_pos_t_gt_bonf": a.get("mean_rs", -1) > 0 and a.get("t", 0) > T_BONF,
                  "holdout_pos": a.get("holdout_mean_rs", -1) > 0,
                  "pf_gt_1_1": (a.get("profit_factor") or 0) > 1.1,
                  "pos_at_4pct": b.get("mean_rs", -1) > 0}
        rows[name] = {**a, "mean_rs_4pct": b.get("mean_rs"), "verdict": "PASS" if all(checks.values()) else "FAIL",
                      "failed": [k for k, v in checks.items() if not v]}
        print(f"{name:28s} n={a.get('trades',0):4d} mean=₹{a.get('mean_rs',0):>8} t={a.get('t',0):>6} "
              f"PF={a.get('profit_factor')} hold=₹{a.get('holdout_mean_rs')} 4%=₹{b.get('mean_rs')} {rows[name]['verdict']}",
              flush=True)
    res = {"cycles": len(plan), "structures": rows,
           "passed": [k for k, v in rows.items() if v["verdict"] == "PASS"]}
    (HERE / "results_test19.json").write_text(json.dumps(res, indent=2, default=str))
    print("PASSED:", res["passed"])


if __name__ == "__main__":
    main()
