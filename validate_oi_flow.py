#!/usr/bin/env python3
"""
validate_oi_flow.py — does intraday OPTION-OI FLOW predict the next NIFTY move?

This is the rigorous test for EDGE_STRATEGY's one new-information hypothesis (the
free intraday OI accrual). It reads intraday_oi_snapshots (written by
intraday_oi_logger), builds an OI-flow signal at each snapshot, aligns it to the
NEXT snapshot's spot move (no lookahead), and measures predictiveness with a
significance bar — data-gated (refuses a verdict until enough samples accrue).

Signals tested per snapshot (near-ATM, ±window of spot):
  • pcr        = put_OI / call_OI            (level)
  • d_pcr      = change in PCR vs prior snap  (flow)
  • oi_imbal   = (Δput_OI − Δcall_OI) sum     (net writing flow)
Target: spot return from this snapshot to the next (same day), entry_lag=1 (the
OI is known at snapshot t; the move t→t+1 is the future). NO lookahead.

Makes NO edge claim — it MEASURES. Given every adjacent hypothesis has failed,
expect a null; the point is an honest verdict, not hope.

Usage:  python validate_oi_flow.py [--min-samples 200] [--window-pct 0.04]
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from collections import defaultdict
from typing import Dict, List, Tuple

DB = "signal_log.db"
MIN_SAMPLES = 200   # need this many snapshot→next-snapshot pairs before a verdict


def _spearman(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 5:
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
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx)) or 1e-9
    vy = math.sqrt(sum((b - my) ** 2 for b in ry)) or 1e-9
    return cov / (vx * vy)


def load_snapshots(db: str = DB, underlying: str = "NIFTY") -> List[dict]:
    try:
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT timestamp, spot, strike, ce_oi, pe_oi FROM intraday_oi_snapshots "
                "WHERE underlying=? AND spot>0 ORDER BY timestamp", (underlying,)).fetchall()
        finally:
            conn.close()
        return [{"ts": r[0], "spot": r[1], "strike": r[2], "ce_oi": r[3], "pe_oi": r[4]} for r in rows]
    except Exception:
        return []


def build_pairs(rows: List[dict], window_pct: float = 0.04) -> List[Tuple[float, float, float, float]]:
    """Return [(pcr, d_pcr, oi_imbal, next_return), ...] — one per snapshot with a successor."""
    # group strikes per timestamp → aggregate near-ATM call/put OI + spot
    snaps: Dict[str, dict] = defaultdict(lambda: {"spot": 0.0, "ce": 0.0, "pe": 0.0})
    for r in rows:
        s = snaps[r["ts"]]
        s["spot"] = r["spot"] or s["spot"]
        if s["spot"] and abs(math.log((r["strike"] or 1) / s["spot"])) <= window_pct:
            s["ce"] += r["ce_oi"] or 0.0
            s["pe"] += r["pe_oi"] or 0.0
    ordered = [snaps[k] for k in sorted(snaps)]
    out = []
    prev_pcr = None
    for i in range(len(ordered) - 1):
        cur, nxt = ordered[i], ordered[i + 1]
        if cur["ce"] <= 0 or cur["spot"] <= 0 or nxt["spot"] <= 0:
            prev_pcr = None
            continue
        # same-day successor only (skip overnight gaps between snapshots on diff days)
        pcr = cur["pe"] / cur["ce"]
        d_pcr = (pcr - prev_pcr) if prev_pcr is not None else 0.0
        oi_imbal = (cur["pe"] - cur["ce"])
        nxt_ret = nxt["spot"] / cur["spot"] - 1.0
        out.append((pcr, d_pcr, oi_imbal, nxt_ret))
        prev_pcr = pcr
    return out


def validate(db: str = DB, min_samples: int = MIN_SAMPLES, window_pct: float = 0.04) -> Dict[str, object]:
    rows = load_snapshots(db)
    if not rows:
        return {"status": "NO_DATA",
                "reason": "intraday_oi_snapshots empty/absent — restart the bot so the OI logger runs"}
    pairs = build_pairs(rows, window_pct)
    n = len(pairs)
    if n < min_samples:
        return {"status": "INSUFFICIENT_DATA", "samples": n,
                "reason": f"have {n}, need >= {min_samples} snapshot pairs (accruing)"}
    pcr = [p[0] for p in pairs]; dpcr = [p[1] for p in pairs]
    imbal = [p[2] for p in pairs]; ret = [p[3] for p in pairs]
    bar = 3 / math.sqrt(n)
    ics = {"pcr": _spearman(pcr, ret), "d_pcr": _spearman(dpcr, ret), "oi_imbal": _spearman(imbal, ret)}
    best = max(ics, key=lambda k: abs(ics[k]))
    verdict = ("NO EDGE" if abs(ics[best]) < bar
               else f"POSSIBLE ({best}) — needs locked-holdout/DSR before any wiring")
    return {"status": "OK", "samples": n, "significance_bar": round(bar, 3),
            "IC": {k: round(v, 3) for k, v in ics.items()}, "verdict": verdict,
            "note": "MEASURE only — do not wire OI-flow into live selection on this alone."}


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate intraday OI-flow vs next NIFTY move")
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    ap.add_argument("--window-pct", type=float, default=0.04)
    args = ap.parse_args()
    res = validate(min_samples=args.min_samples, window_pct=args.window_pct)
    print("\nINTRADAY OI-FLOW VALIDATION")
    print("-" * 50)
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
