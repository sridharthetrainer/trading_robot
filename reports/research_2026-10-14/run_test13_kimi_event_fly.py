"""Test 13 in PREREGISTRATION.md (Kimi): long NIFTY iron butterfly around scheduled
events, exit T+1. Real EOD prices 2020-2026. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test13_kimi_event_fly.py
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
from validation_harness import deflated_sharpe_ratio  # noqa: E402

QTY, SLIP, MAX_DEBIT, N_TRIALS = 65, 0.02, 76.9, 11
COSTS = get_cost_model()

# Sources: federalreserve.gov FOMC calendars (scheduled decisions only).
FOMC = """2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16""".split()
# Source: bls.gov yearly release schedules (Employment Situation).
NFP = """2020-01-10 2020-02-07 2020-03-06 2020-04-03 2020-05-08 2020-06-05 2020-07-02 2020-08-07
2020-09-04 2020-10-02 2020-11-06 2020-12-04 2021-01-08 2021-02-05 2021-03-05 2021-04-02 2021-05-07
2021-06-04 2021-07-02 2021-08-06 2021-09-03 2021-10-08 2021-11-05 2021-12-03 2022-01-07 2022-02-04
2022-03-04 2022-04-01 2022-05-06 2022-06-03 2022-07-08 2022-08-05 2022-09-02 2022-10-07 2022-11-04
2022-12-02 2023-01-06 2023-02-03 2023-03-10 2023-04-07 2023-05-05 2023-06-02 2023-07-07 2023-08-04
2023-09-01 2023-10-06 2023-11-03 2023-12-08 2024-01-05 2024-02-02 2024-03-08 2024-04-05 2024-05-03
2024-06-07 2024-07-05 2024-08-02 2024-09-06 2024-10-04 2024-11-01 2024-12-06 2025-01-10 2025-02-07
2025-03-07 2025-04-04 2025-05-02 2025-06-06 2025-07-03 2025-08-01 2025-09-05 2025-11-20 2025-12-16
2026-01-09 2026-02-11 2026-03-06 2026-04-03 2026-05-08 2026-06-05 2026-07-02 2026-08-07 2026-09-04
2026-10-02""".split()
BUDGET = "2020-02-01 2021-02-01 2022-02-01 2023-02-01 2024-02-01 2024-07-23 2025-02-01 2026-02-01".split()
# Only decision dates confirmed by RBI releases / news in this session.
RBI = """2020-02-06 2020-08-06 2020-10-09 2022-04-08 2022-05-04 2022-06-08 2022-08-05 2023-02-08
2023-04-06 2023-08-10 2024-02-08 2024-04-05 2024-10-09 2025-04-09 2026-02-06 2026-04-08 2026-06-05
2026-08-05 2026-10-07""".split()
AFTER_CLOSE = {"FOMC", "NFP"}


def events():
    out = [(d, "FOMC") for d in FOMC] + [(d, "NFP") for d in NFP]
    out += [(d, "BUDGET") for d in BUDGET] + [(d, "RBI") for d in RBI]
    return sorted(out)


def load():
    _, spot = cbr._load()
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    dates = sorted(set(r[0] for r in con.execute("SELECT DISTINCT date FROM options_eod")) & set(spot))
    plan = []
    for ev_day, kind in events():
        if kind in AFTER_CLOSE:
            entry = ev_day if ev_day in dates else None
        else:
            entry = max((d for d in dates if d < ev_day), default=None)
        exit_ = min((d for d in dates if d > ev_day), default=None)
        if entry and exit_:
            plan.append((ev_day, kind, entry, exit_))
    need = sorted({d for _, _, a, b in plan for d in (a, b)})
    opt = pd.read_sql("SELECT date, expiry, strike, opt_type, close FROM options_eod WHERE close > 0 "
                      "AND date IN (%s)" % ",".join("?" * len(need)), con, params=need)
    con.close()
    px = opt.set_index(["date", "expiry", "strike", "opt_type"])["close"].sort_index()
    strikes = opt.groupby(["date", "expiry", "opt_type"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    expiries = opt.groupby("date")["expiry"].apply(lambda s: sorted(s.unique())).to_dict()
    return plan, px, strikes, expiries, spot


def p(px, *k):
    try:
        return float(px.loc[k])
    except KeyError:
        return None


def fee(premium, side, d):
    return COSTS.single_leg_cost(premium * QTY, "OPT_SELL" if side == "SELL" else "OPT_BUY", side,
                                 include_slippage=False, trade_date=date.fromisoformat(d)).total


def run(plan, px, strikes, expiries, spot, *, wing=0.01, short_fly=False, slip=SLIP):
    trades = []
    for ev_day, kind, d, x in plan:
        exp = next((e for e in expiries.get(d, []) if e >= x), None)
        if exp is None:
            continue
        ce, pe = strikes.get((d, exp, "CE"), np.array([])), strikes.get((d, exp, "PE"), np.array([]))
        both = np.intersect1d(ce, pe)
        if not len(both):
            continue
        s = spot[d]
        k = float(both[np.argmin(np.abs(both - s))])
        cw, pw = ce[ce >= s * (1 + wing)], pe[pe <= s * (1 - wing)]
        if not len(cw) or not len(pw):
            continue
        kc, kp = float(cw.min()), float(pw.max())
        e = {"c": p(px, d, exp, k, "CE"), "p": p(px, d, exp, k, "PE"),
             "cw": p(px, d, exp, kc, "CE"), "pw": p(px, d, exp, kp, "PE")}
        if any(v is None for v in e.values()):
            continue
        debit = e["c"] + e["p"] - e["cw"] - e["pw"]
        if debit <= 0 or debit > MAX_DEBIT:
            continue
        if x == exp:
            sx = spot[x]
            o = {"c": max(sx - k, 0), "p": max(k - sx, 0), "cw": max(sx - kc, 0), "pw": max(kp - sx, 0)}
        else:
            o = {"c": p(px, x, exp, k, "CE"), "p": p(px, x, exp, k, "PE"),
                 "cw": p(px, x, exp, kc, "CE"), "pw": p(px, x, exp, kp, "PE")}
            if any(v is None for v in o.values()):
                continue
        value_out = o["c"] + o["p"] - o["cw"] - o["pw"]
        gross = (value_out - debit) * QTY * (-1 if short_fly else 1)
        buy_in, sell_in = (("cw", "pw"), ("c", "p")) if short_fly else (("c", "p"), ("cw", "pw"))
        cost = slip * (sum(e.values()) + (sum(o.values()) if x != exp else 0)) * QTY
        cost += sum(fee(e[l], "BUY", d) for l in buy_in) + sum(fee(e[l], "SELL", d) for l in sell_in)
        if x != exp:
            cost += sum(fee(o[l], "SELL", x) for l in buy_in) + sum(fee(o[l], "BUY", x) for l in sell_in)
        trades.append({"event": ev_day, "kind": kind, "entry": d, "exit": x, "expiry": exp,
                       "debit_pts": round(debit, 2), "net_rs": round(gross - cost, 2)})
    return pd.DataFrame(trades)


def stats(df):
    if df.empty:
        return {"trades": 0}
    n = df["net_rs"].to_numpy()
    pf = float(n[n > 0].sum() / -n[n < 0].sum()) if (n < 0).any() else float("inf")
    per_year = len(n) / ((pd.Timestamp(df["entry"].max()) - pd.Timestamp(df["entry"].min())).days / 365.25)
    sr = float(n.mean() / n.std(ddof=1) * math.sqrt(per_year)) if n.std(ddof=1) > 0 else 0.0
    sk, ku = float(pd.Series(n).skew()), float(pd.Series(n).kurt() + 3)
    return {"trades": int(len(n)), "total_net_rs": round(float(n.sum())), "expectancy_rs": round(float(n.mean()), 1),
            "profit_factor": round(pf, 3), "win_rate": round(float((n > 0).mean()), 3), "annual_sharpe": round(sr, 3),
            "deflated_sharpe": round(deflated_sharpe_ratio(sr, len(n), N_TRIALS, sk, ku), 4),
            "by_kind_expectancy_rs": {k: round(float(g.mean()), 1) for k, g in df.groupby("kind")["net_rs"]}}


def main():
    data = load()
    long1 = run(*data)
    res = {"long_fly_1pct": stats(long1), "long_fly_0_75pct": stats(run(*data, wing=0.0075)),
           "long_fly_1_25pct": stats(run(*data, wing=0.0125)),
           "DIAGNOSTIC_short_fly_1pct": stats(run(*data, short_fly=True)),
           "events_planned": len(data[0])}
    p1, a, b = res["long_fly_1pct"], res["long_fly_0_75pct"], res["long_fly_1_25pct"]
    sign = lambda s: np.sign(s.get("expectancy_rs", 0))
    checks = {"trades_ge_30": p1.get("trades", 0) >= 30, "expectancy_positive": p1.get("expectancy_rs", -1) > 0,
              "profit_factor_gt_1_15": p1.get("profit_factor", 0) > 1.15,
              "deflated_sharpe_ge_0_95": p1.get("deflated_sharpe", 0) >= 0.95,
              "stable_wings_same_sign": sign(a) == sign(p1) == sign(b)}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test13.json").write_text(json.dumps(res, indent=2, default=bool))
    long1.to_csv(HERE / "results_test13_trades.csv", index=False)
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
