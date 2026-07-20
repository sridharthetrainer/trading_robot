"""
trend_exhaustion_miner.py — offline edge test for TREND EXHAUSTION / TURNING
POINT patterns on NIFTY (2026-07-20, operator: "trying to find reversal").

Distinct from the reversal families already killed this project:
  - level-bounce/fade (Camarilla H3/L3, CPR TC/BC, false-breakout snapback)
    -- cpr_camarilla_pattern_miner.py, 0/21 cells, gross ~0.
  - stretch-from-moving-average mean reversion -- ema_pattern_miner.py
    (E5 distance_reversion), HURTS at every horizon.
  - RSI divergence AS A STRATEGY -- signal_engine.run_rsi_divergence_strategy,
    already measured a net-R loser and pruned (pruned.json, 2026-07-09).

This file tests trend EXHAUSTION specifically: is a sustained directional
move running out of thrust, as opposed to "price touched a level." Four
patterns, all newly defined, none reusing the killed families' logic:

  T1 MOMENTUM_DIVERGENCE   price makes a new N-bar extreme with WEAKER
                          momentum (ROC) than the prior extreme -- classic
                          bearish/bullish divergence, but on raw momentum
                          decay, not RSI overbought/oversold bands.
  T2 DECELERATION_RUN      N consecutive same-direction closes where
                          bar-over-bar thrust is shrinking (still moving,
                          but each push weaker than the last).
  T3 MTF_DISAGREEMENT      5m momentum strongly directional while the most
                          recently CLOSED 1h bar's momentum has flattened
                          or turned -- the short TF running ahead of the
                          higher TF (lookahead-safe: only ever reads a 1h
                          bar whose full 60min window has already closed).
  T4 CLIMAX_EXTENSION      N consecutive same-direction closes with
                          ACCELERATING bar ranges (each bar's range wider
                          than the last) -- a parabolic/climax push.

House method throughout: one trigger per pattern per day (kills same-day
pseudo-replication), signed forward return AGAINST the exhausted direction
(fading it) at fixed horizons, 70/30 day-split holdout, Bonferroni across
every (pattern, horizon) cell, 5bps round-trip cost, reports both gross
and net so a "significant" result can be told apart from cost drag.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cpr_camarilla_pattern_miner import (
    CANDLE_DB, HORIZONS_BARS, ROUND_TRIP_COST_BPS, TRAIN_FRAC, ALPHA,
    MIN_TRAIN_N, MIN_HOLDOUT_N, _stat,
)

logger = logging.getLogger(__name__)

REPORT_FILE = Path("trend_exhaustion_report.json")
SYMBOL = "NIFTY"

SWING_LOOKBACK_BARS = 8      # ~40min on 5m bars, defines a "new N-bar extreme"
MOMENTUM_LOOKBACK_BARS = 6   # ROC window for the momentum comparison
RUN_LENGTH_BARS = 5          # consecutive same-direction closes for T2/T4
# A full NSE session is ~75 5m bars (375min/5min); T1's "prior extreme"
# search needs SWING_LOOKBACK_BARS*4 bars of same-day history, so WARMUP
# must leave real detection room within a single day, not just clear the
# indicator lookback.
WARMUP_BARS = 35


def _load_series(conn: sqlite3.Connection, symbol: str, interval: str) -> List[Tuple]:
    return conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp", (symbol, interval)).fetchall()


def _resample_1h_from_5m(bars_5m: List[Tuple]) -> List[Tuple]:
    """Reconstruct 1h OHLC from the 5m series (bucketed from each day's
    09:15 session start) instead of reading the stored 1h table, whose
    history only reaches back to 2026-04-27 vs 5m's 2025-05-19 -- using
    the stored table gave T3 a train/holdout split with ~0 usable train
    days purely because of that depth gap, not because the pattern is
    genuinely rare. This gives T3 the same ~14 months of history as
    everything else tested in this file."""
    from datetime import datetime, timedelta
    by_day: Dict[str, List[Tuple]] = defaultdict(list)
    for b in bars_5m:
        by_day[str(b[0])[:10]].append(b)
    out: List[Tuple] = []
    for day, day_bars in sorted(by_day.items()):
        day_bars = sorted(day_bars, key=lambda b: b[0])
        session_start = datetime.fromisoformat(day_bars[0][0])
        buckets: Dict[int, List[Tuple]] = defaultdict(list)
        for b in day_bars:
            t = datetime.fromisoformat(b[0])
            bucket = int((t - session_start).total_seconds() // 3600)
            buckets[bucket].append(b)
        for bucket in sorted(buckets):
            rows = buckets[bucket]
            bucket_start = session_start + timedelta(hours=bucket)
            out.append((
                bucket_start.isoformat(), rows[0][1], max(r[2] for r in rows),
                min(r[3] for r in rows), rows[-1][4], sum(r[5] or 0 for r in rows),
            ))
    return out


def _roc(bars: List[Tuple], i: int, lookback: int) -> float:
    """(close - close[i-lookback]) / close[i-lookback] * 100 -- same formula
    as indicators.calculate_roc, computed inline to stay pandas-free and
    consistent with the other miners in this file's family."""
    if i < lookback:
        return 0.0
    c0, c1 = bars[i - lookback][4], bars[i][4]
    return (c1 - c0) / c0 * 100.0 if c0 else 0.0


def _align_1h_index(bars_5m: List[Tuple], bars_1h: List[Tuple]) -> List[int]:
    """For each 5m bar, the index into bars_1h of the most recent 1h bar
    whose full 60-minute window has already closed (start_ts + 60min <=
    5m bar's ts). -1 if none yet. Lookahead-safe: never references an 1h
    bar still "in progress" relative to the 5m bar's own timestamp."""
    from datetime import datetime, timedelta
    h_starts = [datetime.fromisoformat(b[0]) for b in bars_1h]
    out = []
    j = -1
    for b in bars_5m:
        t5 = datetime.fromisoformat(b[0])
        while j + 1 < len(h_starts) and h_starts[j + 1] + timedelta(minutes=60) <= t5:
            j += 1
        out.append(j)
    return out


def _detect_day(bars: List[Tuple], h1_idx_for_bar: List[int],
                 bars_1h: List[Tuple], day_offset: int) -> List[Dict[str, Any]]:
    fired: set = set()
    out: List[Dict[str, Any]] = []

    for i in range(WARMUP_BARS, len(bars)):
        ts, o, h, l, c, v = bars[i]

        # T1 MOMENTUM_DIVERGENCE: new SWING_LOOKBACK_BARS-bar high/low with
        # weaker momentum than the prior such extreme.
        if "T1" not in fired:
            window = bars[i - SWING_LOOKBACK_BARS:i]
            is_new_high = h > max(b[2] for b in window)
            is_new_low = l < min(b[3] for b in window)
            if is_new_high or is_new_low:
                roc_now = _roc(bars, i, MOMENTUM_LOOKBACK_BARS)
                # prior extreme of the same type within the last ~4x lookback
                prior_start = max(WARMUP_BARS, i - SWING_LOOKBACK_BARS * 4)
                prior_extremes = []
                for j in range(prior_start, i - SWING_LOOKBACK_BARS):
                    wj = bars[j - SWING_LOOKBACK_BARS:j]
                    if not wj:
                        continue
                    if is_new_high and bars[j][2] > max(b[2] for b in wj):
                        prior_extremes.append(j)
                    elif is_new_low and bars[j][3] < min(b[3] for b in wj):
                        prior_extremes.append(j)
                if prior_extremes:
                    pj = prior_extremes[-1]
                    roc_prior = _roc(bars, pj, MOMENTUM_LOOKBACK_BARS)
                    if is_new_high and 0 < roc_now < roc_prior:
                        fired.add("T1")
                        out.append({"pattern": "T1_momentum_divergence", "bar": i, "direction": -1})
                    elif is_new_low and roc_prior < roc_now < 0:
                        fired.add("T1")
                        out.append({"pattern": "T1_momentum_divergence", "bar": i, "direction": 1})

        # T2 DECELERATION_RUN: N consecutive same-direction closes, each
        # bar's |close-open| smaller than the previous bar's.
        if "T2" not in fired and i >= WARMUP_BARS + RUN_LENGTH_BARS:
            run = bars[i - RUN_LENGTH_BARS + 1:i + 1]
            closes_up = all(b[4] > b[1] for b in run)
            closes_dn = all(b[4] < b[1] for b in run)
            thrusts = [abs(b[4] - b[1]) for b in run]
            decelerating = all(thrusts[k] < thrusts[k - 1] for k in range(1, len(thrusts)))
            if decelerating and closes_up:
                fired.add("T2")
                out.append({"pattern": "T2_deceleration_run", "bar": i, "direction": -1})
            elif decelerating and closes_dn:
                fired.add("T2")
                out.append({"pattern": "T2_deceleration_run", "bar": i, "direction": 1})

        # T3 MTF_DISAGREEMENT: 5m momentum strongly directional, most
        # recently CLOSED 1h bar's own momentum has flattened/reversed.
        if "T3" not in fired:
            hj = h1_idx_for_bar[i]
            if hj >= MOMENTUM_LOOKBACK_BARS:
                roc_5m = _roc(bars, i, MOMENTUM_LOOKBACK_BARS)
                roc_1h = _roc(bars_1h, hj, 3)
                if roc_5m > 0.15 and roc_1h <= 0.02:
                    fired.add("T3")
                    out.append({"pattern": "T3_mtf_disagreement", "bar": i, "direction": -1})
                elif roc_5m < -0.15 and roc_1h >= -0.02:
                    fired.add("T3")
                    out.append({"pattern": "T3_mtf_disagreement", "bar": i, "direction": 1})

        # T4 CLIMAX_EXTENSION: N consecutive same-direction closes with
        # ACCELERATING (widening) bar ranges -- parabolic push.
        if "T4" not in fired and i >= WARMUP_BARS + RUN_LENGTH_BARS:
            run = bars[i - RUN_LENGTH_BARS + 1:i + 1]
            closes_up = all(b[4] > b[1] for b in run)
            closes_dn = all(b[4] < b[1] for b in run)
            ranges = [b[2] - b[3] for b in run]
            accelerating = all(ranges[k] > ranges[k - 1] for k in range(1, len(ranges)))
            if accelerating and closes_up:
                fired.add("T4")
                out.append({"pattern": "T4_climax_extension", "bar": i, "direction": -1})
            elif accelerating and closes_dn:
                fired.add("T4")
                out.append({"pattern": "T4_climax_extension", "bar": i, "direction": 1})
    return out


def run(symbol: str = SYMBOL) -> Dict[str, Any]:
    conn = sqlite3.connect(CANDLE_DB)
    bars_5m = _load_series(conn, symbol, "5m")
    conn.close()
    bars_1h = _resample_1h_from_5m(bars_5m)

    h1_idx_for_bar = _align_1h_index(bars_5m, bars_1h)

    by_day: Dict[str, List[int]] = defaultdict(list)
    for idx, b in enumerate(bars_5m):
        by_day[str(b[0])[:10]].append(idx)

    observations: List[Dict[str, Any]] = []
    days_used = []
    for day, idxs in sorted(by_day.items()):
        if len(idxs) < WARMUP_BARS + 20:
            continue
        lo, hi = idxs[0], idxs[-1] + 1
        day_bars = bars_5m[lo:hi]
        if len(day_bars) < WARMUP_BARS + 20:
            continue
        days_used.append(day)
        day_h1_idx = h1_idx_for_bar[lo:hi]
        for sig in _detect_day(day_bars, day_h1_idx, bars_1h, lo):
            i, direction = sig["bar"], sig["direction"]
            entry = day_bars[i][4]
            for hname, nbars in HORIZONS_BARS.items():
                j = i + nbars
                if j >= len(day_bars):
                    continue
                gross_bps = direction * (day_bars[j][4] - entry) / entry * 10_000
                observations.append({"pattern": sig["pattern"], "horizon": hname, "day": day,
                                     "net_bps": gross_bps - ROUND_TRIP_COST_BPS,
                                     "gross_bps": gross_bps})

    cutoff = days_used[max(1, int(len(days_used) * TRAIN_FRAC) - 1)] if len(days_used) >= 6 else None
    cells: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(
        lambda: {"train": [], "holdout": [], "train_gross": [], "holdout_gross": []})
    for obs in observations:
        slot = "train" if (cutoff is None or obs["day"] <= cutoff) else "holdout"
        cells[(obs["pattern"], obs["horizon"])][slot].append(obs["net_bps"])
        cells[(obs["pattern"], obs["horizon"])][slot + "_gross"].append(obs["gross_bps"])

    n_tests = sum(1 for c in cells.values() if len(c["train"]) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)
    results = []
    for (pattern, horizon), c in sorted(cells.items()):
        tr, ho = _stat(c["train"]), _stat(c["holdout"])
        if c["train_gross"]:
            tr["gross_mean_bps"] = round(sum(c["train_gross"]) / len(c["train_gross"]), 2)
        if c["holdout_gross"]:
            ho["gross_mean_bps"] = round(sum(c["holdout_gross"]) / len(c["holdout_gross"]), 2)
        if tr.get("n", 0) < MIN_TRAIN_N:
            verdict = "INSUFFICIENT_N"
        else:
            p_corr = min(1.0, tr["p_raw"] * bonferroni)
            sig = p_corr < ALPHA
            ho_agrees = ho.get("n", 0) >= MIN_HOLDOUT_N and (ho.get("mean_bps", 0) or 0) * tr["mean_bps"] > 0
            if sig and tr["mean_bps"] > 0 and ho_agrees:
                verdict = "CANDIDATE"
            elif sig and tr["mean_bps"] > 0:
                verdict = "TRAIN_ONLY_OVERFIT"
            elif sig and tr["mean_bps"] < 0:
                verdict = "HURTS"
            else:
                verdict = "NOISE"
            tr["p_corrected"] = round(p_corr, 6)
        results.append({"pattern": pattern, "horizon": horizon,
                        "train": tr, "holdout": ho, "verdict": verdict})

    report = {
        "symbol": symbol, "days": len(days_used), "cutoff_day": cutoff,
        "observations": len(observations), "bonferroni_tests": bonferroni,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "params": {"swing_lookback_bars": SWING_LOOKBACK_BARS,
                   "momentum_lookback_bars": MOMENTUM_LOOKBACK_BARS,
                   "run_length_bars": RUN_LENGTH_BARS},
        "results": sorted(results, key=lambda r: (r["verdict"] != "CANDIDATE",
                                                    -(r.get("train", {}).get("t", 0) or 0))),
    }
    try:
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("report write failed: %s", exc)
    return report


def main() -> int:
    rep = run()
    print(f"=== TREND EXHAUSTION MINER | {rep['symbol']} | {rep['days']} days | "
          f"{rep['observations']} obs | Bonferroni n={rep['bonferroni_tests']} ===\n")
    for r in rep["results"]:
        tr, ho = r.get("train", {}), r.get("holdout", {})
        print(f"  {r['verdict']:<20} {r['pattern']:<26} h={r['horizon']:<6} "
              f"train n={tr.get('n', 0):>4} net={tr.get('mean_bps', '-'):>7}bps "
              f"gross={tr.get('gross_mean_bps', '-'):>7}bps "
              f"p_corr={tr.get('p_corrected', '-')} | "
              f"holdout n={ho.get('n', 0):>4} net={ho.get('mean_bps', '-'):>7}bps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
