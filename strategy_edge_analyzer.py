"""
strategy_edge_analyzer.py — autonomous, significance-gated edge analysis.

This is the system "learning on its own" *with a guardrail*. Every night it
measures, per strategy, what actually happened to its signals on the CLEAN
dataset (target / SL / timeout, win rate, realized P&L) and tests the FADE
(reverse) hypothesis — then GATES every finding so it never acts on noise:

  1. Minimum sample     — need >= MIN_SAMPLES outcomes (default 30).
  2. Cost-awareness     — round-trip cost subtracted from any "edge".
  3. Significance       — two-sided t-test that mean return != 0 ...
  4. Multiple testing   — ... at a Bonferroni-corrected alpha (0.05 / #strategies),
                          because testing many strategies guarantees some look
                          good by chance (the lesson of every false edge so far).

It REPORTS candidates. It does NOT auto-trade. Promotion to live still requires
a locked-holdout pass (validation_harness). A bounded down-weight hook exists but
is OFF by default (EDGE_ANALYZER_AUTOACT=false) — explore autonomously, promote
only on evidence.

Verdicts per strategy:
  INSUFFICIENT    — too few samples
  NO_EDGE         — mean return not distinguishable from 0 after correction
  FADE_CANDIDATE  — significantly NEGATIVE and fading beats cost (worth a holdout test)
  KEEP_CANDIDATE  — significantly POSITIVE net of cost (worth a holdout test)
  EDGE_BELOW_COST — significant but too small to beat costs
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

MIN_SAMPLES = int(os.getenv("EDGE_ANALYZER_MIN_SAMPLES", "30"))
COST_PCT    = float(os.getenv("EDGE_ANALYZER_COST_PCT", "0.12"))   # round-trip %
ALPHA       = float(os.getenv("EDGE_ANALYZER_ALPHA", "0.05"))
AUTO_ACT    = os.getenv("EDGE_ANALYZER_AUTOACT", "false").lower() in ("true", "1", "yes")


def _load_clean_returns(days: int = 400):
    """Load labelled signals, drop corrupt rows, return a DataFrame with a
    direction-aware realized return column `ret` (in %)."""
    import sqlite3
    import numpy as np
    import pandas as pd
    from signal_quality import clean_signal_frame

    con = sqlite3.connect("signal_log.db")
    try:
        df = pd.read_sql(
            "SELECT side, strategy, symbol, signal_date, entry_price, outcome_price, tb_label "
            "FROM signal_log WHERE tb_label IN (1,0,-1) AND training_eligible=1 "
            "AND stop_loss>0 AND target>0 AND rr>0 AND side IN ('BUY','SELL') "
            "AND entry_price > 0 AND outcome_price > 0", con)
    finally:
        con.close()

    df, report = clean_signal_frame(df)               # drop scale-mismatch corruption
    if len(df) == 0:
        return df, report
    sgn = np.where(df["side"] == "BUY", 1.0, -1.0)
    df["ret"] = sgn * (df["outcome_price"] - df["entry_price"]) / df["entry_price"] * 100.0
    return df, report


def _stat_block(ret, cost: float, alpha_corrected: float) -> Dict[str, Any]:
    """t-test that mean(ret) != 0 and derive a gated verdict."""
    import numpy as np
    from scipy import stats

    n = int(len(ret))
    mean = float(np.mean(ret))
    std = float(np.std(ret, ddof=1)) if n > 1 else 0.0
    if n < MIN_SAMPLES or std == 0:
        return {"n": n, "mean": round(mean, 4), "verdict": "INSUFFICIENT",
                "p_value": None, "as_is_net": round(mean - cost, 4),
                "fade_net": round(-mean - cost, 4)}

    t_stat, p_value = stats.ttest_1samp(ret, 0.0)
    significant = p_value < alpha_corrected
    as_is_net = mean - cost          # take the signal as-is
    fade_net  = -mean - cost         # fade it
    win_rate  = float(np.mean(np.asarray(ret) > 0))

    if not significant:
        verdict = "NO_EDGE"
    elif mean < 0 and fade_net > 0:
        verdict = "FADE_CANDIDATE"
    elif mean > 0 and as_is_net > 0:
        verdict = "KEEP_CANDIDATE"
    else:
        verdict = "EDGE_BELOW_COST"

    return {"n": n, "mean": round(mean, 4), "std": round(std, 4),
            "win_rate": round(win_rate, 4), "t_stat": round(float(t_stat), 3),
            "p_value": round(float(p_value), 5), "significant": bool(significant),
            "as_is_net": round(as_is_net, 4), "fade_net": round(fade_net, 4),
            "verdict": verdict}


def _temporal_consistent(sub) -> bool:
    """Edge sign must hold in BOTH the older and newer half of a strategy's
    signals (a cheap out-of-time check). Conservative: if too few to split,
    returns False so an in-sample-only pattern is NOT flagged as a candidate."""
    import numpy as np
    if len(sub) < 2 * MIN_SAMPLES:
        return False
    s = sub.sort_values("signal_date")
    mid = len(s) // 2
    full = np.sign(s["ret"].mean())
    older = np.sign(s["ret"].iloc[:mid].mean())
    newer = np.sign(s["ret"].iloc[mid:].mean())
    return full != 0 and older == full and newer == full


def analyze(days: int = 400, cost: float = COST_PCT) -> Dict[str, Any]:
    """Run the full autonomous edge analysis. Returns a structured report."""
    df, qc = _load_clean_returns(days)
    if df is None or len(df) == 0:
        return {"error": "no clean signals", "qc": qc}

    # strategies with enough samples define the multiple-testing family
    counts = df["strategy"].value_counts()
    strategies = [s for s, n in counts.items() if n >= MIN_SAMPLES]
    n_tests = max(1, len(strategies) + 1)            # +1 for the overall test
    alpha_corrected = ALPHA / n_tests                # Bonferroni

    tb = df["tb_label"]
    report: Dict[str, Any] = {
        "n_signals": int(len(df)),
        "corrupt_dropped": qc.get("dropped", 0),
        "cost_pct": cost,
        "n_tests": n_tests,
        "alpha_corrected": round(alpha_corrected, 5),
        "outcomes": {"target": int((tb == 1).sum()),
                     "sl": int((tb == -1).sum()),
                     "timeout": int((tb == 0).sum())},
        "overall": _stat_block(df["ret"].values, cost, alpha_corrected),
        "per_strategy": {},
    }
    for s in strategies:
        sub = df[df["strategy"] == s]
        block = _stat_block(sub["ret"].values, cost, alpha_corrected)
        # Out-of-time stability gate: a would-be candidate must hold across
        # both time halves, else it is an in-sample-only artifact.
        if block["verdict"] in ("FADE_CANDIDATE", "KEEP_CANDIDATE"):
            if not _temporal_consistent(sub):
                block["note"] = "edge not stable across time halves"
                block["verdict"] = "UNSTABLE_OOS"
        report["per_strategy"][s] = block

    # candidates that survived ALL gates
    cands = {s: b for s, b in report["per_strategy"].items()
             if b["verdict"] in ("FADE_CANDIDATE", "KEEP_CANDIDATE")}
    report["candidates"] = cands
    report["conclusion"] = (
        "NO significant edge survives cost + multiple-testing — explore further, "
        "promote nothing." if not cands else
        f"{len(cands)} candidate(s) survived gating — REQUIRE locked-holdout "
        "validation before any live use.")
    return report


def format_report(rep: Dict[str, Any]) -> str:
    if "error" in rep:
        return f"edge analyzer: {rep['error']}"
    o = rep["outcomes"]
    lines = [
        "🔬 <b>Autonomous Edge Analysis</b>",
        f"signals={rep['n_signals']} (dropped {rep['corrupt_dropped']} corrupt) "
        f"cost={rep['cost_pct']}%  tests={rep['n_tests']} α={rep['alpha_corrected']}",
        f"outcomes: target={o['target']} SL={o['sl']} timeout={o['timeout']}",
        f"overall: mean={rep['overall']['mean']}% → {rep['overall']['verdict']}",
        "",
    ]
    rows = sorted(rep["per_strategy"].items(), key=lambda kv: kv[1]["mean"])
    for s, b in rows:
        flag = "⚠️" if b["verdict"] in ("FADE_CANDIDATE", "KEEP_CANDIDATE") else "  "
        lines.append(f"{flag} {s[:28]:28s} n={b['n']:4d} mean={b['mean']:+.3f}% "
                     f"p={b.get('p_value')} → {b['verdict']}")
    lines += ["", f"<b>{rep['conclusion']}</b>"]
    return "\n".join(lines)


def run_nightly(send_telegram: bool = False) -> Dict[str, Any]:
    """Entry point for the post-market pipeline. Reports; acts only if AUTO_ACT."""
    rep = analyze()
    logger.info("edge analyzer: %s", rep.get("conclusion", rep.get("error")))
    if AUTO_ACT and rep.get("candidates"):
        # Bounded, conservative: candidates would be fed to learned_filters/weights
        # here — but ONLY after a locked-holdout pass. Left as an explicit no-op so
        # autonomy never trades on un-validated patterns.
        logger.warning("edge analyzer: AUTO_ACT on, but promotion still requires "
                       "holdout validation — no live change applied.")
    return rep


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = analyze()
    print(format_report(rep).replace("<b>", "").replace("</b>", ""))
