"""
option_signal_research_ledger.py — forward-holdout tracking for option-bot
signal research candidates that don't fit the existing rule/cohort ledgers
(2026-07-15). Same discipline as learned_filter_ledger.json on the equity
side and the pairs scan: a candidate is discovered on historical data, its
exact definition is FROZEN at discovery time, and it is only ever judged
on data with snapshot_time strictly AFTER its own discovery date. Nothing
here is promoted automatically — this reports a verdict, a human decides
what to do with it, same as every other gate in this project.

CANDIDATE 1 — "score_inverse_3hr": the option bot's own confidence score
is inversely, sign-stably related to the 3-hour forward return in the
signal's own stated direction (found 2026-07-15 via
option_decomposition_followups.py): low-score tercile +2.06bps/54.3% win,
high-score tercile -4.47bps/45.2% win on the DISCOVERY sample (all data
up to and including 2026-07-14). Gap ~6.5bps, close to this pipeline's
own measured ~10bps sensitivity floor (pipeline_sensitivity_floor.py) --
real and sign-stable in the discovery-period train/holdout split, but NOT
yet confirmed on data the discovery process never touched. That is what
this ledger accrues.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from option_underlying_decomposition import _load_candles, _parse_snapshot_time, _forward_return, _stat

logger = logging.getLogger(__name__)

LEDGER_FILE = Path("option_signal_research_ledger.json")
SNAPSHOT_DB = "option_chain_snapshots.db"
MIN_FORWARD_N_PER_TERCILE = 50   # per tercile, before attempting a verdict
MIN_FORWARD_DAYS = 5

CANDIDATES: Dict[str, Dict[str, Any]] = {
    "score_inverse_3hr": {
        "hypothesis": "High-score option-bot signals underperform low-score "
                      "signals at a 3-hour horizon, in the signal's own "
                      "stated direction (contrarian read of the confidence score).",
        "discovered_at": "2026-07-15",
        "underlyings": ("NIFTY", "BANKNIFTY", "FINNIFTY"),
        "horizon_min": 180,
        # FROZEN at discovery — computed once from all data through
        # 2026-07-14 (option_decomposition_followups.py tercile split).
        # Never recomputed from later data; that would be data-snooping.
        "score_lo_cut": 19.96,
        "score_hi_cut": 40.38,
        "discovery_sample": {
            "low_tercile":  {"n": 2713, "mean_bps": 2.06, "win_rate": 0.543},
            "high_tercile": {"n": 2712, "mean_bps": -4.47, "win_rate": 0.452},
        },
    },
}


def _load_scored_observations(underlyings, min_date: str) -> List[Dict[str, Any]]:
    with sqlite3.connect(SNAPSHOT_DB) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT underlying, snapshot_time, direction, score
                  FROM option_strike_signals
                 WHERE underlying IN ({','.join('?' for _ in underlyings)})
                   AND score > 0 AND snapshot_time > ?
                 ORDER BY underlying, snapshot_time""",
            (*underlyings, min_date),
        ).fetchall()
    return [{"underlying": r[0], "snapshot_time": r[1], "direction": r[2], "score": float(r[3])}
            for r in rows]


def check_candidate(name: str) -> Dict[str, Any]:
    cfg = CANDIDATES[name]
    obs = _load_scored_observations(cfg["underlyings"], cfg["discovered_at"])
    candle_cache = {u: _load_candles(u) for u in cfg["underlyings"]}

    low_rets, high_rets = [], []
    days_seen = set()
    for o in obs:
        try:
            entry_ts = _parse_snapshot_time(o["snapshot_time"])
        except Exception:
            continue
        ret = _forward_return(candle_cache[o["underlying"]], entry_ts, cfg["horizon_min"])
        if ret is None:
            continue
        sign = 1.0 if o["direction"] == "BULLISH" else -1.0
        signed = sign * ret
        days_seen.add(o["snapshot_time"][:10])
        if o["score"] <= cfg["score_lo_cut"]:
            low_rets.append(signed)
        elif o["score"] >= cfg["score_hi_cut"]:
            high_rets.append(signed)

    low_stat, high_stat = _stat(low_rets), _stat(high_rets)
    forward_days = len(days_seen)
    enough = (low_stat.get("n", 0) >= MIN_FORWARD_N_PER_TERCILE
              and high_stat.get("n", 0) >= MIN_FORWARD_N_PER_TERCILE
              and forward_days >= MIN_FORWARD_DAYS)

    if not enough:
        verdict = "ACCRUING"
    else:
        gap = low_stat["mean_bps"] - high_stat["mean_bps"]
        discovery_gap = (cfg["discovery_sample"]["low_tercile"]["mean_bps"]
                         - cfg["discovery_sample"]["high_tercile"]["mean_bps"])
        verdict = "CONFIRMED" if gap > 0 and discovery_gap > 0 else "REJECTED"

    return {
        "candidate": name, "discovered_at": cfg["discovered_at"],
        "forward_days": forward_days, "verdict": verdict,
        "forward_low_tercile": low_stat, "forward_high_tercile": high_stat,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def _catalog_combo_evidence() -> Dict[str, Any]:
    """Combo-level (not per-leg) promotion status for every implemented
    option-catalog strategy — the observation unit is one combo_id, so a
    4-leg condor counts once. Guarded: absent modules or an unmigrated
    schema simply yield an empty dict."""
    out: Dict[str, Any] = {}
    try:
        from option_live_edge_policy import strategy_combo_policy
        from option_strategy_registry import implemented_ids
        with sqlite3.connect(SNAPSHOT_DB) as conn:
            for sid in implemented_ids():
                out[sid] = strategy_combo_policy(conn, strategy=sid)
    except Exception as exc:
        logger.debug("catalog combo evidence: %s", exc)
    return out


def run_all() -> Dict[str, Any]:
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "candidates": {name: check_candidate(name) for name in CANDIDATES},
              "catalog_strategies_combo_unit": _catalog_combo_evidence()}
    try:
        LEDGER_FILE.write_text(json.dumps(report, indent=2))
    except Exception as exc:
        logger.debug("ledger write failed: %s", exc)
    return report


def main() -> int:
    report = run_all()
    print("=== OPTION SIGNAL RESEARCH LEDGER ===\n")
    for name, r in report["candidates"].items():
        print(f"{name}: verdict={r['verdict']} forward_days={r['forward_days']} "
              f"(need >= {MIN_FORWARD_DAYS})")
        print(f"  forward low tercile:  n={r['forward_low_tercile'].get('n', 0)} "
              f"{r['forward_low_tercile'].get('mean_bps')}bps")
        print(f"  forward high tercile: n={r['forward_high_tercile'].get('n', 0)} "
              f"{r['forward_high_tercile'].get('mean_bps')}bps")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
