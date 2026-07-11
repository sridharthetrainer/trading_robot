"""
structure_reverse_engineer.py — mine market-structure swing points (HH/HL/
LH/LL) and structure events (BOS/CHoCH) against forward outcomes on REAL
cached candles (2026-07-12, operator: "reverse engineer on all points hh hl
ll lh and so on and find the pattern to improvise").

Method (no-lookahead, cost-aware, holdout-validated — same discipline as
validation_harness / cost-aware net-R work):
  1. Fractal swings with K-bar confirmation: a swing high at bar i exists
     only once bars i+1..i+K printed, so the ENTRY bar is i+K, never i.
  2. Each swing labelled vs the previous swing of its kind: HH/LH for
     highs, HL/LL for lows.
  3. Pattern state = the last 4 confirmed swing labels ("HL>HH>HL>HH").
     Compact regimes: TREND_UP (HH+HL), TREND_DOWN (LH+LL), MIXED.
  4. Structure events at bar close:
       BOS_UP    close breaks last swing high inside up-structure (continuation)
       CHOCH_UP  close breaks last swing high inside DOWN-structure (reversal)
       BOS_DOWN / CHOCH_DOWN mirrored.
  5. Outcome: direction-appropriate net move over 6/12/36-bar horizons
     (30m/1h/3h on 5m bars), non-overlapping per pattern (no
     pseudo-replication), net of ROUND_TRIP_BPS costs.
  6. Validation: first 70% of DAYS = train, last 30% = holdout. A pattern
     is only CANDIDATE if it clears Bonferroni-corrected significance in
     train AND keeps sign with nonneg mean in holdout. Everything else is
     NOISE/FAIL — reported honestly.

Output: structure_reverse_engineer_report.json + console table.
Nothing here wires into live scoring — survivors go through the
learned-filter forward-holdout ledger like every other candidate.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPORT_FILE = Path("structure_reverse_engineer_report.json")
CANDLE_DB = "candle_cache.db"
SWING_K = 3                      # confirmation bars each side
STATE_LEN = 4                    # swing labels per pattern state
HORIZONS = (6, 12, 36)           # forward bars (5m -> 30m / 1h / 3h)
ROUND_TRIP_BPS = 4.0             # entry+exit cost+slippage, futures-proxy
TRAIN_FRAC = 0.70
MIN_TRAIN_N = 30
ALPHA = 0.05


def _load(symbol: str = "NIFTY", interval: str = "5m") -> pd.DataFrame:
    with sqlite3.connect(CANDLE_DB) as conn:
        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND interval=? ORDER BY timestamp",
            conn, params=(symbol, interval))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.drop_duplicates("timestamp").set_index("timestamp")
    return df


def _confirmed_swings(df: pd.DataFrame, k: int = SWING_K) -> List[Dict[str, Any]]:
    """Fractal swings; each carries confirm_idx = idx + k (first bar the
    swing is knowable). Labels compare to the previous swing of same kind."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    swings: List[Dict[str, Any]] = []
    last_high = last_low = None
    for i in range(k, n - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            label = ("HH" if last_high is not None and highs[i] > last_high
                     else "LH" if last_high is not None else "H0")
            last_high = highs[i]
            swings.append({"idx": i, "confirm_idx": i + k, "kind": "H",
                           "price": float(highs[i]), "label": label})
        if lows[i] == min(lows[i - k:i + k + 1]):
            label = ("HL" if last_low is not None and lows[i] > last_low
                     else "LL" if last_low is not None else "L0")
            last_low = lows[i]
            swings.append({"idx": i, "confirm_idx": i + k, "kind": "L",
                           "price": float(lows[i]), "label": label})
    swings.sort(key=lambda s: (s["confirm_idx"], s["idx"]))
    return swings


def _regime(labels: List[str]) -> str:
    ups = sum(1 for x in labels if x in ("HH", "HL"))
    dns = sum(1 for x in labels if x in ("LH", "LL"))
    if ups == len(labels):
        return "TREND_UP"
    if dns == len(labels):
        return "TREND_DOWN"
    return "MIXED"


def _stat(rets: List[float]) -> Dict[str, Any]:
    a = np.asarray(rets, dtype=float)
    n = len(a)
    if n == 0:
        return {"n": 0}
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    # two-sided normal-approx p (fine at these n)
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2))))
    return {"n": n, "mean_bps": round(mean, 2), "win": round(float((a > 0).mean()), 3),
            "t": round(t, 2), "p": round(p, 5)}


def _collect_events(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Every pattern/event occurrence with its entry bar and direction."""
    swings = _confirmed_swings(df)
    close = df["close"].to_numpy()
    n = len(df)
    events: List[Dict[str, Any]] = []

    # 1. swing-sequence states at each swing confirmation
    hist: List[str] = []
    swing_by_confirm: Dict[int, List[Dict[str, Any]]] = {}
    for s in swings:
        swing_by_confirm.setdefault(s["confirm_idx"], []).append(s)
    for s in swings:
        if s["label"] in ("H0", "L0"):
            hist.append(s["label"])
            continue
        hist.append(s["label"])
        recent = [x for x in hist if x not in ("H0", "L0")][-STATE_LEN:]
        if len(recent) < STATE_LEN:
            continue
        state = ">".join(recent)
        reg = _regime(recent)
        # long in up-structure, short in down-structure, both for mixed states
        direction = 1 if reg == "TREND_UP" else -1 if reg == "TREND_DOWN" else 0
        if direction == 0:
            # direction from the LAST label: bullish labels -> long
            direction = 1 if recent[-1] in ("HH", "HL") else -1
        events.append({"pattern": f"SEQ:{state}", "entry_idx": s["confirm_idx"],
                       "direction": direction})
        events.append({"pattern": f"REGIME:{reg}:{recent[-1]}",
                       "entry_idx": s["confirm_idx"], "direction": direction})

    # 2. BOS / CHoCH at bar close (uses only swings confirmed BEFORE the bar)
    last_sw_high = last_sw_low = None
    labels_so_far: List[str] = []
    pending = sorted(swings, key=lambda s: s["confirm_idx"])
    pi = 0
    for i in range(n):
        while pi < len(pending) and pending[pi]["confirm_idx"] <= i - 1:
            s = pending[pi]
            if s["kind"] == "H":
                last_sw_high = s["price"]
            else:
                last_sw_low = s["price"]
            if s["label"] not in ("H0", "L0"):
                labels_so_far.append(s["label"])
            pi += 1
        recent = labels_so_far[-STATE_LEN:]
        if len(recent) < STATE_LEN or last_sw_high is None or last_sw_low is None:
            continue
        reg = _regime(recent)
        if close[i] > last_sw_high:
            ev = "BOS_UP" if reg == "TREND_UP" else "CHOCH_UP" if reg == "TREND_DOWN" else "BREAK_UP_MIXED"
            events.append({"pattern": f"EVT:{ev}", "entry_idx": i, "direction": 1})
            last_sw_high = close[i]     # don't refire every bar of the same break
        elif close[i] < last_sw_low:
            ev = "BOS_DOWN" if reg == "TREND_DOWN" else "CHOCH_DOWN" if reg == "TREND_UP" else "BREAK_DOWN_MIXED"
            events.append({"pattern": f"EVT:{ev}", "entry_idx": i, "direction": -1})
            last_sw_low = close[i]
    return events


def _outcomes(df: pd.DataFrame, events: List[Dict[str, Any]],
              horizon: int) -> Dict[str, Dict[str, List[float]]]:
    """Net bps per pattern, non-overlapping within a pattern, split
    train/holdout by DAY (first TRAIN_FRAC of distinct days = train)."""
    close = df["close"].to_numpy()
    days = df.index.normalize()
    uniq_days = days.unique().sort_values()
    cut = uniq_days[int(len(uniq_days) * TRAIN_FRAC) - 1]
    n = len(df)
    out: Dict[str, Dict[str, List[float]]] = {}
    busy_until: Dict[str, int] = {}
    for ev in sorted(events, key=lambda e: e["entry_idx"]):
        i = ev["entry_idx"]
        pat = ev["pattern"]
        if i + horizon >= n or i < 0:
            continue
        if i < busy_until.get(pat, -1):
            continue
        # same-session only: exit bar must share the entry's trading day
        if days[i + horizon] != days[i]:
            continue
        busy_until[pat] = i + horizon
        gross = (close[i + horizon] / close[i] - 1.0) * 1e4 * ev["direction"]
        net = gross - ROUND_TRIP_BPS
        split = "train" if days[i] <= cut else "holdout"
        out.setdefault(pat, {"train": [], "holdout": []})[split].append(net)
    return out


def run(symbol: str = "NIFTY", interval: str = "5m") -> Dict[str, Any]:
    df = _load(symbol, interval)
    if len(df) < 2000:
        return {"error": f"only {len(df)} bars for {symbol} {interval}"}
    events = _collect_events(df)
    results: List[Dict[str, Any]] = []
    n_tests = 0
    for horizon in HORIZONS:
        per_pat = _outcomes(df, events, horizon)
        n_tests += sum(1 for v in per_pat.values() if len(v["train"]) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)
    for horizon in HORIZONS:
        per_pat = _outcomes(df, events, horizon)
        for pat, splits in per_pat.items():
            tr = _stat(splits["train"])
            if tr.get("n", 0) < MIN_TRAIN_N:
                continue
            ho = _stat(splits["holdout"])
            sig_train = tr["p"] * bonferroni < ALPHA and tr["mean_bps"] > 0
            held = ho.get("n", 0) >= 10 and ho.get("mean_bps", -1) > 0
            verdict = ("CANDIDATE" if sig_train and held
                       else "TRAIN_ONLY_OVERFIT" if sig_train
                       else "HURTS" if tr["mean_bps"] < 0 and tr["p"] * bonferroni < ALPHA
                       else "NOISE")
            results.append({"pattern": pat, "horizon_bars": horizon,
                            "train": tr, "holdout": ho, "verdict": verdict})
    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE",
                                -(r["train"].get("t", 0))))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol, "interval": interval, "bars": len(df),
        "span": f"{df.index[0].date()}..{df.index[-1].date()}",
        "swing_k": SWING_K, "state_len": STATE_LEN,
        "round_trip_bps": ROUND_TRIP_BPS,
        "bonferroni_tests": bonferroni,
        "n_events": len(events),
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "hurts": [r for r in results if r["verdict"] == "HURTS"][:15],
        "all_tested": results,
    }
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    full: Dict[str, Any] = {"runs": []}
    for sym, iv in (("NIFTY", "5m"), ("BANKNIFTY", "5m"), ("NIFTY", "1h")):
        rep = run(sym, iv)
        full["runs"].append(rep)
        if rep.get("error"):
            print(f"{sym} {iv}: {rep['error']}")
            continue
        print(f"\n=== {sym} {iv} | {rep['bars']} bars {rep['span']} | "
              f"{rep['n_events']} events | Bonferroni x{rep['bonferroni_tests']} ===")
        cands = rep["candidates"]
        print(f"CANDIDATES surviving train-significance + holdout: {len(cands)}")
        for r in cands:
            print(f"  ✅ {r['pattern']} h={r['horizon_bars']}: "
                  f"train n={r['train']['n']} {r['train']['mean_bps']}bps "
                  f"t={r['train']['t']} | holdout n={r['holdout'].get('n')} "
                  f"{r['holdout'].get('mean_bps')}bps")
        top = [r for r in rep["all_tested"] if r["verdict"] != "CANDIDATE"][:8]
        for r in top:
            print(f"  {r['verdict']:>18} {r['pattern']} h={r['horizon_bars']}: "
                  f"train n={r['train']['n']} {r['train']['mean_bps']}bps "
                  f"t={r['train']['t']} p={r['train']['p']} | "
                  f"holdout {r['holdout'].get('mean_bps')}bps n={r['holdout'].get('n')}")
    REPORT_FILE.write_text(json.dumps(full, indent=2))
    print(f"\nreport -> {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
