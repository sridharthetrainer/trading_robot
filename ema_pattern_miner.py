"""
ema_pattern_miner.py — offline edge test for the 6-pattern 20/50-EMA
library pasted from an external AI on 2026-07-17 (pullback bounce,
20/50 crossover, slope persistence, squeeze expansion, extreme-distance
reversion, 50-EMA support/resistance test).

RESEARCH ONLY. Reuses cpr_camarilla_pattern_miner's stats scaffolding
(same house method: one trigger per pattern per day, cost-adjusted signed
forward returns at 15/30/60min, 70/30 day-split holdout, Bonferroni).
Unlike the CPR miner, EMAs need warm-up history that crosses day
boundaries, so indicators are computed once over the continuous 5m series
and triggers are then evaluated within each day.

Spec deviations (documented, not silent):
  - "touch within X points" thresholds don't scale; the spec's own
    ATR-relative variants are used everywhere.
  - Volume-confirmation clauses are dropped: NSE index candles carry no
    real volume (same UNTESTABLE reason as the CPR miner's P4).
  - P1 is long-only per its own spec (BULLISH_CONTINUATION); P2-P6 trade
    both directions as specified.

Priors, for context: the equity registry's ma_cross strategy fails OOS
validation and `trend` is pruned as a measured loser; these six 5m
conjunctions are nevertheless new, untested cells.
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
    MIN_TRAIN_N, MIN_HOLDOUT_N, ATR_BARS, _stat,
)

logger = logging.getLogger(__name__)

REPORT_FILE = Path("ema_pattern_report.json")
SYMBOL = "NIFTY"
EMA_WARMUP_BARS = 150   # bars skipped before any trigger (50-EMA settling)


def _load_series(conn: sqlite3.Connection, symbol: str, interval: str) -> List[Tuple]:
    return conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp", (symbol, interval)).fetchall()


def _ema_series(closes: List[float], n: int) -> List[float]:
    k = 2.0 / (n + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(c * k + out[-1] * (1 - k))
    return out


def _atr_series(bars: List[Tuple], n: int = ATR_BARS) -> List[float]:
    trs = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i][2], bars[i][3], bars[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = []
    for i in range(len(trs)):
        lo = max(1, i - n + 1)
        window = trs[lo:i + 1]
        out.append(sum(window) / len(window) if window else 0.0)
    return out


def _detect(bars: List[Tuple], ema20: List[float], ema50: List[float],
             atr: List[float]) -> List[Dict[str, Any]]:
    """All 6 detectors over the continuous series; one trigger per pattern
    per day."""
    fired_today: Dict[str, set] = defaultdict(set)
    out: List[Dict[str, Any]] = []

    for i in range(EMA_WARMUP_BARS, len(bars)):
        day = str(bars[i][0])[:10]
        fired = fired_today[day]
        ts, o, h, l, c, v = bars[i]
        a = atr[i]
        if a <= 0:
            continue
        e20, e50 = ema20[i], ema50[i]
        p20, p50 = ema20[i - 1], ema50[i - 1]
        prev_c = bars[i - 1][4]

        # E1 PULLBACK_BOUNCE (long-only per spec): uptrend stack, prior bar
        # touched/dipped to the 20-EMA (low within 0.5*ATR of it or below),
        # this bar closes green back above it.
        if "E1" not in fired and c > e20 > e50:
            prior_touched = bars[i - 1][3] <= p20 + 0.5 * a and bars[i - 1][3] >= p20 - 0.5 * a
            if prior_touched and c > o and prev_c <= p20 + 0.2 * a:
                fired.add("E1")
                out.append({"pattern": "E1_ema20_pullback_bounce", "bar": i, "direction": 1})

        # E2 CROSSOVER: 20-EMA crossing the 50-EMA this bar.
        if "E2" not in fired:
            if p20 <= p50 and e20 > e50:
                fired.add("E2")
                out.append({"pattern": "E2_ema_crossover", "bar": i, "direction": 1})
            elif p20 >= p50 and e20 < e50:
                fired.add("E2")
                out.append({"pattern": "E2_ema_crossover", "bar": i, "direction": -1})

        # E3 SLOPE persistence: 20-EMA slope same sign 3 consecutive bars,
        # price on the same side of the 20-EMA.
        if "E3" not in fired and i >= EMA_WARMUP_BARS + 3:
            d1 = ema20[i] - ema20[i - 1]
            d2 = ema20[i - 1] - ema20[i - 2]
            d3 = ema20[i - 2] - ema20[i - 3]
            if d1 > 0 and d2 > 0 and d3 > 0 and c > e20:
                fired.add("E3")
                out.append({"pattern": "E3_ema_slope_persist", "bar": i, "direction": 1})
            elif d1 < 0 and d2 < 0 and d3 < 0 and c < e20:
                fired.add("E3")
                out.append({"pattern": "E3_ema_slope_persist", "bar": i, "direction": -1})

        # E4 SQUEEZE_EXPANSION: EMAs were pinched (<0.2*ATR apart within the
        # last 2 bars), distance now expanded >50% vs 2 bars ago; direction
        # = which side the 20-EMA pulled away to.
        if "E4" not in fired and i >= EMA_WARMUP_BARS + 2:
            dist_now = abs(e20 - e50)
            dist_back = abs(ema20[i - 2] - ema50[i - 2])
            if dist_back < 0.2 * a and dist_now > dist_back * 1.5 and dist_now > 0:
                fired.add("E4")
                out.append({"pattern": "E4_ema_squeeze_expansion", "bar": i,
                            "direction": 1 if e20 > e50 else -1})

        # E5 DISTANCE_REVERSION: price stretched >1.5*ATR from the 20-EMA
        # and the stretch has started shrinking -> trade back toward EMA.
        if "E5" not in fired:
            dist = c - e20
            prev_dist = prev_c - p20
            if dist > 1.5 * a and abs(dist) < abs(prev_dist):
                fired.add("E5")
                out.append({"pattern": "E5_distance_reversion", "bar": i, "direction": -1})
            elif dist < -1.5 * a and abs(dist) < abs(prev_dist):
                fired.add("E5")
                out.append({"pattern": "E5_distance_reversion", "bar": i, "direction": 1})

        # E6 50-EMA TEST: trend-stacked, price within 0.3*ATR of the 50-EMA,
        # closing back on the trend side (bounce with-trend / rejection
        # against a bear stack).
        if "E6" not in fired and abs(c - e50) <= 0.3 * a:
            if c > e50 and e20 > e50 and bars[i - 1][3] <= e50:
                fired.add("E6")
                out.append({"pattern": "E6_ema50_test", "bar": i, "direction": 1})
            elif c < e50 and e20 < e50 and bars[i - 1][2] >= e50:
                fired.add("E6")
                out.append({"pattern": "E6_ema50_test", "bar": i, "direction": -1})
    return out


def run(symbol: str = SYMBOL) -> Dict[str, Any]:
    conn = sqlite3.connect(CANDLE_DB)
    bars = _load_series(conn, symbol, "5m")
    conn.close()
    if len(bars) < EMA_WARMUP_BARS + 50:
        return {"error": f"only {len(bars)} bars"}

    closes = [b[4] for b in bars]
    ema20 = _ema_series(closes, 20)
    ema50 = _ema_series(closes, 50)
    atr = _atr_series(bars)

    signals = _detect(bars, ema20, ema50, atr)

    observations: List[Dict[str, Any]] = []
    for sig in signals:
        i, direction = sig["bar"], sig["direction"]
        entry = bars[i][4]
        day = str(bars[i][0])[:10]
        for hname, nbars in HORIZONS_BARS.items():
            j = i + nbars
            if j >= len(bars) or str(bars[j][0])[:10] != day:
                continue   # same-session only
            gross = direction * (bars[j][4] - entry) / entry * 10_000
            observations.append({"pattern": sig["pattern"], "horizon": hname,
                                 "day": day, "net_bps": gross - ROUND_TRIP_COST_BPS,
                                 "gross_bps": gross})

    days_used = sorted({o["day"] for o in observations})
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
    if rep.get("error"):
        print(rep["error"])
        return 1
    print(f"=== EMA PATTERN MINER | {rep['symbol']} | {rep['days']} days | "
          f"{rep['observations']} obs | Bonferroni n={rep['bonferroni_tests']} ===\n")
    for r in rep["results"]:
        tr, ho = r.get("train", {}), r.get("holdout", {})
        print(f"  {r['verdict']:<20} {r['pattern']:<28} h={r['horizon']:<6} "
              f"train n={tr.get('n', 0):>4} net={tr.get('mean_bps', '-'):>7}bps "
              f"gross={tr.get('gross_mean_bps', '-'):>7}bps "
              f"p_corr={tr.get('p_corrected', '-')} | "
              f"holdout n={ho.get('n', 0):>4} net={ho.get('mean_bps', '-'):>7}bps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
