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
from datetime import date, datetime
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Focused watchlist for the nightly pairs re-test (the ones that showed any
# cointegration / structural link) — keeps the nightly fetch light. Run the full
# 20-pair scan manually with `python pairs_stat_arb_validation.py`.
PAIR_WATCHLIST = [
    ("BPCL", "IOC"), ("MARUTI", "M&M"), ("ULTRACEMCO", "GRASIM"),
    ("SUNPHARMA", "DRREDDY"), ("TCS", "INFY"), ("HINDALCO", "JSWSTEEL"),
]
MIN_N    = 150      # min labelled signals before a keep/prune verdict is "confirmed"
# Canonical gate — same constant every status surface quotes (signal_log is the
# source of truth; fallback keeps the monitor standalone-safe).
try:
    from signal_log import EDGE_GATE_DAYS as MIN_DAYS
except Exception:
    MIN_DAYS = 8    # min DISTINCT strict days — 5 correlated days once faked a pocket
PRUNE_R  = -0.03    # NET-of-cost avg-R at/below this = prune candidate
KEEP_R   = 0.10     # NET-of-cost avg-R at/above this = keep candidate
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


def _strategy_ranking() -> Dict[str, Any]:
    """Rank strategies by NET-of-cost R with a day-diversity gate.

    2026-07-03 diagnostic: ranking by PRE-COST tb_r_multiple overstated every
    strategy (gross R ~0, net -0.18), and 5 correlated days once made a pocket
    look real. CONFIRMED now requires n>=MIN_N AND distinct days>=MIN_DAYS AND is
    judged on tb_r_multiple_net (costs are the one certain number). Gross avg-R
    is kept per-row for context.
    """
    try:
        con = sqlite3.connect("file:signal_log.db?mode=ro", uri=True)
        rows = con.execute(
            "SELECT strategy, COUNT(*) n, AVG(tb_r_multiple) avg_R, "
            "AVG(tb_r_multiple_net) net_R, COUNT(DISTINCT signal_date) days, "
            "100.0*SUM(CASE WHEN tb_label=1 THEN 1 ELSE 0 END)/COUNT(*) win "
            "FROM signal_log WHERE tb_label IN (-1,0,1) AND training_eligible=1 "
            "AND stop_loss>0 AND target>0 AND rr>0 AND strategy!='' "
            "GROUP BY strategy HAVING n>=15"
        ).fetchall()
        con.close()
    except Exception as exc:
        logger.debug("strategy ranking: %s", exc)
        return {"all": [], "keep": [], "prune": [], "max_days": 0}
    allr = sorted(
        ((s, int(n), round(a or 0, 3), round(nr or 0, 3), int(d), round(w, 1))
         for s, n, a, nr, d, w in rows),
        key=lambda x: x[3], reverse=True)                       # rank by NET R
    max_days = max((r[4] for r in allr), default=0)
    confirmed = [r for r in allr if r[1] >= MIN_N and r[4] >= MIN_DAYS]
    keep  = [r for r in confirmed if r[3] >= KEEP_R]            # net-R thresholds
    prune = [r for r in confirmed if r[3] <= PRUNE_R]
    return {"all": allr, "keep": keep, "prune": prune, "max_days": max_days}


def _write_kp_report(sr: Dict[str, Any]) -> None:
    try:
        def fmt(b):
            return "\n".join(
                f"  {s:38s} n={n:<4} days={d:<2} netR={nr:+.3f} (gross {a:+.3f}) win={w}%"
                for s, n, a, nr, d, w in b)
        txt = (f"# Strategy keep/prune — {date.today()} (nightly, NET-of-cost R)\n\n"
               f"Ranked by tb_r_multiple_net (costs+slippage included). CONFIRMED requires "
               f"n>={MIN_N} AND distinct days>={MIN_DAYS} (max days so far: {sr.get('max_days', 0)}).\n"
               f"Rigorous gate is validation_harness (DSR). Reversible prune via pruning.py.\n\n"
               f"## CONFIRMED KEEP (net-R>={KEEP_R}, n>={MIN_N}, days>={MIN_DAYS})\n{fmt(sr['keep']) or '  (none yet)'}\n\n"
               f"## CONFIRMED PRUNE (net-R<={PRUNE_R}, n>={MIN_N}, days>={MIN_DAYS})\n{fmt(sr['prune']) or '  (none yet)'}\n\n"
               f"## ALL (ranked by net-R)\n{fmt(sr['all'])}\n")
        open(KP_REPORT, "w").write(txt)
    except Exception as exc:
        logger.debug("kp report write: %s", exc)


def _suggest_prunes(sr: Dict[str, Any]) -> int:
    """Merge CONFIRMED net-negative strategies into prune_suggestions.json
    (SUGGESTIONS ONLY — pruning.py promotes to pruned.json deliberately;
    never auto-disables). Returns number of suggested strategies."""
    prune_names = sorted({r[0] for r in sr.get("prune", [])})
    if not prune_names:
        return 0
    try:
        path = "prune_suggestions.json"
        try:
            data = json.load(open(path))
        except Exception:
            data = {}
        existing = set(data.get("strategies") or [])
        merged = sorted(existing | set(prune_names))
        if merged != sorted(existing):
            data["strategies"] = merged
            data["generated"] = datetime.now().isoformat(timespec="seconds")
            data.setdefault("note", "SUGGESTIONS ONLY — promote to pruned.json deliberately, data-gated")
            json.dump(data, open(path, "w"), indent=2)
            logger.info("prune_suggestions.json: +%d strategy suggestion(s)", len(merged) - len(existing))
        return len(merged)
    except Exception as exc:
        logger.debug("suggest prunes: %s", exc)
        return 0


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
    n_suggested = _suggest_prunes(sr)
    out["strategy"] = {"total": len(sr["all"]), "confirmed_keep": len(sr["keep"]),
                       "confirmed_prune": len(sr["prune"]),
                       "max_days": sr.get("max_days", 0),
                       "prune_suggestions": n_suggested}

    # DECISION-POINT alert (once): the day-diversity gate is now satisfiable —
    # keep/prune verdicts become statistically meaningful. This is the trigger
    # to run the pruning review instead of guessing early.
    if sr.get("max_days", 0) >= MIN_DAYS and (sr["keep"] or sr["prune"]):
        am = _alerts()
        if am:
            try:
                am.send(
                    "📊 <b>EDGE REVIEW READY</b> — day gate crossed\n"
                    f"  {sr.get('max_days')} distinct days; CONFIRMED keep={len(sr['keep'])} "
                    f"prune={len(sr['prune'])} (net-of-cost R, n>={MIN_N}, days>={MIN_DAYS})\n"
                    f"  See strategy_keep_prune_candidates.md + prune_suggestions.json\n"
                    f"  Apply deliberately via pruning.py (reversible).",
                    dedup_key="edgemon_review_ready", dedup_cooldown_override=7 * 86400)
            except Exception as exc:
                logger.debug("review-ready alert: %s", exc)

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
