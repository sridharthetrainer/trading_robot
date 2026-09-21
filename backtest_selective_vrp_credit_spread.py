"""
backtest_selective_vrp_credit_spread.py -- tests SELECTIVE volatility-
risk-premium capture: instead of blindly selling a bull put spread every
week (already tested, REJECTED after Bonferroni correction), does
SELLING ONLY on weeks where implied vol is high relative to recent
realized vol -- a genuinely different, cross-sectional-timing
construction, not a new structure -- show better economics?

Methodology: for each week's entry (same bull put spread as
credit_spread_backtest_real.py: otm=2%, wing=500), back out the short
leg's OWN implied vol from its real quoted premium (option_intraday_
pricer.implied_vol, Black-Scholes off the real EOD settle), and compare
it to trailing 20-day realized volatility (from candle_cache.db's real
NIFTY daily closes) as of that week. IV_RATIO = implied_vol / realized_vol.
Rank all weeks by this ratio; test SELLING ONLY the top-half (IV_RATIO
above its own trailing median, computed causally -- using only weeks
seen so far, no lookahead) against the full unconditional baseline.

This is a genuinely different hypothesis from the already-rejected blind
version: it tests WHEN to sell, not a new structure to sell.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime

import numpy as np
import pandas as pd

from condor_backtest_real import _load, _prep
from credit_spread_backtest_real import _build_credit_spread, _settle_spread
from option_intraday_pricer import implied_vol, year_fraction, RISK_FREE_RATE

OTM_PCT = 0.02
WING_PTS = 500.0
MIN_DTE = 3
BROKERAGE_LEG = 20.0
SLIPPAGE_PCT = 0.02
QTY = 75
REALIZED_VOL_LOOKBACK = 20


def _load_daily_closes(symbol: str = "NIFTY") -> pd.Series:
    con = sqlite3.connect("candle_cache.db")
    df = pd.read_sql_query(
        "SELECT timestamp, close FROM candles WHERE symbol=? AND interval='1d' ORDER BY timestamp",
        con, params=(symbol,))
    con.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.date.astype(str)
    return df.set_index("timestamp")["close"]


def run() -> None:
    opt, spot_map = _load()
    prep = _prep(opt)
    dates, groups, exp_by_date, bisect = prep

    daily_close = _load_daily_closes()
    daily_dates = sorted(daily_close.index)
    log_ret = np.log(daily_close.reindex(daily_dates)).diff()

    def realized_vol_as_of(d: str) -> float:
        """Trailing REALIZED_VOL_LOOKBACK-day annualized realized vol,
        using only days strictly before d (causal, no lookahead)."""
        prior_dates = [x for x in daily_dates if x < d]
        if len(prior_dates) < REALIZED_VOL_LOOKBACK + 1:
            return float("nan")
        window = prior_dates[-REALIZED_VOL_LOOKBACK:]
        rets = log_ret.reindex(window).dropna()
        if len(rets) < REALIZED_VOL_LOOKBACK // 2:
            return float("nan")
        return float(rets.std() * math.sqrt(252))

    weeks = []
    seen_weeks = set()
    for d in dates:
        wk = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if wk in seen_weeks or d not in spot_map:
            continue
        target_exp = None
        di = bisect.bisect_right(dates, d)
        for e in exp_by_date.get(d, ()):
            if e <= d:
                continue
            if bisect.bisect_right(dates, e) - di >= MIN_DTE:
                target_exp = e
                break
        if target_exp is None or target_exp not in spot_map:
            continue
        c = _build_credit_spread(groups.get((d, target_exp)), spot_map[d], OTM_PCT, WING_PTS, "put")
        if c is None:
            continue
        gross = _settle_spread(c, spot_map[target_exp])
        slip = SLIPPAGE_PCT * c["legs_premium"]
        net = gross - slip - (BROKERAGE_LEG * 2) / QTY
        net_rs = net * QTY

        # back out the SHORT leg's own implied vol from its real quoted premium
        short_strike = c["short"]
        row = groups.get((d, target_exp))
        short_px = None
        if row is not None:
            match = row[(row["strike"] == short_strike) & (row["opt_type"] == "PE")]
            if len(match):
                px = float(match["settle"].iloc[0])
                short_px = px if px > 0 else float(match["close"].iloc[0])
        T = year_fraction(datetime.combine(date.fromisoformat(d), datetime.min.time()), date.fromisoformat(target_exp))
        iv = None
        if short_px and short_px > 0 and T > 0:
            iv = implied_vol(short_px, spot_map[d], short_strike, T, RISK_FREE_RATE, "PE")
        rv = realized_vol_as_of(d)

        weeks.append({"date": d, "net_rs": net_rs, "iv": iv, "rv": rv,
                      "iv_ratio": (iv / rv) if (iv and rv and rv > 0) else None})
        seen_weeks.add(wk)

    df = pd.DataFrame(weeks).sort_values("date").reset_index(drop=True)
    valid = df.dropna(subset=["iv_ratio"]).reset_index(drop=True)
    print(f"Total weeks: {len(df)}  With valid causal IV/RV ratio: {len(valid)}")

    # causal median: at each week, use only the median of iv_ratio from
    # PRIOR weeks (no lookahead) to decide "high" vs "low" for THIS week
    selected = []
    unselected = []
    for i in range(len(valid)):
        prior = valid["iv_ratio"].iloc[:i]
        if len(prior) < 20:
            continue
        thresh = prior.median()
        row = valid.iloc[i]
        (selected if row["iv_ratio"] > thresh else unselected).append(row["net_rs"])

    def stat(x):
        x = np.array(x)
        n = len(x)
        if n < 5:
            return {"n": n}
        mean = x.mean()
        se = x.std(ddof=1) / math.sqrt(n)
        t = mean / se if se > 0 else 0
        p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
        return {"n": n, "mean_rs": round(mean, 1), "total_rs": round(x.sum(), 0),
                "t": round(t, 3), "p": round(p, 5), "lcb95": round(mean - 1.645 * se, 1)}

    print(f"\nSELECTED (IV/RV above trailing causal median -- 'sell selectively'): {stat(selected)}")
    print(f"UNSELECTED (IV/RV below median -- 'skip these weeks'): {stat(unselected)}")

    n = len(selected) + len(unselected)
    mid = n // 2
    combo = [("selected", v) for v in selected] + [("unselected", v) for v in unselected]
    # re-derive in original chronological order for day-split
    chrono = []
    idx = 0
    for i in range(len(valid)):
        prior = valid["iv_ratio"].iloc[:i]
        if len(prior) < 20:
            continue
        thresh = prior.median()
        row = valid.iloc[i]
        if row["iv_ratio"] > thresh:
            chrono.append(("selected", row["net_rs"]))
        else:
            chrono.append(("unselected", row["net_rs"]))
    sel_chrono = [v for label, v in chrono if label == "selected"]
    mid_s = len(sel_chrono) // 2
    print(f"\nDay-split on SELECTED subset:")
    print(f"  OLDER: {stat(sel_chrono[:mid_s])}")
    print(f"  NEWER: {stat(sel_chrono[mid_s:])}")


if __name__ == "__main__":
    run()
