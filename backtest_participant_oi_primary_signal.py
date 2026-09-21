"""
backtest_participant_oi_primary_signal.py -- tests FII/PRO participant-OI
positioning as a PRIMARY, standalone predictor of NIFTY forward returns,
not as a minor confluence-score modifier (the only way this data has
ever been tested in this project before -- and the modifier-pruning
analysis found the closest existing modifier, participant_mod, was
never properly isolated this way).

Data: participant_oi.db (8,285 rows, 2020-01-01 to 2026-09-18, FII/DII/
Pro/Client/TOTAL positioning across index+stock futures and options;
paired with a real nifty_daily close series in the same DB, 1,661 rows,
same date range).

PRE-SPECIFIED hypotheses (fixed BEFORE looking at any result, exactly 3
metrics x 2 forward horizons = 6 tests, Bonferroni-corrected across all
6 -- no additional metrics or horizons added after seeing results, per
this project's standing discipline against combinatorial fishing):

1. FII_NET_FUT_RATIO = (future_index_long - future_index_short) /
   (future_index_long + future_index_short) -- normalized FII index-
   futures directional bias, as of day T.
2. FII_NET_OPT_RATIO = ((call_long-call_short) - (put_long-put_short)) /
   total options activity -- normalized FII index-options directional
   bias (net call exposure minus net put exposure).
3. PRO_NET_FUT_RATIO = same formula as #1, for client_type='Pro' (the
   OTHER professional/institutional positioning category, motivated by
   the SEBI-cited finding that Pro/FPI algorithmic participants capture
   most of the profits retail loses).

Forward horizons: next trading day (T+1) and next trading week (T+5),
using REAL nifty_daily closes, log returns.

Day-split 70/30 train/holdout (chronological), Pearson correlation +
Welch-style t-test on each (metric, horizon) pair, Bonferroni-corrected
alpha = 0.05/6 = 0.00833.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List

import numpy as np
import pandas as pd

DB = "participant_oi.db"
HORIZONS = {"T+1": 1, "T+5": 5}
TRAIN_FRAC = 0.70
N_TESTS = 6
ALPHA_CORRECTED = 0.05 / N_TESTS


def _load() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    part = pd.read_sql_query(
        "SELECT date, client_type, future_index_long, future_index_short, "
        "opt_index_call_long, opt_index_call_short, opt_index_put_long, opt_index_put_short "
        "FROM participant_oi WHERE client_type IN ('FII','Pro') ORDER BY date", con)
    nifty = pd.read_sql_query("SELECT date, close FROM nifty_daily ORDER BY date", con)
    con.close()
    return part, nifty


def _build_metrics(part: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for client in ("FII", "Pro"):
        sub = part[part["client_type"] == client].set_index("date")
        fut_total = sub["future_index_long"] + sub["future_index_short"]
        fut_ratio = (sub["future_index_long"] - sub["future_index_short"]) / fut_total.replace(0, np.nan)
        opt_total = (sub["opt_index_call_long"] + sub["opt_index_call_short"] +
                     sub["opt_index_put_long"] + sub["opt_index_put_short"])
        opt_net = ((sub["opt_index_call_long"] - sub["opt_index_call_short"]) -
                   (sub["opt_index_put_long"] - sub["opt_index_put_short"]))
        opt_ratio = opt_net / opt_total.replace(0, np.nan)
        rows.append(pd.DataFrame({f"{client}_NET_FUT_RATIO": fut_ratio,
                                   f"{client}_NET_OPT_RATIO": opt_ratio}))
    merged = rows[0].join(rows[1], how="outer")
    return merged


def _stat(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    n = len(x)
    if n < 10:
        return {"n": n}
    r = float(np.corrcoef(x, y)[0, 1])
    # t-test for correlation significance: t = r*sqrt(n-2)/sqrt(1-r^2)
    if abs(r) >= 1.0:
        t = float("inf")
    else:
        t = r * math.sqrt(n - 2) / math.sqrt(1 - r ** 2)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return {"n": n, "corr": round(r, 4), "t": round(t, 3), "p": round(p, 6)}


def run() -> None:
    part, nifty = _load()
    metrics = _build_metrics(part)
    nifty = nifty.set_index("date").sort_index()
    nifty["log_close"] = np.log(nifty["close"])

    all_dates = sorted(set(metrics.index) & set(nifty.index))
    metrics = metrics.reindex(all_dates)
    nifty_dates = nifty.index.tolist()

    metric_names = ["FII_NET_FUT_RATIO", "FII_NET_OPT_RATIO", "Pro_NET_FUT_RATIO"]
    # Note: build_metrics names Pro columns "Pro_NET_..." (client label as-is)
    results = []
    for metric_name in metric_names:
        if metric_name not in metrics.columns:
            print(f"{metric_name}: column not found, skipping")
            continue
        for h_name, h_days in HORIZONS.items():
            xs, ys, ds = [], [], []
            for d in all_dates:
                if d not in nifty.index:
                    continue
                idx = nifty_dates.index(d)
                fwd_idx = idx + h_days
                if fwd_idx >= len(nifty_dates):
                    continue
                x = metrics.loc[d, metric_name]
                if pd.isna(x):
                    continue
                fwd_ret = nifty["log_close"].iloc[fwd_idx] - nifty["log_close"].iloc[idx]
                xs.append(x)
                ys.append(fwd_ret)
                ds.append(d)
            if len(xs) < 30:
                print(f"{metric_name} / {h_name}: insufficient ({len(xs)})")
                continue
            xs, ys = np.array(xs), np.array(ys)
            cut = int(len(xs) * TRAIN_FRAC)
            train_stat = _stat(xs[:cut], ys[:cut])
            holdout_stat = _stat(xs[cut:], ys[cut:])
            sig_train = train_stat.get("p", 1) < ALPHA_CORRECTED
            print(f"{metric_name:<22} {h_name}: n={len(xs)} "
                  f"TRAIN(n={train_stat.get('n')} corr={train_stat.get('corr')} p={train_stat.get('p')}) "
                  f"HOLDOUT(n={holdout_stat.get('n')} corr={holdout_stat.get('corr')} p={holdout_stat.get('p')}) "
                  f"{'[TRAIN SIG]' if sig_train else ''}")
            results.append({"metric": metric_name, "horizon": h_name,
                             "train": train_stat, "holdout": holdout_stat})

    print(f"\nBonferroni-corrected alpha ({N_TESTS} pre-specified tests): {ALPHA_CORRECTED:.5f}")


if __name__ == "__main__":
    run()
