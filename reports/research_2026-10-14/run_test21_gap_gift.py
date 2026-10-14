"""Test 21 in PREREGISTRATION.md: does NIFTY follow or fade a >= 0.5% opening gap
(the move GIFT Nifty signals pre-open)? Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test21_gap_gift.py
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
MIN_GAP, COST = 0.005, 0.0006


def days_from_bars(df, entry_t, exit_t):
    df = df.copy()
    df["day"] = df["ts"].dt.date
    df["hm"] = df["ts"].dt.strftime("%H:%M")
    out = []
    for day, g in df.groupby("day"):
        g = g.sort_values("ts")
        o = g[g["hm"] == "09:15"]
        e = g[g["hm"] == entry_t]
        x = g[g["hm"] <= exit_t]
        if o.empty or e.empty or x.empty:
            continue
        out.append({"day": day, "open": float(o["open"].iloc[0]), "entry": float(e["close"].iloc[0]),
                    "exit": float(x["close"].iloc[-1]), "last": float(g["close"].iloc[-1])})
    d = pd.DataFrame(out).sort_values("day").reset_index(drop=True)
    d["prev_close"] = d["last"].shift()
    d["gap"] = d["open"] / d["prev_close"] - 1
    d["follow_gross"] = np.sign(d["gap"]) * (d["exit"] / d["entry"] - 1)
    return d.dropna()


def load():
    m = pd.read_csv(ROOT / "external_data" / "nse_index_upto_2024" / "NIFTY_50.csv", parse_dates=["date"])
    m = m.rename(columns={"date": "ts"})
    a = days_from_bars(m, "09:19", "15:24")            # 1-min: 09:19 bar closes at 09:20
    con = sqlite3.connect(f"file:{ROOT / 'candle_cache.db'}?mode=ro", uri=True)
    f = pd.read_sql("SELECT timestamp, open, high, low, close FROM candles WHERE symbol='NIFTY' AND interval='5m'", con)
    con.close()
    f["ts"] = pd.to_datetime(f["timestamp"].str[:19])
    b = days_from_bars(f, "09:15", "15:20")            # 5-min: 09:15 bar closes 09:20; 15:20 bar closes 15:25
    return a, b


def summ(x):
    x = np.asarray(x)
    if len(x) < 2:
        return {"n": int(len(x))}
    return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 4),
            "t": round(float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))), 2),
            "win_rate": round(float((x > 0).mean()), 3)}


def main():
    a, b = load()
    a = a[a["gap"].abs() >= MIN_GAP]
    b = b[b["gap"].abs() >= MIN_GAP]
    ins = a[a["day"] < pd.Timestamp("2022-01-01").date()]
    oos = a[a["day"] >= pd.Timestamp("2022-01-01").date()]
    direction = 1.0 if ins["follow_gross"].mean() > 0 else -1.0     # +1 follow, -1 fade
    net = lambda df: direction * df["follow_gross"] - COST
    res = {"direction_chosen_in_sample": "follow" if direction > 0 else "fade",
           "in_sample_2015_2021_gross": summ(direction * ins["follow_gross"]),
           "oos_2022_2024_net": summ(net(oos)), "recent_2025_2026_net": summ(net(b)),
           "gap_days": {"in_sample": int(len(ins)), "oos": int(len(oos)), "recent": int(len(b))}}
    i, o, r = res["in_sample_2015_2021_gross"], res["oos_2022_2024_net"], res["recent_2025_2026_net"]
    checks = {"in_sample_direction_t_gt_2": i.get("t", 0) > 2,
              "oos_mean_pos_t_gt_2_n_ge_60": o.get("n", 0) >= 60 and o.get("mean_pct", -1) > 0 and o.get("t", 0) > 2,
              "recent_mean_pos": r.get("mean_pct", -1) > 0}
    res["checks"], res["verdict"] = checks, "PASS" if all(checks.values()) else "FAIL"
    (HERE / "results_test21.json").write_text(json.dumps(res, indent=2, default=bool))
    print(json.dumps(res, indent=2, default=bool))


if __name__ == "__main__":
    main()
