#!/usr/bin/env python3
"""
backtest_macro_sentiment.py — validate whether the macro/global sentiment from
macro_global_profit_engine actually predicts NIFTY (audit deliverable).

Honest + data-gated: it joins the daily `macro_global_sentiment` snapshots with
NIFTY daily closes and measures predictiveness of the NEXT-day move (close→close,
entry_lag=1, no lookahead — macro is logged EOD, traded next day). Until enough
distinct snapshot days accrue (MIN_DAYS), it refuses to report a verdict (the
nightly pipeline logs one snapshot/day via macro_global_profit_engine.log_sentiment).

It makes NO edge claim — it MEASURES. Given the exhausted edge search, expect a
null result; the point is to confirm/deny with evidence before anyone wires macro
sentiment into live trade selection.

Usage:
    python backtest_macro_sentiment.py            # report (or INSUFFICIENT_DATA)
    python backtest_macro_sentiment.py --min-days 20
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from typing import Dict, List, Tuple

MACRO_DB = "signal_log.db"
NIFTY_DB = "participant_oi.db"   # nifty_daily(date, close)
MIN_DAYS = 20


def load_macro(db_path: str = MACRO_DB) -> List[Tuple[str, float, str, float]]:
    """Returns [(date, global_score, bias, gift_change_pct), ...]. Empty if table
    absent (table is created lazily by macro_global_profit_engine.log_sentiment)."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT substr(timestamp,1,10) d, global_score, bias, gift_change_pct "
                "FROM macro_global_sentiment GROUP BY d ORDER BY d"
            ).fetchall()
        finally:
            conn.close()
        return [(r[0], float(r[1] or 0), str(r[2] or ""), float(r[3] or 0)) for r in rows]
    except Exception:
        return []


def load_nifty_close(db_path: str = NIFTY_DB) -> Dict[str, float]:
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT date, close FROM nifty_daily WHERE close>0 ORDER BY date"
            ).fetchall()
        finally:
            conn.close()
        return {str(d): float(c) for d, c in rows}
    except Exception:
        return {}


def _spearman(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx > 0 and vy > 0 else 0.0


def run(min_days: int = MIN_DAYS) -> Dict[str, object]:
    macro = load_macro()
    closes = load_nifty_close()
    if not macro:
        return {"status": "INSUFFICIENT_DATA",
                "reason": "no macro_global_sentiment snapshots yet "
                          "(nightly pipeline logs one/day)", "days": 0}
    if not closes:
        return {"status": "ERROR", "reason": "no NIFTY daily closes found"}

    dates = sorted(closes)
    nxt = {dates[i]: dates[i + 1] for i in range(len(dates) - 1)}  # next trading day

    scores: List[float] = []
    gifts:  List[float] = []
    rets:   List[float] = []
    bias_hits: Dict[str, List[int]] = {}
    for d, score, bias, gift in macro:
        nd = nxt.get(d)
        if nd is None or d not in closes:
            continue
        r = closes[nd] / closes[d] - 1.0
        scores.append(score); gifts.append(gift); rets.append(r)
        if bias in ("BULLISH", "BEARISH"):
            hit = 1 if ((bias == "BULLISH" and r > 0) or (bias == "BEARISH" and r < 0)) else 0
            bias_hits.setdefault(bias, []).append(hit)

    n = len(rets)
    if n < min_days:
        return {"status": "INSUFFICIENT_DATA",
                "reason": f"only {n} aligned days; need >= {min_days}",
                "days": n}

    ic_score = _spearman(scores, rets)
    ic_gift  = _spearman(gifts, rets)
    bar = 3 / math.sqrt(n)
    bias_acc = {b: round(100 * sum(h) / len(h), 1) for b, h in bias_hits.items() if h}
    verdict = ("NO EDGE" if abs(ic_score) < bar
               else "POSSIBLE — needs locked-holdout/DSR before any wiring")
    return {
        "status": "OK", "days": n,
        "ic_global_score": round(ic_score, 3),
        "ic_gift_change": round(ic_gift, 3),
        "significance_bar": round(bar, 3),
        "bias_direction_accuracy": bias_acc,
        "verdict": verdict,
        "note": "MEASURE only — do not wire macro into live selection on this alone.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate macro sentiment vs next-day NIFTY")
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    args = ap.parse_args()
    res = run(min_days=args.min_days)
    print("\nMACRO SENTIMENT BACKTEST")
    print("-" * 50)
    for k, v in res.items():
        print(f"  {k}: {v}")
    if res.get("status") == "INSUFFICIENT_DATA":
        print("\n  (data-gated — accruing one snapshot/day via the nightly pipeline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
