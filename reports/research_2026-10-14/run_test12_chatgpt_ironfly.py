"""Test 12 in PREREGISTRATION.md (ChatGPT #1): VRP-conditioned monthly NIFTY iron
fly, 150-point wings, 5-session hold, real EOD prices 2020-2026. Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test12_chatgpt_ironfly.py
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

WING, QTY, HOLD, OOS_START, REFORM = 150, 65, 5, "2023-01-01", "2024-11-20"
COSTS = get_cost_model()


def load():
    con = sqlite3.connect(f"file:{HERE.parents[1] / 'options_nifty.db'}?mode=ro", uri=True)
    exps = pd.to_datetime(pd.Series([r[0] for r in con.execute("SELECT DISTINCT expiry FROM options_eod")]))
    monthly = sorted(exps.groupby(exps.dt.to_period("M")).max().dt.strftime("%Y-%m-%d"))
    # Only monthly contracts are needed (keeps memory small -- Test 10 lesson).
    opt = pd.read_sql("SELECT date, expiry, strike, opt_type, close FROM options_eod WHERE close > 0 "
                      "AND expiry IN (%s)" % ",".join("?" * len(monthly)), con, params=monthly)
    con.close()
    px = opt.set_index(["date", "expiry", "strike", "opt_type"])["close"].sort_index()
    strikes = opt.groupby(["date", "expiry"])["strike"].apply(lambda s: np.sort(s.unique())).to_dict()
    _, spot = cbr._load()
    dates = sorted(set(opt["date"]) & set(spot))
    nifty = nifty_daily(spot)
    r = np.log(nifty).diff().dropna()
    v = r.pow(2).ewm(alpha=0.06, adjust=False).mean()          # v_t = 0.94 v_{t-1} + 0.06 r_t^2
    vix = pd.read_csv(HERE / "india_vix_2019_2026.csv", parse_dates=["date"]).set_index("date")["vix_close"]
    vrp = ((vix / 100) ** 2 - 252 * v.reindex(vix.index, method="ffill")).dropna()
    p75 = vrp.shift(1).rolling(252, min_periods=252).quantile(0.75)
    return px, strikes, spot, dates, monthly, vrp, p75


def price(px, *key):
    try:
        return float(px.loc[key])
    except KeyError:
        return None


def fee(premium, side, d, mult):
    return mult * COSTS.single_leg_cost(premium * QTY, "OPT_SELL" if side == "SELL" else "OPT_BUY", side,
                                        include_slippage=False, trade_date=date.fromisoformat(d)).total


def legs_at(px, d, exp, k):
    return {"sc": price(px, d, exp, k, "CE"), "sp": price(px, d, exp, k, "PE"),
            "lc": price(px, d, exp, k + WING, "CE"), "lp": price(px, d, exp, k - WING, "PE")}


def run(px, strikes, spot, dates, monthly, vrp, p75, *, conditioned=True, slip=0.02, fee_mult=1.0):
    pos = {d: i for i, d in enumerate(dates)}
    trades = []
    for prev_exp, _ in zip(monthly, monthly[1:]):
        if prev_exp not in pos:
            continue
        for d in dates[pos[prev_exp] + 1:]:
            target = next((e for e in monthly if 21 <= (date.fromisoformat(e) - date.fromisoformat(d)).days <= 35), None)
            nxt_cycle = next((e for e in monthly if e > prev_exp), None)
            if nxt_cycle is None or d >= nxt_cycle:
                break                                           # cycle over
            if target is None:                                  # no 21-35-day monthly expiry today
                continue
            if conditioned:
                prev_day = dates[pos[d] - 1]
                ts = pd.Timestamp(prev_day)
                val, thr = vrp.asof(ts), p75.asof(ts)
                if pd.isna(val) or pd.isna(thr) or not (val > 0 and val >= thr):
                    continue
            ks = strikes.get((d, target), np.array([]))
            if not len(ks):
                continue
            k = float(ks[np.argmin(np.abs(ks - spot[d]))])
            entry = legs_at(px, d, target, k)
            if any(x is None for x in entry.values()):
                continue
            exit_i = pos[d] + HOLD
            if exit_i >= len(dates) or dates[exit_i] > target:
                break
            x_day = dates[exit_i]
            out = legs_at(px, x_day, target, k)
            if any(x is None for x in out.values()):
                break                                           # unpriceable exit: skip this cycle
            credit = entry["sc"] + entry["sp"] - entry["lc"] - entry["lp"]
            close_cost = out["sc"] + out["sp"] - out["lc"] - out["lp"]
            cost = slip * (sum(entry.values()) + sum(out.values())) * QTY
            cost += fee(entry["sc"], "SELL", d, fee_mult) + fee(entry["sp"], "SELL", d, fee_mult)
            cost += fee(entry["lc"], "BUY", d, fee_mult) + fee(entry["lp"], "BUY", d, fee_mult)
            cost += fee(out["sc"], "BUY", x_day, fee_mult) + fee(out["sp"], "BUY", x_day, fee_mult)
            cost += fee(out["lc"], "SELL", x_day, fee_mult) + fee(out["lp"], "SELL", x_day, fee_mult)
            trades.append({"entry": d, "exit": x_day, "expiry": target, "strike": k,
                           "credit_pts": round(credit, 2), "max_contract_loss_rs": round((WING - credit) * QTY),
                           "net_rs": round((credit - close_cost) * QTY - cost, 2)})
            break                                               # one position per cycle
    return pd.DataFrame(trades)


def summary(df):
    if df.empty:
        return {"trades": 0}
    oos, post = df[df["entry"] >= OOS_START]["net_rs"], df[df["entry"] >= REFORM]["net_rs"]
    return {"trades": int(len(df)), "total_net_rs": round(float(df["net_rs"].sum())),
            "mean_net_rs": round(float(df["net_rs"].mean()), 1), "win_rate": round(float((df["net_rs"] > 0).mean()), 3),
            "oos_trades": int(len(oos)), "oos_mean_net_rs": round(float(oos.mean()), 1) if len(oos) else None,
            "post_reform_trades": int(len(post)),
            "post_reform_mean_net_rs": round(float(post.mean()), 1) if len(post) else None,
            "worst_rs": round(float(df["net_rs"].min())),
            "max_contract_loss_rs": int(df["max_contract_loss_rs"].max())}


def main():
    data = load()
    cond = run(*data)
    res = {"conditioned": summary(cond), "conditioned_stress": summary(run(*data, slip=0.04, fee_mult=2.0)),
           "unconditional": summary(run(*data, conditioned=False))}
    oos = cond[cond["entry"] >= OOS_START]["net_rs"].to_numpy()
    lower95 = None
    if len(oos) >= 3:
        rng = np.random.default_rng(20261014)
        b, n = 3, len(oos)
        boots = [oos[np.concatenate([(s + np.arange(b)) % n for s in rng.integers(0, n, int(np.ceil(n / b)))])[:n]].mean()
                 for _ in range(5000)]
        lower95 = round(float(np.quantile(boots, 0.025)), 1)
    res["oos_bootstrap_lower95_mean_rs"] = lower95
    c, s, u = res["conditioned"], res["conditioned_stress"], res["unconditional"]
    g = {"G1_oos_mean_positive": (c.get("oos_mean_net_rs") or -1) > 0,
         "G2_stress_oos_mean_positive": (s.get("oos_mean_net_rs") or -1) > 0,
         "G3_beats_unconditional": (c.get("oos_mean_net_rs") or -1e9) > (u.get("oos_mean_net_rs") or -1e9),
         "G4_bootstrap_lower95_positive": (lower95 or -1) > 0,
         "G5_max_contract_loss_le_10k": c.get("max_contract_loss_rs", 1e9) <= 10_000,
         "G7_oos_trades_ge_100": c.get("oos_trades", 0) >= 100,
         "G8_forward_50": False}
    res["gates"] = g
    core = all(g[k] for k in ("G1_oos_mean_positive", "G2_stress_oos_mean_positive",
                              "G3_beats_unconditional", "G4_bootstrap_lower95_positive"))
    res["verdict"] = "INCONCLUSIVE (forward paper candidate)" if core else "FAIL"
    (HERE / "results_test12.json").write_text(json.dumps(res, indent=2, default=bool))
    cond.to_csv(HERE / "results_test12_trades.csv", index=False)
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
