"""
cpr_camarilla_pattern_miner.py — offline edge test for the 5-pattern
CPR/Camarilla library pasted from an external AI on 2026-07-16
(resistance-cluster breakout, CPR pullback, Camarilla boundary fade,
volume-price divergence, session PDH/PDL+CPR breakout).

House method, same as structure_reverse_engineer.py: this is RESEARCH ONLY.
It reads candle_cache.db, detects each pattern per the spec's own rules
(translated faithfully; deviations documented inline), takes at most ONE
trigger per pattern per day (kills same-day pseudo-replication), measures
the cost-adjusted signed forward return in the pattern's implied direction
at fixed horizons, splits by day 70/30 train/holdout, and Bonferroni-
corrects across every (pattern, horizon) cell tested. Verdicts:

  CANDIDATE          train-significant, positive, holdout sign-confirms
  TRAIN_ONLY_OVERFIT train-significant + positive, holdout disagrees/thin
  HURTS              train-significant and NEGATIVE (the pattern's own
                     direction loses money)
  NOISE              nothing significant
  UNTESTABLE         required input missing (e.g. index volume for the
                     divergence pattern -- NSE index candles carry no real
                     volume)

Spec translations that had to deviate (documented, not silent):
  - "within 2-3 points" cluster width: written for a low-priced instrument;
    the spec's own Python sample uses `atr * 0.3`, which is what's used.
  - Pattern 5's "fill zone" is a TradingView-indicator concept with no
    derivable definition -- omitted; the core PDH+TC / PDL+BC conjunction
    is what's tested.
  - Entry/SL/TP mechanics ("+1 point", "-2 points") don't scale across
    instruments; forward-return horizons are used instead, same as every
    other miner here. SL/TP variants only matter if raw direction has edge.

Prior evidence (context, not prejudgment): pivot/SR/volume score-modifiers
measured NOISE/HURTS on 15,434 live signals (pruned 2026-07-15/16);
HH/HL/BOS structure mining found 0/95 with edge. These conjunctions are
new cells, hence this test.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pivot_boss import calc_cpr, calc_camarilla_pivots, calc_floor_pivots

logger = logging.getLogger(__name__)

CANDLE_DB = "candle_cache.db"
REPORT_FILE = Path("cpr_camarilla_pattern_report.json")

SYMBOL = "NIFTY"
HORIZONS_BARS = {"15min": 3, "30min": 6, "60min": 12}   # 5m bars
ROUND_TRIP_COST_BPS = 5.0    # conservative flat round-trip on the underlying
TRAIN_FRAC = 0.70
ALPHA = 0.05
MIN_TRAIN_N = 30
MIN_HOLDOUT_N = 12
ATR_BARS = 14
ROUND_NUMBER_STEP = 100.0    # NIFTY psychological levels


def _load_days(conn: sqlite3.Connection, symbol: str, interval: str) -> Dict[str, List[Tuple]]:
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp", (symbol, interval)).fetchall()
    by_day: Dict[str, List[Tuple]] = defaultdict(list)
    for r in rows:
        by_day[str(r[0])[:10]].append(r)
    return by_day


def _atr(bars: List[Tuple], i: int, n: int = ATR_BARS) -> float:
    lo = max(1, i - n + 1)
    trs = []
    for j in range(lo, i + 1):
        h, l, pc = bars[j][2], bars[j][3], bars[j - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _round_levels_near(price: float) -> List[float]:
    base = round(price / ROUND_NUMBER_STEP) * ROUND_NUMBER_STEP
    return [base - ROUND_NUMBER_STEP, base, base + ROUND_NUMBER_STEP]


def _is_reversal_candle(o: float, h: float, l: float, c: float,
                         prev_o: float, prev_c: float) -> bool:
    """Pin bar / hammer / shooting star / engulfing, per the spec's list."""
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    pin = body < rng * 0.34 and (upper_wick > rng * 0.5 or lower_wick > rng * 0.5)
    engulf = body > 0 and abs(prev_c - prev_o) > 0 and (
        (c > o and prev_c < prev_o and c >= prev_o and o <= prev_c)
        or (c < o and prev_c > prev_o and c <= prev_o and o >= prev_c))
    return pin or engulf


def _detect_day(bars: List[Tuple], levels: Dict[str, float],
                 volume_ok: bool) -> List[Dict[str, Any]]:
    """Run all 5 detectors over one day's 5m bars. Each pattern fires at
    most once per day (first trigger wins)."""
    fired: set = set()
    out: List[Dict[str, Any]] = []
    tc, bc = levels["TC"], levels["BC"]
    h3, l3, h5, l5 = levels["H3"], levels["L3"], levels["H5"], levels["L5"]
    r1, r2, r3 = levels["R1"], levels["R2"], levels["R3"]
    pdh, pdl = levels["PDH"], levels["PDL"]

    for i in range(ATR_BARS + 1, len(bars)):
        ts, o, h, l, c, v = bars[i]
        atr = _atr(bars, i)
        if atr <= 0:
            continue
        prev_c = bars[i - 1][4]
        prev_o = bars[i - 1][1]

        # P1 RESISTANCE_CLUSTER breakout: >=2 resistance levels clustered
        # within 0.3*ATR just overhead, price above TC, close crosses above
        # the cluster high -> long breakout.
        if "P1" not in fired and c > tc:
            overhead = [x for x in [h3, r1, r2, r3, pdh] + _round_levels_near(c)
                        if c < x <= c + 1.5 * atr]
            if len(overhead) >= 2:
                cluster = sorted(overhead)
                for a_idx in range(len(cluster) - 1):
                    grp = [cluster[a_idx]]
                    for b in cluster[a_idx + 1:]:
                        if b - grp[0] <= 0.3 * atr:
                            grp.append(b)
                    if len(grp) >= 2 and prev_c <= max(grp) < c:
                        fired.add("P1")
                        out.append({"pattern": "P1_resistance_cluster_breakout",
                                    "bar": i, "direction": 1})
                        break

        # P2 CPR_PULLBACK bounce: was above TC 5-10 bars ago, now below TC,
        # within 0.5*ATR of BC or L3 -> long bounce.
        if "P2" not in fired and 10 < i and c < tc:
            was_above = any(bars[j][4] > tc for j in range(max(0, i - 10), max(0, i - 4)))
            near_support = min(abs(c - bc), abs(c - l3)) <= 0.5 * atr
            if was_above and near_support:
                fired.add("P2")
                out.append({"pattern": "P2_cpr_pullback_bounce", "bar": i, "direction": 1})

        # P3 CAMARILLA_BOUNDARY fade: within 0.2*ATR of H3/H5 (short) or
        # L3/L5 (long), >=2 touches in last 5 bars, reversal candle.
        if "P3" not in fired:
            for lvl, direction in ((h3, -1), (h5, -1), (l3, 1), (l5, 1)):
                if abs(c - lvl) <= 0.2 * atr:
                    touches = sum(1 for j in range(max(0, i - 4), i + 1)
                                  if bars[j][3] <= lvl <= bars[j][2])
                    if touches >= 2 and _is_reversal_candle(o, h, l, c, prev_o, prev_c):
                        fired.add("P3")
                        out.append({"pattern": "P3_camarilla_boundary_fade",
                                    "bar": i, "direction": direction})
                        break

        # P4 VOLUME_DIVERGENCE at resistance: higher price highs on lower
        # volume highs near a resistance level -> short. Requires real
        # volume, which NSE *index* candles don't carry -- gated upstream.
        if "P4" not in fired and volume_ok and i >= 20:
            win = bars[i - 9:i + 1]
            half_a, half_b = win[:5], win[5:]
            price_hh = max(b[2] for b in half_b) > max(b[2] for b in half_a)
            vol_lh = max(b[5] for b in half_b) < max(b[5] for b in half_a)
            at_res = min(abs(c - x) for x in (r1, h3, pdh)) <= 0.3 * atr
            if price_hh and vol_lh and at_res:
                fired.add("P4")
                out.append({"pattern": "P4_volume_divergence_short", "bar": i, "direction": -1})

        # P5 SESSION_BREAKOUT: close crosses above BOTH PDH and TC -> long;
        # below both PDL and BC -> short. (Spec's "fill zone" omitted --
        # TradingView-indicator concept with no derivable definition.)
        if "P5" not in fired:
            up_lvl, dn_lvl = max(pdh, tc), min(pdl, bc)
            if prev_c <= up_lvl < c:
                fired.add("P5")
                out.append({"pattern": "P5_session_breakout", "bar": i, "direction": 1})
            elif prev_c >= dn_lvl > c:
                fired.add("P5")
                out.append({"pattern": "P5_session_breakout", "bar": i, "direction": -1})
    return out


def _stat(rets: List[float]) -> Dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0}
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(n - 1, 1)
    sd = math.sqrt(var)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2)))) if n > 3 else 1.0
    return {"n": n, "mean_bps": round(mean, 2), "t": round(t, 3), "p_raw": round(p, 6),
            "win_rate": round(sum(1 for r in rets if r > 0) / n, 4)}


def run(symbol: str = SYMBOL) -> Dict[str, Any]:
    conn = sqlite3.connect(CANDLE_DB)
    intraday = _load_days(conn, symbol, "5m")
    daily = _load_days(conn, symbol, "1d")
    conn.close()

    daily_by_date = {d: rows[-1] for d, rows in daily.items()}
    daily_dates = sorted(daily_by_date)

    # Index candles carry no real volume on NSE; only test P4 if volume is
    # genuinely present (non-zero on a meaningful share of bars).
    all_vols = [b[5] for rows in intraday.values() for b in rows]
    volume_ok = bool(all_vols) and (sum(1 for v in all_vols if v and v > 0) / len(all_vols)) > 0.5

    observations: List[Dict[str, Any]] = []
    days_used = []
    for day in sorted(intraday):
        prev_days = [d for d in daily_dates if d < day]
        if not prev_days:
            continue
        pd_row = daily_by_date[prev_days[-1]]
        prev_h, prev_l, prev_c = float(pd_row[2]), float(pd_row[3]), float(pd_row[4])
        cpr = calc_cpr(prev_h, prev_l, prev_c)
        cam = calc_camarilla_pivots(prev_h, prev_l, prev_c)
        piv = calc_floor_pivots(prev_h, prev_l, prev_c)
        levels = {"TC": cpr["tc"], "BC": cpr["bc"],
                  "H3": cam["H3"], "L3": cam["L3"], "H5": cam["H5"], "L5": cam["L5"],
                  "R1": piv["R1"], "R2": piv["R2"], "R3": piv["R3"],
                  "PDH": prev_h, "PDL": prev_l}
        bars = intraday[day]
        if len(bars) < ATR_BARS + 15:
            continue
        days_used.append(day)
        for sig in _detect_day(bars, levels, volume_ok):
            i, direction = sig["bar"], sig["direction"]
            entry = bars[i][4]
            for hname, nbars in HORIZONS_BARS.items():
                if i + nbars >= len(bars):
                    continue   # same-session only, no overnight
                exit_px = bars[i + nbars][4]
                gross_bps = direction * (exit_px - entry) / entry * 10_000
                observations.append({
                    "pattern": sig["pattern"], "horizon": hname, "day": day,
                    "net_bps": gross_bps - ROUND_TRIP_COST_BPS,
                })

    cutoff = days_used[max(1, int(len(days_used) * TRAIN_FRAC) - 1)] if len(days_used) >= 6 else None
    cells: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(lambda: {"train": [], "holdout": []})
    for obs in observations:
        slot = "train" if (cutoff is None or obs["day"] <= cutoff) else "holdout"
        cells[(obs["pattern"], obs["horizon"])][slot].append(obs["net_bps"])

    n_tests = sum(1 for c in cells.values() if len(c["train"]) >= MIN_TRAIN_N)
    bonferroni = max(1, n_tests)
    results = []
    for (pattern, horizon), c in sorted(cells.items()):
        tr, ho = _stat(c["train"]), _stat(c["holdout"])
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

    if not volume_ok:
        results.append({"pattern": "P4_volume_divergence_short", "horizon": "all",
                        "verdict": "UNTESTABLE",
                        "note": "NSE index candles carry no real volume; pattern requires it"})

    report = {
        "symbol": symbol, "days": len(days_used), "cutoff_day": cutoff,
        "observations": len(observations), "bonferroni_tests": bonferroni,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS, "volume_data_present": volume_ok,
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
    print(f"=== CPR/CAMARILLA PATTERN MINER | {rep['symbol']} | {rep['days']} days | "
          f"{rep['observations']} obs | Bonferroni n={rep['bonferroni_tests']} | "
          f"volume_present={rep['volume_data_present']} ===\n")
    for r in rep["results"]:
        tr, ho = r.get("train", {}), r.get("holdout", {})
        print(f"  {r['verdict']:<20} {r['pattern']:<36} h={r['horizon']:<6} "
              f"train n={tr.get('n', 0):>4} {tr.get('mean_bps', '-'):>8}bps "
              f"p_corr={tr.get('p_corrected', '-')} | "
              f"holdout n={ho.get('n', 0):>4} {ho.get('mean_bps', '-'):>8}bps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
