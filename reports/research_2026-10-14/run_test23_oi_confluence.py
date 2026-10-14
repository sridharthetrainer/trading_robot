"""Test 23 in PREREGISTRATION.md: does BANKNIFTY / NIFTY option-chain bias (and their
agreement) predict NIFTY over the next 30 minutes? Read-only research.
    .venv/bin/python reports/research_2026-10-14/run_test23_oi_confluence.py
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
BIAS = {"BULLISH": 1.0, "BEARISH": -1.0}


def snaps(underlying):
    con = sqlite3.connect(f"file:{ROOT / 'option_chain_snapshots.db'}?mode=ro", uri=True)
    rows = con.execute("SELECT snapshot_time, summary_json FROM option_chain_snapshots WHERE ok=1 AND underlying=?",
                       (underlying,)).fetchall()
    con.close()
    out = [(pd.Timestamp(t[:19]), BIAS.get(json.loads(s).get("net_bias"), 0.0)) for t, s in rows]
    return pd.DataFrame(out, columns=["ts", "bias"]).sort_values("ts")


def main():
    con = sqlite3.connect(f"file:{ROOT / 'candle_cache.db'}?mode=ro", uri=True)
    c = pd.read_sql("SELECT timestamp, close FROM candles WHERE symbol='NIFTY' AND interval='5m' "
                    "AND timestamp >= '2026-06-18'", con)
    con.close()
    c["ts"] = pd.to_datetime(c["timestamp"].str[:19])
    c = c.sort_values("ts").reset_index(drop=True)
    c["day"] = c["ts"].dt.date
    c["fwd"] = np.log(c.groupby("day")["close"].shift(-6) / c["close"])
    bn, nf = snaps("BANKNIFTY"), snaps("NIFTY")
    bn = pd.merge_asof(bn, nf.rename(columns={"bias": "nf_bias"}), on="ts", direction="nearest",
                       tolerance=pd.Timedelta("5min"))
    bn["bar"] = bn["ts"].dt.floor("5min")
    df = bn.merge(c[["ts", "fwd", "day"]].rename(columns={"ts": "bar"}), on="bar", how="inner").dropna(subset=["fwd"])
    signals = {"banknifty_bias": df["bias"],
               "nifty_bias": df["nf_bias"].fillna(0.0),
               "confluence_both_agree": np.where((df["bias"] != 0) & (df["bias"] == df["nf_bias"]), df["bias"], 0.0)}
    days = sorted(df["day"].unique())
    cut = days[int(len(days) * 0.7)]
    res = {"days": len(days), "snapshots": int(len(df))}
    for name, sig in signals.items():
        d = df.assign(s=np.asarray(sig, dtype=float))
        d = d[d["s"] != 0]
        d["signed"] = d["s"] * d["fwd"]
        per_day = d.groupby("day")["signed"].mean()
        t = float(per_day.mean() / (per_day.std(ddof=1) / math.sqrt(len(per_day)))) if len(per_day) > 1 else 0.0
        hold = d[d["day"] >= cut]["signed"]
        r = {"signals": int(len(d)), "pct_bullish": round(float((d["s"] > 0).mean()), 3),
             "mean_signed_pct": round(float(d["signed"].mean()) * 100, 4), "day_clustered_t": round(t, 2),
             "holdout_mean_pct": round(float(hold.mean()) * 100, 4) if len(hold) else None}
        r["verdict"] = "PASS" if (t > 2.4 and (r["holdout_mean_pct"] or -1) > 0 and r["mean_signed_pct"] > 0.03) else "FAIL"
        res[name] = r
    (HERE / "results_test23.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
