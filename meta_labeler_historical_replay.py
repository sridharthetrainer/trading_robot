"""
meta_labeler_historical_replay.py — daily-bar historical replay meta-labeler
(2026-07-20, operator: "can't we use historical data for ML instead of
waiting for new signals in future").

meta_labeler.py is bounded to ~14 distinct days because signal_log is a
LIVE decision log -- it only contains what the bot actually scored in
real time, and every one of its 42 features first shows a real
(non-constant) value on 2026-06-29 or later (confirmed by direct query;
the "extra" days before that are excluded by training_eligible, not by
missing features -- there is no hidden pool of already-good historical
rows to unlock there).

The only way to get a genuinely longer training set is to REPLAY a
signal-generation rule against historical price data that already exists
independent of live signal_log. Checked what's actually available:
  - Individual equities' 5m candles only go back to ~2026-06-10 (~6
    weeks) -- not meaningfully deeper than signal_log itself.
  - Individual equities' 1d candles go back to 2022-05/06 for many
    liquid large-caps (4+ years) -- genuinely deep.
So this replay operates on DAILY bars, not intraday, and is a
deliberately different (not identical) model from meta_labeler.py:

WHAT THIS DOES NOT DO (stated up front, not discovered later):
  - Does NOT call the real signal_engine.generate_signal() / 57-strategy
    confluence engine. That function is intraday-oriented, has many
    live-only data dependencies, and running it in a historical batch
    loop would be slow and risk side effects. Signal generation here is
    a simple, disclosed, un-tuned rule (N-day breakout) -- a stand-in,
    not a replica of the live engine's score.
  - Does NOT include india_vix, fii_net_cash, fii_fut_ratio, fii_cum_5d,
    iv_percentile, pcr_atm, or any of the confluence modifiers computed
    inside generate_signal() (mtf_pivot_mod, sr_level_mod, weinstein_mod,
    etc.) -- none of those are computable from price history alone, and
    this project's own vix_history.csv/fii_history.csv are only 7-8 rows
    deep regardless of how much price history exists (a separate,
    unresolved data-source gap -- Zerodha's historical API would not
    close it, since that's OHLC price data, not VIX/FII archives).
  - Triple-barrier target/stop/max_bars below are illustrative daily-
    swing defaults, not fit to this data -- changing them after seeing
    results would be exactly the kind of parameter search this project's
    day-split discipline exists to prevent.

What it DOES give: genuinely many more distinct trading DAYS (hundreds,
not ~14) for testing whether price/calendar-derived features carry any
meta-labeling signal at all -- a different, complementary question to
meta_labeler.py's "do the live confluence modifiers combine usefully."
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from pivot_boss import calc_floor_pivots
from triple_barrier import label_triple_barrier

logger = logging.getLogger(__name__)

CANDLE_DB = "candle_cache.db"
REPORT_FILE = Path("meta_labeler_historical_replay_report.json")

MIN_DAILY_BARS = 500          # ~2+ years; keeps the universe to genuinely deep symbols
BREAKOUT_LOOKBACK = 10         # N-day high/low breakout trigger
WARMUP_BARS = 40               # bars needed before weekly/monthly pivots + breakout are valid
TARGET_PCT = 0.02              # illustrative daily-swing triple-barrier params
STOP_PCT = 0.015
MAX_BARS = 10                  # trading days
TEST_FRAC = 0.30
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70)

_FEATURES = [
    "day_of_week", "volume_ratio", "momentum_roc10", "atr_pct",
    "trend_ema_ratio", "above_weekly_pvt", "above_monthly_pvt",
    "pct_from_weekly_r1", "pct_from_monthly_r1", "side_buy",
]


def _deep_symbols(conn: sqlite3.Connection, min_bars: int = MIN_DAILY_BARS) -> List[str]:
    rows = conn.execute(
        "SELECT symbol, COUNT(*) c FROM candles WHERE interval='1d' "
        "GROUP BY symbol HAVING c >= ? ORDER BY c DESC", (min_bars,)).fetchall()
    return [r[0] for r in rows]


def _load_daily(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval='1d' ORDER BY timestamp", conn, params=(symbol,))
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True).dt.tz_localize(None)
    return df


def _weekly_monthly_pivots(df: pd.DataFrame, i: int) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Floor pivots from the PRIOR calendar week's / month's OHLC, using
    only bars strictly before index i (no lookahead)."""
    ts = df["timestamp"].iloc[i]
    hist = df.iloc[:i]
    week_start = ts - pd.Timedelta(days=ts.dayofweek + 7)
    week_end = week_start + pd.Timedelta(days=6)
    wk = hist[(hist["timestamp"] >= week_start) & (hist["timestamp"] <= week_end)]
    month_hist = hist[hist["timestamp"].dt.to_period("M") == (ts.to_period("M") - 1)]
    weekly = calc_floor_pivots(wk["high"].max(), wk["low"].min(), wk["close"].iloc[-1]) if len(wk) else {}
    monthly = calc_floor_pivots(month_hist["high"].max(), month_hist["low"].min(),
                                 month_hist["close"].iloc[-1]) if len(month_hist) else {}
    return weekly, monthly


def _build_symbol_observations(symbol: str, df: pd.DataFrame) -> List[Dict[str, Any]]:
    obs: List[Dict[str, Any]] = []
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values

    for i in range(WARMUP_BARS, len(df) - MAX_BARS):
        window_hi = highs[i - BREAKOUT_LOOKBACK:i].max()
        window_lo = lows[i - BREAKOUT_LOOKBACK:i].min()
        c = closes[i]
        side = None
        if c > window_hi:
            side = "BUY"
        elif c < window_lo:
            side = "SELL"
        if side is None:
            continue

        vol_avg20 = volumes[max(0, i - 20):i].mean()
        vol_ratio = float(volumes[i] / vol_avg20) if vol_avg20 > 0 else 1.0
        roc10 = float((c - closes[i - 10]) / closes[i - 10] * 100.0) if closes[i - 10] > 0 else 0.0
        tr = np.maximum(highs[i - 14:i + 1] - lows[i - 14:i + 1],
                        np.maximum(abs(highs[i - 14:i + 1] - closes[i - 15:i]),
                                   abs(lows[i - 14:i + 1] - closes[i - 15:i])))
        atr_pct = float(tr.mean() / c * 100.0) if c > 0 else 0.0
        ema20 = pd.Series(closes[max(0, i - 40):i + 1]).ewm(span=20).mean().iloc[-1]
        trend_ratio = float(c / ema20) if ema20 > 0 else 1.0

        weekly, monthly = _weekly_monthly_pivots(df, i)
        w_r1, m_r1 = weekly.get("R1"), monthly.get("R1")
        pct_w_r1 = float((c - w_r1) / w_r1 * 100.0) if w_r1 else 0.0
        pct_m_r1 = float((c - m_r1) / m_r1 * 100.0) if m_r1 else 0.0
        above_w = int(c > weekly.get("P", c)) if weekly else 0
        above_m = int(c > monthly.get("P", c)) if monthly else 0

        label = label_triple_barrier(
            df.iloc[:i + MAX_BARS + 1], entry_idx=i, entry_price=float(c),
            target_pct=TARGET_PCT, stop_pct=STOP_PCT, max_bars=MAX_BARS, side=side)

        obs.append({
            "symbol": symbol, "date": df["timestamp"].iloc[i].strftime("%Y-%m-%d"),
            "side_buy": int(side == "BUY"),
            "day_of_week": int(df["timestamp"].iloc[i].dayofweek),
            "volume_ratio": vol_ratio, "momentum_roc10": roc10, "atr_pct": atr_pct,
            "trend_ema_ratio": trend_ratio, "above_weekly_pvt": above_w,
            "above_monthly_pvt": above_m, "pct_from_weekly_r1": pct_w_r1,
            "pct_from_monthly_r1": pct_m_r1, "meta_label": int(label == 1),
            "tb_label": label,
        })
    return obs


def run() -> Dict[str, Any]:
    conn = sqlite3.connect(CANDLE_DB)
    symbols = _deep_symbols(conn)
    all_obs: List[Dict[str, Any]] = []
    for sym in symbols:
        df = _load_daily(conn, sym)
        if len(df) < WARMUP_BARS + MAX_BARS + 20:
            continue
        all_obs.extend(_build_symbol_observations(sym, df))
    conn.close()

    if not all_obs:
        return {"error": "no observations generated"}

    df = pd.DataFrame(all_obs).sort_values("date").reset_index(drop=True)
    dates = sorted(df["date"].unique())
    cutoff = dates[int(len(dates) * (1.0 - TEST_FRAC))]
    tr_mask = df["date"] < cutoff
    te_mask = ~tr_mask

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import roc_auc_score

    X = df[_FEATURES].fillna(0.0).astype(float).values
    y = df["meta_label"].values
    Xtr, Xte = X[tr_mask.values], X[te_mask.values]
    ytr, yte = y[tr_mask.values], y[te_mask.values]

    clf = RandomForestClassifier(n_estimators=300, max_depth=5, min_samples_leaf=50,
                                  class_weight="balanced", random_state=42, n_jobs=-1)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]
    base_rate = float(np.mean(yte))
    auc = float(roc_auc_score(yte, proba))

    by_threshold = []
    for t in THRESHOLDS:
        sel = proba >= t
        n_sel = int(sel.sum())
        prec = float(np.mean(yte[sel])) if n_sel else 0.0
        by_threshold.append({"threshold": t, "n_selected": n_sel,
                             "coverage": round(n_sel / len(yte), 4) if len(yte) else 0.0,
                             "precision": round(prec, 4), "lift_vs_base": round(prec - base_rate, 4)})
    usable = [b for b in by_threshold if b["lift_vs_base"] > 0.02 and b["coverage"] >= 0.10 and b["n_selected"] >= 20]
    best = max(usable, key=lambda b: b["lift_vs_base"]) if usable else None

    imp = sorted(zip(_FEATURES, clf.feature_importances_), key=lambda kv: -kv[1])

    report = {
        "universe_symbols": symbols, "n_symbols": len(symbols),
        "n_total": int(len(df)), "n_train": int(tr_mask.sum()), "n_test": int(te_mask.sum()),
        "distinct_days_total": len(dates), "distinct_days_train": int(df.loc[tr_mask, "date"].nunique()),
        "distinct_days_test": int(df.loc[te_mask, "date"].nunique()),
        "date_range": [dates[0], dates[-1]], "cutoff_date": cutoff,
        "base_win_rate": round(base_rate, 4), "auc": round(auc, 4),
        "by_threshold": by_threshold, "best_threshold": best,
        "top_features": [{"feature": f, "importance": round(float(v), 4)} for f, v in imp],
        "params": {"target_pct": TARGET_PCT, "stop_pct": STOP_PCT, "max_bars": MAX_BARS,
                   "breakout_lookback": BREAKOUT_LOOKBACK},
        "caveats": [
            "Daily-bar replay with a simple N-day-breakout signal rule -- NOT the live "
            "57-strategy confluence engine.",
            "Excludes VIX/FII/OI/news-derived features entirely (historical archives for "
            "those are only 7-8 rows deep regardless of price-history depth).",
            "Triple-barrier target/stop/max_bars are illustrative, not fit to this data.",
        ],
    }
    if best:
        report["conclusion"] = (
            f"AUC={auc:.3f} over {len(dates)} distinct days ({report['distinct_days_train']} "
            f"train / {report['distinct_days_test']} test, vs meta_labeler.py's ~14 total). "
            f"Best threshold {best['threshold']}: precision {base_rate:.1%}->{best['precision']:.1%} "
            f"({best['lift_vs_base']:+.1%}) at {best['coverage']:.0%} coverage. Report-only -- "
            "requires a separate holdout pass and cost-adjustment before this means anything.")
    else:
        report["conclusion"] = f"AUC={auc:.3f}, no threshold clears base rate by >2% at usable coverage."
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("report write failed: %s", exc)
    return report


def main() -> int:
    rep = run()
    if rep.get("error"):
        print(rep["error"])
        return 1
    print(f"=== HISTORICAL REPLAY META-LABELER | {rep['n_symbols']} symbols | "
          f"{rep['distinct_days_total']} distinct days ({rep['date_range'][0]}..{rep['date_range'][1]}) "
          f"| {rep['n_total']} obs ===\n")
    print(f"  train: {rep['n_train']} obs / {rep['distinct_days_train']} days")
    print(f"  test:  {rep['n_test']} obs / {rep['distinct_days_test']} days")
    print(f"  AUC: {rep['auc']}  base_win_rate: {rep['base_win_rate']}\n")
    for b in rep["by_threshold"]:
        print(f"  thr={b['threshold']}  n={b['n_selected']:>5}  cov={b['coverage']:.0%}  "
              f"prec={b['precision']:.1%}  lift={b['lift_vs_base']:+.1%}")
    print("\n  top features:")
    for f in rep["top_features"][:8]:
        print(f"    {f['feature']:<20} {f['importance']}")
    print(f"\n  {rep['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
