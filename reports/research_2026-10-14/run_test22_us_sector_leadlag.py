"""Test 22 in PREREGISTRATION.md: does a US sector's overnight move carry into the
matching Indian sector after the open? Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test22_us_sector_leadlag.py
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPLIT, THRESH, COST = "2022-01-01", 0.01, 0.001
SECTORS = {
    "IT": ("XLK", "TCS INFY WIPRO HCLTECH TECHM"),
    "Banks": ("XLF", "HDFCBANK ICICIBANK KOTAKBANK AXISBANK SBIN"),
    "Energy": ("XLE", "RELIANCE ONGC BPCL IOC GAIL"),
    "Pharma": ("XLV", "SUNPHARMA DRREDDY CIPLA DIVISLAB LUPIN"),
    "FMCG": ("XLP", "HINDUNILVR ITC NESTLEIND BRITANNIA DABUR"),
    "Metals": ("XLB", "TATASTEEL HINDALCO JSWSTEEL VEDL SAIL"),
    "Auto": ("XLY", "MARUTI M&M BAJAJ-AUTO HEROMOTOCO EICHERMOT"),
    "Realty": ("XLRE", "DLF GODREJPROP OBEROIRLTY PRESTIGE PHOENIXLTD"),
    "CapGoods": ("XLI", "LT SIEMENS ABB BHEL CUMMINSIND"),
    "Market": ("SPY", "NIFTYBEES"),
}


def india(symbols):
    con = sqlite3.connect(f"file:{ROOT / 'nse_cache.db'}?mode=ro", uri=True)
    q = "SELECT symbol, date, open, close FROM ohlcv WHERE symbol IN (%s)" % ",".join("?" * len(symbols))
    px = pd.read_sql(q, con, params=symbols)
    con.close()
    adj = sqlite3.connect(f"file:{HERE / 'adjusted_returns.db'}?mode=ro", uri=True)
    flags = pd.read_sql("SELECT symbol, date, adjustment FROM daily WHERE symbol IN (%s) AND adjustment != ''"
                        % ",".join("?" * len(symbols)), adj, params=symbols)
    dates = pd.read_sql("SELECT DISTINCT date FROM daily", adj)["date"]
    adj.close()
    px = px[px["date"].isin(set(dates))].sort_values(["symbol", "date"])   # cleaned calendar only
    px["prev_close"] = px.groupby("symbol")["close"].shift()
    px = px.merge(flags, on=["symbol", "date"], how="left")
    px = px[px["adjustment"].isna()]
    px["gap"] = px["open"] / px["prev_close"] - 1
    px["oc"] = px["close"] / px["open"] - 1
    px = px[(px["open"] > 0) & (px["gap"].abs() < 0.2)]
    return px.groupby("date")[["gap", "oc"]].mean()


def t_of(x):
    x = np.asarray(x)
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 1 and x.std(ddof=1) > 0 else 0.0


def main():
    us = pd.read_csv(HERE / "us_sector_closes_2016_2026.csv", index_col=0, parse_dates=True)
    us_ret = us.pct_change()
    res = {}
    for name, (etf, syms) in SECTORS.items():
        ind = india(syms.split())
        ind.index = pd.to_datetime(ind.index)
        prior = us_ret[etf].dropna()
        # US session that closed before Indian day D: latest US date strictly before D
        pos = prior.index.searchsorted(ind.index, side="left") - 1
        ok = pos >= 0
        ind = ind[ok].copy()
        ind["us"] = prior.to_numpy()[pos[ok]]
        corr_gap = float(ind["us"].corr(ind["gap"]))
        corr_oc = float(ind["us"].corr(ind["oc"]))
        big = ind[ind["us"].abs() >= THRESH]
        follow = np.sign(big["us"]) * big["oc"]
        ins, oos = follow[big.index < SPLIT], follow[big.index >= SPLIT]
        direction = 1.0 if ins.mean() > 0 else -1.0
        oos_net = direction * oos - COST
        r = {"days": int(len(ind)), "corr_us_vs_gap": round(corr_gap, 3), "corr_us_vs_open_to_close": round(corr_oc, 3),
             "big_us_days": int(len(big)), "direction": "follow" if direction > 0 else "fade",
             "in_sample_t": round(t_of(direction * ins), 2), "in_sample_mean_pct": round(float(direction * ins.mean()) * 100, 3),
             "oos_trades": int(len(oos)), "oos_mean_net_pct": round(float(oos_net.mean()) * 100, 3), "oos_t": round(t_of(oos_net), 2)}
        r["verdict"] = "PASS" if (r["in_sample_t"] > 2 and r["oos_trades"] >= 40 and r["oos_mean_net_pct"] > 0
                                  and r["oos_t"] > 2.8) else "FAIL"
        res[name] = r
        print(f"{name:9s} {etf:5s} corr(gap)={corr_gap:+.2f} corr(open->close)={corr_oc:+.3f} big={len(big):4d} "
              f"{r['direction']:6s} IS t={r['in_sample_t']:+.2f}  OOS n={r['oos_trades']} net={r['oos_mean_net_pct']:+.3f}% "
              f"t={r['oos_t']:+.2f}  {r['verdict']}", flush=True)
    (HERE / "results_test22.json").write_text(json.dumps(res, indent=2))
    print("PASSED:", [k for k, v in res.items() if v["verdict"] == "PASS"])


if __name__ == "__main__":
    main()
