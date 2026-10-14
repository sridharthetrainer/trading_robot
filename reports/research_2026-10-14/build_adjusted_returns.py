"""Build split/bonus/demerger-adjusted daily returns from nse_cache.db (bhavcopy).

Survivorship-free (bhavcopy lists delisted stocks until their last trade).
Cleaning rules, fixed before the momentum test:
 1. Drop dates whose liquid-stock closes AND volumes equal the previous date
    (holiday copies, 78 found) and the corrupt file stored for 2019-09-30.
 2. NSE split/bonus factors, multiplied when several fall on one ex-date; if a
    factor disagrees with the observed ex-date move by > 25% (|log| > 0.25),
    the observed move is used instead.
 3. Demerger/arrangement ex-dates: observed move used as the factor (if < 0.97).
 4. Unrecorded split: one-day ratio <= ~0.5, within 3% of a standard ratio AND volume
    >= 3x its prior 20-day median -> treated as a split. Real crashes (e.g.
    YESBANK 0.44, DHFL 0.57) do not match and stay as losses.
 5. Returns spanning > 5 market dates (stock left the EQ series) are discarded.
Price returns only (no dividends). Output: adjusted_returns.db (not in git).
    .venv/bin/python reports/research_2026-10-14/build_adjusted_returns.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Only ratios <= 1/2: 0.8 / 0.75 / 0.67 are what 20% circuits and real crashes
# look like (Adani Jan-2023, election day 2024-06-04, COVID) -- a first run
# that allowed them "inferred" 959 splits, nearly all real losses.
CANON = np.array([1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 5])
BAD_DATES = {"2019-09-30"}
RENAMED_ON_EX_DATE = {"TMPV": "TATAMOTORS"}


def load() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{ROOT / 'nse_cache.db'}?mode=ro", uri=True)
    px = pd.read_sql("SELECT symbol, date, close, volume FROM ohlcv", con)
    con.close()
    return px


def duplicate_dates(px: pd.DataFrame) -> set[str]:
    w = px.pivot(index="date", columns="symbol", values="close").sort_index()
    v = px.pivot(index="date", columns="symbol", values="volume").sort_index()
    top = (w * v).median().rank(ascending=False)
    cols = top[top <= 300].index
    same = ((w[cols].eq(w[cols].shift()) | w[cols].isna()).all(axis=1)
            & (v[cols].eq(v[cols].shift()) | v[cols].isna()).all(axis=1))
    return set(w.index[same])


def corporate_factors() -> tuple[dict, set]:
    acts = pd.DataFrame(json.loads((HERE / "corporate_actions_2016_2026.json").read_text())["actions"])
    acts["ex"] = pd.to_datetime(acts["ex_date"], format="%d-%b-%Y").dt.strftime("%Y-%m-%d")
    acts = acts.drop_duplicates(["symbol", "ex", "subject"])
    sb = acts[(acts["kind"] == "split_bonus") & acts["factor"].notna()]
    factors = sb.groupby(["symbol", "ex"])["factor"].prod().to_dict()
    demergers = set(zip(acts.loc[acts["kind"] == "demerger", "symbol"],
                        acts.loc[acts["kind"] == "demerger", "ex"]))
    # NSE files some demergers under the post-rename symbol while that day's
    # bhavcopy row still carries the old one (Tata Motors CV demerger).
    for new, old in RENAMED_ON_EX_DATE.items():
        demergers |= {(old, ex) for sym, ex in list(demergers) if sym == new}
    return factors, demergers


def main() -> None:
    px = load()
    drop = duplicate_dates(px) | BAD_DATES
    px = px[~px["date"].isin(drop)].sort_values(["symbol", "date"]).reset_index(drop=True)
    factors, demergers = corporate_factors()

    px["ratio"] = px["close"] / px.groupby("symbol")["close"].shift()
    px["vol_med20"] = px.groupby("symbol")["volume"].transform(lambda s: s.shift().rolling(20, 10).median())
    keys = list(zip(px["symbol"], px["date"]))
    f = np.ones(len(px))
    how = np.array([""] * len(px), dtype=object)
    r = px["ratio"].to_numpy()
    for i, k in enumerate(keys):
        if not np.isfinite(r[i]):
            continue
        if k in factors:
            F = factors[k]
            f[i], how[i] = (r[i], "nse_mismatch_observed") if abs(np.log(r[i] / F)) > 0.25 else (F, "nse")
        elif k in demergers and r[i] < 0.97:
            f[i], how[i] = r[i], "demerger_observed"
    near = np.abs(r[:, None] / CANON[None, :] - 1).min(axis=1) < 0.03
    spike = px["volume"].to_numpy() >= 3 * px["vol_med20"].to_numpy()
    inferred = (how == "") & near & spike & (r < 0.55)
    best = CANON[np.abs(r[:, None] / CANON[None, :] - 1).argmin(axis=1)]
    f[inferred], how[inferred] = best[inferred], "inferred_split"

    px["factor"], px["adjustment"] = f, how
    px["ret"] = px["ratio"] / px["factor"] - 1
    # 5. A return spanning > 5 market dates is not a one-day move: the stock
    # left the EQ series (BE/BZ trade-to-trade, often distress) and came back
    # (SUZLON +126% 2021-02-02, CGPOWER +130% 2022-01-04, PCJEWELLER -89%
    # across its 2024 split). Discard it; the momentum test excludes stocks
    # with missing returns in their lookback window.
    pos = pd.Series(np.arange(px["date"].nunique()), index=sorted(px["date"].unique()))
    gap = px["date"].map(pos) - px.groupby("symbol")["date"].shift().map(pos)
    px.loc[gap > 5, ["ret", "adjustment"]] = [np.nan, "gap_discarded"]
    px["traded_value"] = px["close"] * px["volume"]
    out = px[["symbol", "date", "close", "ret", "traded_value", "factor", "adjustment"]]

    db = HERE / "adjusted_returns.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    out.to_sql("daily", con, index=False)
    con.execute("CREATE INDEX idx_daily ON daily(date, symbol)")
    con.commit()
    con.close()
    summary = {"rows": len(out), "symbols": int(out["symbol"].nunique()),
               "dates_dropped": len(drop), "start": out["date"].min(), "end": out["date"].max(),
               "adjustments": out["adjustment"].value_counts().drop("", errors="ignore").to_dict()}
    (HERE / "adjusted_returns_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
