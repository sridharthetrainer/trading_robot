#!/usr/bin/env python3
"""
nightly_edge_monitor.py — re-test the edge candidates each night and alert ONLY on
a real crossing, so you stop manually hunting and let the machine flag a genuine
edge if/when one appears.

What it does (best-effort, never raises into the scheduler):
  1. Pairs stat-arb scan (pairs_stat_arb_validation.scan_pairs) → saves the report;
     ALERTS only if a pair reaches verdict=PASS (cointegrated + OOS edge after costs).
  2. Strategy keep/prune ranking from triple-barrier labels (tb_label / tb_r_multiple)
     → regenerates strategy_keep_prune_candidates.md. CONFIRMED prune candidates
     (avg-R <= PRUNE_R over >= MIN_N samples) and keep candidates (avg-R >= KEEP_R).
     Saved for review; NOT alerted nightly (would spam) — only the PASS event alerts.

Wired into the idle-engine nightly schedule.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Focused watchlist for the nightly pairs re-test (the ones that showed any
# cointegration / structural link) — keeps the nightly fetch light. Run the full
# 20-pair scan manually with `python pairs_stat_arb_validation.py`.
PAIR_WATCHLIST = [
    ("BPCL", "IOC"), ("MARUTI", "M&M"), ("ULTRACEMCO", "GRASIM"),
    ("SUNPHARMA", "DRREDDY"), ("TCS", "INFY"), ("HINDALCO", "JSWSTEEL"),
]
MIN_N   = 150       # min labelled signals before a keep/prune verdict is "confirmed"
PRUNE_R = -0.03     # avg-R at/below this (after enough samples) = prune candidate
KEEP_R  = 0.10      # avg-R at/above this = keep candidate
PAIRS_REPORT = "pairs_validation_report.json"
KP_REPORT    = "strategy_keep_prune_candidates.md"


def _alerts():
    try:
        import os
        from alerts import AlertManager
        return AlertManager(bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""))
    except Exception:
        return None


def _strategy_ranking() -> Dict[str, List[Tuple]]:
    try:
        con = sqlite3.connect("file:signal_log.db?mode=ro", uri=True)
        rows = con.execute(
            "SELECT strategy, COUNT(*) n, AVG(tb_r_multiple) avg_R, "
            "100.0*SUM(CASE WHEN tb_label=1 THEN 1 ELSE 0 END)/COUNT(*) win "
            "FROM signal_log WHERE tb_label IN (-1,0,1) AND training_eligible=1 "
            "AND stop_loss>0 AND target>0 AND rr>0 AND strategy!='' "
            "GROUP BY strategy HAVING n>=15"
        ).fetchall()
        con.close()
    except Exception as exc:
        logger.debug("strategy ranking: %s", exc)
        return {"all": [], "keep": [], "prune": []}
    allr = sorted(((s, int(n), round(a or 0, 3), round(w, 1)) for s, n, a, w in rows),
                  key=lambda x: x[2], reverse=True)
    keep  = [r for r in allr if r[1] >= MIN_N and r[2] >= KEEP_R]
    prune = [r for r in allr if r[1] >= MIN_N and r[2] <= PRUNE_R]
    return {"all": allr, "keep": keep, "prune": prune}


def _write_kp_report(sr: Dict[str, List[Tuple]]) -> None:
    try:
        def fmt(b):
            return "\n".join(f"  {s:38s} n={n:<4} avgR={a:+.3f} win={w}%" for s, n, a, w in b)
        txt = (f"# Strategy keep/prune — {date.today()} (nightly, R-weighted)\n\n"
               f"PRE-COST, asymmetric barriers bias avg-R +. CONFIRMED = n>={MIN_N}.\n"
               f"Rigorous gate is validation_harness (DSR). Reversible prune via pruning.py.\n\n"
               f"## CONFIRMED KEEP (avg-R>={KEEP_R}, n>={MIN_N})\n{fmt(sr['keep']) or '  (none yet)'}\n\n"
               f"## CONFIRMED PRUNE (avg-R<={PRUNE_R}, n>={MIN_N})\n{fmt(sr['prune']) or '  (none yet)'}\n\n"
               f"## ALL (ranked by avg-R)\n{fmt(sr['all'])}\n")
        open(KP_REPORT, "w").write(txt)
    except Exception as exc:
        logger.debug("kp report write: %s", exc)


def run() -> Dict[str, Any]:
    """Nightly entrypoint. Best-effort."""
    out: Dict[str, Any] = {"date": date.today().isoformat()}

    passed: List[str] = []
    try:
        import pairs_stat_arb_validation as pv
        pres = pv.scan_pairs(PAIR_WATCHLIST)
        json.dump(pres, open(PAIRS_REPORT, "w"), indent=2)
        passed = [r.get("pair") for r in pres.get("ranked", []) if r.get("verdict") == "PASS"]
        out["pairs"] = {"scanned": pres.get("scanned"), "cointegrated": pres.get("cointegrated"),
                        "passed": passed}
    except Exception as exc:
        logger.debug("pairs monitor: %s", exc)
        out["pairs_error"] = str(exc)[:80]

    sr = _strategy_ranking()
    _write_kp_report(sr)
    out["strategy"] = {"total": len(sr["all"]), "confirmed_keep": len(sr["keep"]),
                       "confirmed_prune": len(sr["prune"])}

    # ALERT ONLY on a genuine crossing: a pair passing validation.
    if passed:
        am = _alerts()
        if am:
            try:
                am.send("🟢 <b>EDGE MONITOR — validated pair(s)!</b>\n  " + ", ".join(passed)
                        + "\n  (cointegrated + OOS edge after costs — review before any live use)",
                        dedup_key=f"edgemon_pass_{date.today()}", dedup_cooldown_override=86400)
            except Exception:
                pass
    logger.info("Edge monitor: pairs passed=%d | confirmed keep=%d prune=%d",
                len(passed), len(sr["keep"]), len(sr["prune"]))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    print(json.dumps(run(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
