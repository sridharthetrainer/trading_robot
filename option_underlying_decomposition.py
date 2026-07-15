"""
option_underlying_decomposition.py — does the option bot's OI-flow-implied
DIRECTION call have any edge at all in the underlying, separate from
everything the option wrapper adds (strike selection, IV, theta, spread,
costs)? (2026-07-15, operator: "reverse engineer... underlying vs option
construction edge", from a ChatGPT audit-program critique.)

Every strategy on the equity/underlying side already trades the underlying
directly — no option wrapper to decompose, that question is already
answered (all fail OOS). The option bot's flow-derived direction
(LONG_BUILDUP/SHORT_COVERING/etc. -> BULLISH/BEARISH) is different: nothing
so far has isolated "does the underlying actually move the implied way"
from "does the final option P&L work". This measures ONLY the first part,
using the underlying's own candles — zero option premium, theta, spread,
or cost anywhere in this file.

Method (same discipline as option_cohort_edge_miner.py /
structure_reverse_engineer.py):
  - Dedup to one observation per (underlying, snapshot_time, direction) —
    the option_strike_signals table has ~5x pseudo-replication from
    multiple strikes sharing the same underlying moment and view.
  - Signed forward return in the underlying's OWN candles at fixed
    horizons (15/30/60/180 min), same-session only (no overnight gap).
  - Day-split (70/30) train/holdout, Bonferroni across every horizon x
    flow combination tested, holdout must independently confirm sign.
  - Reports the KNOWN option net_r alongside the underlying-only result so
    the gap (if any) between "does the market move right" and "does the
    trade make money" is explicit.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

SNAPSHOT_DB = "option_chain_snapshots.db"
CANDLE_DB = "candle_cache.db"
REPORT_FILE = Path("option_underlying_decomposition_report.json")

HORIZONS_MIN = (15, 30, 60, 180)
TRAIN_FRAC = 0.70
MIN_TRAIN_N = 30
MIN_HOLDOUT_N = 15
ALPHA = 0.05

# Known option net_r for context (from option_cohort_edge_miner.py /
# option_bot_audit, all-time, 2026-07-15) — not recomputed here, just
# reported alongside for the "does the wrapper destroy the call" gap.
KNOWN_OPTION_NET_R_ALL_TIME = -0.0131


def _load_observations(underlyings: Tuple[str, ...]) -> List[Dict[str, Any]]:
    with sqlite3.connect(SNAPSHOT_DB) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT underlying, snapshot_time, direction, flow
                  FROM option_strike_signals
                 WHERE underlying IN ({','.join('?' for _ in underlyings)})
                 ORDER BY underlying, snapshot_time""",
            underlyings,
        ).fetchall()
    return [{"underlying": r[0], "snapshot_time": r[1], "direction": r[2], "flow": r[3]}
            for r in rows]


def _load_candles(underlying: str) -> List[Tuple[datetime, float]]:
    with sqlite3.connect(CANDLE_DB) as conn:
        rows = conn.execute(
            "SELECT timestamp, close FROM candles WHERE symbol=? AND interval='5m' "
            "ORDER BY timestamp", (underlying,)).fetchall()
    out = []
    for ts, close in rows:
        try:
            out.append((datetime.fromisoformat(ts), float(close)))
        except Exception:
            continue
    return out


def _parse_snapshot_time(s: str) -> datetime:
    # "2026-06-19T09:16:23+0530" -> aware datetime
    s = s.replace("T", " ")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return datetime.fromisoformat(s)


def _forward_return(candles: List[Tuple[datetime, float]], entry_ts: datetime,
                     horizon_min: int) -> float | None:
    """Signed-agnostic (unsigned) pct return over horizon_min, same session
    only. None if no valid entry/exit candle pair exists."""
    entry_close = None
    entry_day = entry_ts.date()
    exit_close = None
    exit_deadline = entry_ts + timedelta(minutes=horizon_min)
    for ts, close in candles:
        if ts.date() != entry_day:
            if entry_close is not None and exit_close is None:
                return None  # ran past session close before horizon reached
            continue
        if entry_close is None and ts >= entry_ts:
            entry_close = close
            continue
        if entry_close is not None and ts >= exit_deadline:
            exit_close = close
            break
    if entry_close is None or exit_close is None or entry_close <= 0:
        return None
    return (exit_close - entry_close) / entry_close * 1e4  # bps, unsigned


def _stat(rets: List[float]) -> Dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0}
    mean = sum(rets) / n
    sd = 0.0
    if n > 1:
        var = sum((x - mean) ** 2 for x in rets) / (n - 1)
        sd = math.sqrt(var)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    win = sum(1 for x in rets if x > 0) / n
    return {"n": n, "mean_bps": round(mean, 2), "win_rate": round(win, 3),
            "t": round(t, 2), "p": round(p, 5)}


def _verdict(train: Dict[str, Any], holdout: Dict[str, Any], bonferroni: int) -> str:
    if train.get("n", 0) < MIN_TRAIN_N:
        return "INSUFFICIENT_DATA"
    sig = train["p"] * bonferroni < ALPHA
    held = (holdout.get("n", 0) >= MIN_HOLDOUT_N
            and holdout.get("mean_bps", 0) * train["mean_bps"] > 0)
    if sig and train["mean_bps"] > 0 and held:
        return "CANDIDATE"
    if sig and train["mean_bps"] > 0:
        return "TRAIN_ONLY_OVERFIT"
    if sig and train["mean_bps"] < 0:
        return "DIRECTION_WRONG_WAY"
    return "NOISE"


def run(underlyings: Tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY")) -> Dict[str, Any]:
    obs = _load_observations(underlyings)
    candle_cache = {u: _load_candles(u) for u in underlyings}

    days_seen = sorted({o["snapshot_time"][:10] for o in obs})
    if len(days_seen) < 6:
        return {"error": f"only {len(days_seen)} distinct days of signals "
                          f"(need >= 6) — accumulating, check back later"}
    cutoff = days_seen[max(1, int(len(days_seen) * TRAIN_FRAC) - 1)]

    # signed forward return per observation per horizon, plus flow tag
    per_horizon_flow: Dict[Tuple[int, str], Tuple[List[float], List[float]]] = {}
    per_horizon_all: Dict[int, Tuple[List[float], List[float]]] = {}
    skipped = 0
    for o in obs:
        try:
            entry_ts = _parse_snapshot_time(o["snapshot_time"])
        except Exception:
            skipped += 1
            continue
        sign = 1.0 if o["direction"] == "BULLISH" else -1.0
        day = o["snapshot_time"][:10]
        train_slot = day <= cutoff
        for h in HORIZONS_MIN:
            ret = _forward_return(candle_cache[o["underlying"]], entry_ts, h)
            if ret is None:
                continue
            signed = sign * ret
            tr_all, ho_all = per_horizon_all.setdefault(h, ([], []))
            (tr_all if train_slot else ho_all).append(signed)
            key = (h, o["flow"])
            tr_f, ho_f = per_horizon_flow.setdefault(key, ([], []))
            (tr_f if train_slot else ho_f).append(signed)

    n_tests = sum(1 for tr, _ in per_horizon_all.values() if len(tr) >= MIN_TRAIN_N)
    n_tests += sum(1 for tr, _ in per_horizon_flow.values() if len(tr) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)

    results: List[Dict[str, Any]] = []
    for h, (tr, ho) in per_horizon_all.items():
        tr_stat = _stat(tr)
        if tr_stat.get("n", 0) < MIN_TRAIN_N:
            continue
        ho_stat = _stat(ho)
        results.append({"kind": "overall", "horizon_min": h,
                        "train": tr_stat, "holdout": ho_stat,
                        "verdict": _verdict(tr_stat, ho_stat, bonferroni)})
    for (h, flow), (tr, ho) in per_horizon_flow.items():
        tr_stat = _stat(tr)
        if tr_stat.get("n", 0) < MIN_TRAIN_N:
            continue
        ho_stat = _stat(ho)
        results.append({"kind": f"flow:{flow}", "horizon_min": h,
                        "train": tr_stat, "holdout": ho_stat,
                        "verdict": _verdict(tr_stat, ho_stat, bonferroni)})

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["train"].get("t", 0) or 0)))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "underlyings": list(underlyings),
        "distinct_observations": len(obs), "skipped_unparseable": skipped,
        "days": len(days_seen), "cutoff_day": cutoff,
        "bonferroni_tests": bonferroni,
        "known_option_net_r_all_time": KNOWN_OPTION_NET_R_ALL_TIME,
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "direction_wrong_way": [r for r in results if r["verdict"] == "DIRECTION_WRONG_WAY"][:10],
        "all_tested": results,
    }
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
    print(f"=== UNDERLYING-VS-OPTION DECOMPOSITION | {rep['distinct_observations']} distinct "
          f"observations over {rep['days']} days | Bonferroni x{rep['bonferroni_tests']} ===")
    print(f"(known option net_r, all-time: {rep['known_option_net_r_all_time']})\n")
    cands = rep["candidates"]
    print(f"CANDIDATES (real underlying edge, holdout-confirmed): {len(cands)}")
    for r in cands:
        print(f"  ✅ {r['kind']} h={r['horizon_min']}min: train n={r['train']['n']} "
              f"{r['train']['mean_bps']}bps t={r['train']['t']} | "
              f"holdout n={r['holdout'].get('n')} {r['holdout'].get('mean_bps')}bps")
    print("\nTop by |t| (regardless of verdict):")
    for r in sorted(rep["all_tested"], key=lambda r: -abs(r["train"].get("t", 0) or 0))[:12]:
        print(f"  {r['verdict']:>20} {r['kind']:<20} h={r['horizon_min']:>3}min: "
              f"train n={r['train']['n']:>5} {r['train']['mean_bps']:>7}bps "
              f"t={r['train']['t']:>6} p={r['train']['p']:.4f} | "
              f"holdout n={r['holdout'].get('n')} {r['holdout'].get('mean_bps')}bps")
    print(f"\nreport -> {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
