"""
chartink_screen_backtest.py -- backtests well-known, widely-shared public
Chartink screener strategies as multi-day NIFTY200 equity swing trades.

Chartink itself is a screener, not a strategy with a built-in exit -- the
scan just flags stocks matching a technical condition each day; what a
trader does after that is discretionary. This backtest picks ONE concrete
holding period (10 trading days, ~2 calendar weeks, a common retail
swing-trade horizon) as its primary test, and reports 5/20 days only as
exploratory context -- documented here as this project's OWN modeling
choice, not something scraped from an authoritative source.

DATA-DEPTH CAVEAT (same one cross_sectional_factor_test.py already
documents): common daily history across the ~190-stock universe in
candle_cache.db is only ~1 year (313 bars for RELIANCE, checked
2026-09-20), so a literal "52-week high" screen has only ~60 trading days
of live signal-generation window after its own 252-day warmup. Small
sample, reported honestly, not padded.

DRIFT-CONFOUND DISCIPLINE (learned the hard way testing intraday option
strategies the same day -- see RESEARCH_QUEUE_2026-09-13.md, "TradingView
built-in strategies" section, where 4/5 indicators looked falsely
profitable purely from captured index drift): every screen's forward
return here is measured as EXCESS return over the equal-weight universe's
return across the exact same window, not raw return. Applied from the
start this time, not bolted on after a suspicious result.

Screens implemented (four of Chartink's most widely shared public scans):
  1. sma200_breakout     -- close crosses above SMA(200).
  2. high52w_volume      -- close makes a new 252-day high (using only
                             PRIOR bars, not including today -- a
                             same-window self-referential high would make
                             "today breaks its own rolling max" circular,
                             the same class of lookahead bug already found
                             and fixed once this session in
                             backtest_supertrend_mtf.py) AND volume >=
                             1.5x its own trailing 20-day average.
  3. supertrend_flip     -- Supertrend(10,3) direction flips from -1 to +1
                             (reuses indicators.py's existing, unmodified
                             calculate_supertrend).
  4. rsi_oversold_bounce -- RSI(14) crosses above 30 from below (reuses
                             indicators.py's existing calculate_rsi).

Methodology: day-split (not row-split) train/holdout at 70/30, same
discipline as eod_setup_edge_analyzer.py; one-sided LCB95
(mean - 1.645*SE) on after-cost excess return; Bonferroni across the 4
screens tested. Real delivery-equity cost (ROUND_TRIP_COST_PCT = 0.22%,
same estimate cross_sectional_factor_test.py uses -- STT both sides +
exchange/GST/stamp allowance), applied once per round trip.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from indicators import calculate_rsi, calculate_supertrend

CANDLE_DB = "candle_cache.db"
HOLD_DAYS_PRIMARY = 10
SMA200_PERIOD = 200
HIGH52W_LOOKBACK = 252
VOL_AVG_PERIOD = 20
VOL_MULTIPLIER = 1.5
SUPERTREND_PERIOD = 10
SUPERTREND_MULT = 3.0
RSI_PERIOD = 14
RSI_OVERSOLD = 30.0
ROUND_TRIP_COST_PCT = 0.0022   # matches cross_sectional_factor_test.py
TRAIN_FRAC = 0.70
ALPHA = 0.05
MIN_HISTORY = max(HIGH52W_LOOKBACK, SMA200_PERIOD) + 30


def _load_universe() -> Dict[str, pd.DataFrame]:
    with sqlite3.connect(CANDLE_DB) as conn:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM candles WHERE interval='1d'")]
        out: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            if sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"):
                continue
            rows = conn.execute(
                "SELECT timestamp, open, high, low, close, volume FROM candles "
                "WHERE symbol=? AND interval='1d' ORDER BY timestamp", (sym,)).fetchall()
            if len(rows) < MIN_HISTORY:
                continue
            idx = pd.to_datetime([str(r[0])[:10] for r in rows])
            df = pd.DataFrame(
                {"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                 "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                 "volume": [r[5] for r in rows]}, index=idx)
            out[sym] = df
    return out


def _screen_sma200_breakout(df: pd.DataFrame) -> pd.Series:
    sma = df["close"].rolling(SMA200_PERIOD).mean()
    return (df["close"] > sma) & (df["close"].shift(1) <= sma.shift(1))


def _screen_high52w_volume(df: pd.DataFrame) -> pd.Series:
    prior_high = df["close"].shift(1).rolling(HIGH52W_LOOKBACK).max()
    vol_avg = df["volume"].shift(1).rolling(VOL_AVG_PERIOD).mean()
    return (df["close"] > prior_high) & (df["volume"] >= VOL_MULTIPLIER * vol_avg)


def _screen_supertrend_flip(df: pd.DataFrame) -> pd.Series:
    _, direction = calculate_supertrend(df, period=SUPERTREND_PERIOD, multiplier=SUPERTREND_MULT)
    return (direction == 1) & (direction.shift(1) == -1)


def _screen_rsi_oversold_bounce(df: pd.DataFrame) -> pd.Series:
    rsi = calculate_rsi(df["close"], period=RSI_PERIOD)
    return (rsi > RSI_OVERSOLD) & (rsi.shift(1) <= RSI_OVERSOLD)


SCREENS: Dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "sma200_breakout": _screen_sma200_breakout,
    "high52w_volume": _screen_high52w_volume,
    "supertrend_flip": _screen_supertrend_flip,
    "rsi_oversold_bounce": _screen_rsi_oversold_bounce,
}


def _forward_return(df: pd.DataFrame, entry_i: int, hold_days: int) -> Optional[float]:
    exit_i = entry_i + hold_days
    if exit_i >= len(df):
        return None
    entry_px = float(df["close"].iloc[entry_i])
    exit_px = float(df["close"].iloc[exit_i])
    if entry_px <= 0:
        return None
    return (exit_px - entry_px) / entry_px * 100.0


def _stat(rets: List[float]) -> Dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0}
    mean = sum(rets) / n
    sd = 0.0
    if n > 1:
        var = sum((x - mean) ** 2 for x in rets) / (n - 1)
        sd = math.sqrt(var)
    se = sd / math.sqrt(n) if sd > 0 else 0.0
    t = mean / se if se > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    win = sum(1 for x in rets if x > 0) / n
    return {"n": n, "mean_excess_pct": round(mean, 4),
            "lcb95_pct": round(mean - 1.645 * se, 4),
            "win_rate": round(win, 3), "t": round(t, 2), "p": round(p, 5)}


def run_screen(name: str, hold_days: int = HOLD_DAYS_PRIMARY,
               universe: Optional[Dict[str, pd.DataFrame]] = None) -> Dict[str, Any]:
    if universe is None:
        universe = _load_universe()
    if len(universe) < 20:
        return {"error": f"only {len(universe)} symbols with enough common history"}
    screen_fn = SCREENS[name]

    hits: List[Dict[str, Any]] = []
    for sym, df in universe.items():
        flags = screen_fn(df)
        positions = np.flatnonzero(flags.fillna(False).values)
        for i in positions:
            hits.append({"sym": sym, "date": df.index[i], "entry_i": int(i)})

    if not hits:
        return {"error": "zero screen hits across the whole universe"}

    hits.sort(key=lambda h: h["date"])
    cut_idx = max(1, int(len(hits) * TRAIN_FRAC) - 1)
    cutoff_date = hits[cut_idx]["date"]

    train_excess, holdout_excess = [], []
    for h in hits:
        sym, hit_date, entry_i = h["sym"], h["date"], h["entry_i"]
        df = universe[sym]
        raw_ret = _forward_return(df, entry_i, hold_days)
        if raw_ret is None:
            continue
        bench_rets = []
        for other_sym, other_df in universe.items():
            pos = other_df.index.searchsorted(hit_date)
            if pos >= len(other_df) or other_df.index[pos] != hit_date:
                continue
            r = _forward_return(other_df, pos, hold_days)
            if r is not None:
                bench_rets.append(r)
        if not bench_rets:
            continue
        benchmark_ret = float(np.mean(bench_rets))
        excess = (raw_ret - benchmark_ret) - ROUND_TRIP_COST_PCT * 100
        (train_excess if hit_date <= cutoff_date else holdout_excess).append(excess)

    return {
        "screen": name, "hold_days": hold_days,
        "universe_size": len(universe), "total_hits": len(hits),
        "cutoff_date": str(pd.Timestamp(cutoff_date).date()),
        "train": _stat(train_excess), "holdout": _stat(holdout_excess),
    }


def _verdict(train: Dict[str, Any], holdout: Dict[str, Any], bonferroni: int) -> str:
    if train.get("n", 0) < 20:
        return "INSUFFICIENT_DATA"
    sig = train["p"] * bonferroni < ALPHA
    held = (holdout.get("n", 0) >= 10
            and holdout.get("mean_excess_pct", 0) > 0
            and holdout.get("lcb95_pct", float("-inf")) > 0)
    if sig and train["mean_excess_pct"] > 0 and held:
        return "CANDIDATE"
    if sig and train["mean_excess_pct"] > 0:
        return "TRAIN_ONLY_OVERFIT"
    if sig and train["mean_excess_pct"] < 0:
        return "HURTS"
    return "NOISE"


def main() -> int:
    import json
    from datetime import datetime
    from pathlib import Path

    universe = _load_universe()
    bonferroni = len(SCREENS)
    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "hold_days_primary": HOLD_DAYS_PRIMARY, "bonferroni_tests": bonferroni,
        "universe_size": len(universe), "screens": {},
    }
    print(f"=== CHARTINK SCREEN BACKTEST (hold={HOLD_DAYS_PRIMARY}d, "
          f"excess vs equal-weight universe, universe={len(universe)} symbols) ===\n")
    for name in SCREENS:
        rep = run_screen(name, HOLD_DAYS_PRIMARY, universe=universe)
        if rep.get("error"):
            print(f"{name}: {rep['error']}\n")
            report["screens"][name] = {"error": rep["error"]}
            continue
        verdict = _verdict(rep["train"], rep["holdout"], bonferroni)
        print(f"--- {name} | hits={rep['total_hits']} | cutoff={rep['cutoff_date']} ---")
        print(f"  verdict={verdict}")
        print(f"  train   n={rep['train'].get('n')} excess={rep['train'].get('mean_excess_pct')}% "
              f"lcb95={rep['train'].get('lcb95_pct')}% t={rep['train'].get('t')} p={rep['train'].get('p')}")
        print(f"  holdout n={rep['holdout'].get('n')} excess={rep['holdout'].get('mean_excess_pct')}% "
              f"lcb95={rep['holdout'].get('lcb95_pct')}%")
        print()
        report["screens"][name] = {**rep, "verdict": verdict}

    try:
        Path("chartink_screen_report.json").write_text(json.dumps(report, indent=2, default=str))
    except Exception as exc:
        print(f"report write failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
