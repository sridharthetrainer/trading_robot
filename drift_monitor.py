"""
drift_monitor.py — ongoing drift detection for strategies and modifiers already
in production.

strategy_edge_analyzer.py and modifier_edge_analyzer.py each answer "does this
show edge, as measured once, right now" — including a one-time forward-holdout
check for anything that looks like a new candidate. Neither keeps watching
something that already passed: if a strategy's edge quietly decays after it's
already been considered acceptable, nothing catches it until someone happens to
re-run a manual holdout check. That's exactly what happened this session:
sma20_atm_option and di_momentum_call both looked profitable over their full
history and were significantly NEGATIVE in just the most recent 30% of trades —
found only because a holdout split was run by hand.

This module automates that check, on a rolling basis, for whatever is currently
in signal_log: split each strategy's (and each logged modifier's) history into
a REFERENCE period (everything before the recent window) and a RECENT period
(the last `DRIFT_RECENT_DAYS` calendar days), then test whether RECENT is both
significantly worse AND distributionally different from REFERENCE — same
significance + multiple-testing discipline as the other two analyzers.

It REPORTS ONLY. It does not call pruning.py, does not modify pruned.json, and
does not touch autonomous_edge_policy.py's automatic state machine — promotion/
demotion stays exactly where it already lives. This is a complementary early
warning, not a new actor (per CLAUDE.md: AI must not self-authorize changes to
what's live).

Scope note: only DEGRADATION is flagged (recent significantly worse than
reference). A significant IMPROVEMENT is left as STABLE — informationally
interesting but not a risk signal, and out of scope for what this module is
for; a future session could add it as a separate, explicitly-named verdict.

Verdicts per strategy/modifier:
  STABLE             — recent-window performance not distinguishable from reference
  DRIFT_SUSPECTED     — recent-window mean AND distribution both diverge significantly
                        from reference, in the WORSE direction
  INSUFFICIENT_DATA   — fewer than MIN_SAMPLES observations in reference or recent window
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

MIN_SAMPLES = int(os.getenv("DRIFT_MIN_SAMPLES", "30"))
RECENT_DAYS = int(os.getenv("DRIFT_RECENT_DAYS", "30"))
ALPHA       = float(os.getenv("DRIFT_ALPHA", "0.05"))
COST_PCT    = float(os.getenv("EDGE_ANALYZER_COST_PCT", "0.12"))
EPS         = float(os.getenv("MOD_EDGE_EPS", "0.05"))   # matches modifier_edge_analyzer

# CUSUM sequential test: MIN_SAMPLES-gated calendar windows above never
# produce a verdict for sparse strategies until BOTH a 30-sample reference
# AND a separate 30-sample recent window fill up -- for a strategy firing a
# few times a month, that's "INSUFFICIENT_DATA forever", the same failure
# mode already named for validation in this session's MDE work. CUSUM instead
# only needs a smaller bootstrap reference, then accumulates evidence trade
# by trade as they arrive -- it flags (or doesn't) whenever enough evidence
# exists, however long that takes, rather than waiting for two fixed windows.
CUSUM_MIN_REFERENCE = int(os.getenv("DRIFT_CUSUM_MIN_REFERENCE", "15"))
CUSUM_K_MULT = float(os.getenv("DRIFT_CUSUM_K_MULT", "0.5"))   # allowance, in reference std
CUSUM_H_MULT = float(os.getenv("DRIFT_CUSUM_H_MULT", "5.0"))   # decision boundary, in reference std

# Reuse the exact same modifier column list as modifier_edge_analyzer.py, imported
# directly rather than copy-pasted, so the two never silently drift apart.
from modifier_edge_analyzer import _MOD_COLS as _MODIFIER_COLS


def _load_clean(days: int = 400):
    """Labelled signals with a direction-aware realized return `ret` (%) plus the
    logged modifier columns. Same query shape as strategy_edge_analyzer.py /
    modifier_edge_analyzer.py, combined so both drift checks share one load."""
    import sqlite3
    import numpy as np
    import pandas as pd
    from signal_quality import clean_signal_frame

    con = sqlite3.connect("signal_log.db")
    try:
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_log)").fetchall()}
        present_mods = [c for c in _MODIFIER_COLS if c in existing]
        cols = (", " + ", ".join(present_mods)) if present_mods else ""
        df = pd.read_sql(
            f"SELECT side, strategy, signal_date, entry_price, outcome_price, "
            f"tb_label{cols} FROM signal_log "
            "WHERE tb_label IN (1,0,-1) AND training_eligible=1 "
            "AND stop_loss>0 AND target>0 AND rr>0 AND side IN ('BUY','SELL') "
            "AND entry_price > 0 AND outcome_price > 0", con)
    finally:
        con.close()

    df, report = clean_signal_frame(df)
    if len(df) == 0:
        return df, report
    sgn = np.where(df["side"] == "BUY", 1.0, -1.0)
    df["ret"] = sgn * (df["outcome_price"] - df["entry_price"]) / df["entry_price"] * 100.0
    return df, report


def _split_reference_recent(df, recent_days: int):
    """Chronological split by DISTINCT calendar day (not row count, so one
    signal-dense day can't dominate a half — same reasoning as
    strategy_edge_analyzer._holdout_check). reference = everything strictly
    before the cutoff day; recent = the last `recent_days` distinct days."""
    days_sorted = sorted(df["signal_date"].unique())
    if len(days_sorted) <= recent_days:
        return df.iloc[0:0], df    # not enough history for a reference period
    cutoff_day = days_sorted[-recent_days]
    reference = df[df["signal_date"] < cutoff_day]
    recent = df[df["signal_date"] >= cutoff_day]
    return reference, recent


def _drift_block(ref_ret, recent_ret, alpha_corrected: float) -> Dict[str, Any]:
    """Welch t-test on the mean + two-sample KS test on the full distribution,
    comparing recent vs reference. Both must be significant, in the worse
    direction, to flag drift -- a mean-only shift can be one outlier trade;
    a distribution-only shift without a mean move isn't necessarily harmful."""
    import numpy as np
    from scipy import stats

    n_ref, n_recent = int(len(ref_ret)), int(len(recent_ret))
    if n_ref < MIN_SAMPLES or n_recent < MIN_SAMPLES:
        return {"n_reference": n_ref, "n_recent": n_recent, "verdict": "INSUFFICIENT_DATA"}

    ref_mean, recent_mean = float(np.mean(ref_ret)), float(np.mean(recent_ret))
    t_stat, t_p = stats.ttest_ind(recent_ret, ref_ret, equal_var=False)  # Welch
    ks_stat, ks_p = stats.ks_2samp(recent_ret, ref_ret)
    mean_worse_significant = (t_p < alpha_corrected) and (recent_mean < ref_mean)
    dist_significant = ks_p < alpha_corrected

    block = {
        "n_reference": n_ref, "n_recent": n_recent,
        "reference_mean": round(ref_mean, 4), "recent_mean": round(recent_mean, 4),
        "recent_net_of_cost": round(recent_mean - COST_PCT, 4),
        "t_stat": round(float(t_stat), 3), "t_p_value": round(float(t_p), 5),
        "ks_stat": round(float(ks_stat), 4), "ks_p_value": round(float(ks_p), 5),
        "verdict": "DRIFT_SUSPECTED" if (mean_worse_significant and dist_significant) else "STABLE",
    }
    return block


CUSUM_H_REFERENCE_N = int(os.getenv("DRIFT_CUSUM_H_REFERENCE_N", "30"))

def cusum_check(returns_chronological, min_reference: int = CUSUM_MIN_REFERENCE,
                 k_mult: float = CUSUM_K_MULT, h_mult: float = CUSUM_H_MULT) -> Dict[str, Any]:
    """One-sided CUSUM for a DOWNWARD shift in mean return, run over a single
    chronologically-ordered series (no separate reference/recent windows).

    Bootstraps a TARGET MEAN from the first `min_reference` observations, then
    walks the REMAINING observations in order accumulating:
        C_t = min(0, C_{t-1} + (x_t - ref_mean) + k)
    where k = k_mult * noise_std is the allowance (how large a shift to
    tolerate as noise) and h is the decision boundary. Flags the FIRST index
    (if any) where C_t drops below -h.

    `noise_std` is estimated from the FULL series, not the small reference
    window -- verified against real signal_log data that using only the
    min_reference-sized window for std makes k/h depend on whatever that tiny
    sample happened to draw (a 15-point window drew std=0.58 against the true
    full-series std of 1.14 on real data).

    `h` SCALES WITH sqrt(n_evaluated) rather than being a fixed multiple of
    noise_std -- this is the second, more important calibration fix. A fixed
    h = h_mult * noise_std has a bounded "average run length" under the null
    (no real drift): verified via positive control (pure-noise simulation)
    that a fixed h=5*sigma gives an acceptable ~6.5% false-alarm rate at
    n_evaluated=30, but climbs to 24.5% at n=100, 46.5% at n=500, and 75.5%
    at n=3500 -- strategies with thousands of logged signals were flagging as
    DRIFT_SUSPECTED from ordinary noise almost every time, not real
    degradation, because a long enough random walk will eventually cross any
    fixed finite boundary. Scaling h by sqrt(n_evaluated / CUSUM_H_REFERENCE_N)
    holds the false-alarm rate roughly constant (~3-7%) from n_evaluated=30
    up through 3500+ in the same positive-control test -- same principle as
    the Bonferroni correction used everywhere else in this pipeline (control
    the rate of false positives across however many "looks" are actually
    being taken), just applied to a sequential test's boundary instead of a
    fixed test's alpha.

    Needs only `min_reference` (default 15) observations for the target
    mean, then evaluates every new trade as it arrives -- a strategy firing
    2x/week still gets a real, continuously-updated answer, unlike the
    calendar-window check which needs 2x30=60 total observations split into
    two disjoint windows before it can say anything at all.
    """
    import numpy as np

    arr = np.asarray(returns_chronological, dtype=float)
    n = len(arr)
    if n <= min_reference:
        return {"n_total": n, "n_reference": min(n, min_reference),
                "verdict": "INSUFFICIENT_DATA",
                "reason": f"n={n} <= min_reference={min_reference}, no room to accumulate evidence yet"}

    ref_mean = float(arr[:min_reference].mean())
    noise_std = float(arr.std(ddof=1))   # full-series std -- see docstring
    if noise_std <= 0:
        return {"n_total": n, "n_reference": min_reference, "verdict": "INSUFFICIENT_DATA",
                "reason": "series has zero variance -- cannot set a boundary"}
    n_evaluated_for_scale = max(1, n - min_reference)
    h_mult = h_mult * (n_evaluated_for_scale / CUSUM_H_REFERENCE_N) ** 0.5

    k = k_mult * noise_std
    h = h_mult * noise_std

    c = 0.0
    flagged_at = None
    for i, x in enumerate(arr[min_reference:]):
        c = min(0.0, c + (x - ref_mean) + k)
        if c <= -h and flagged_at is None:
            flagged_at = min_reference + i   # index into arr, 0-based

    n_evaluated = n - min_reference
    verdict = "DRIFT_SUSPECTED" if flagged_at is not None else "STABLE"
    return {
        "n_total": n, "n_reference": min_reference, "n_evaluated": n_evaluated,
        "reference_mean": round(ref_mean, 4), "noise_std": round(noise_std, 4),
        "k": round(k, 4), "h": round(h, 4), "final_cusum": round(c, 4),
        "flagged_at_index": flagged_at, "verdict": verdict,
    }


def analyze(days: int = 400, recent_days: int = RECENT_DAYS) -> Dict[str, Any]:
    """Run drift detection across every strategy and modifier with enough
    volume to test. Returns a structured report."""
    df, qc = _load_clean(days)
    if df is None or len(df) == 0:
        return {"error": "no clean signals", "qc": qc}

    counts = df["strategy"].value_counts()
    strategies = [s for s, n in counts.items() if n >= 2 * MIN_SAMPLES]
    # CUSUM needs far less to start (CUSUM_MIN_REFERENCE + >=1 evaluated
    # point) than the calendar check's 2*MIN_SAMPLES -- this is deliberately
    # a BROADER population, or sparse strategies excluded from `strategies`
    # above would never get evaluated by anything at all.
    cusum_strategies = [s for s, n in counts.items() if n >= CUSUM_MIN_REFERENCE + 5]
    present_mods = [c for c in _MODIFIER_COLS if c in df.columns]
    n_tests = max(1, len(strategies) + len(present_mods))
    alpha_corrected = ALPHA / n_tests   # Bonferroni, same convention as the other two analyzers

    report: Dict[str, Any] = {
        "n_signals": int(len(df)),
        "corrupt_dropped": qc.get("dropped", 0),
        "recent_days": recent_days,
        "n_tests": n_tests,
        "alpha_corrected": round(alpha_corrected, 6),
        "per_strategy": {},
        "per_modifier": {},
        "per_strategy_cusum": {},
    }

    for s in strategies:
        sub = df[df["strategy"] == s].sort_values("signal_date")
        ref, recent = _split_reference_recent(sub, recent_days)
        report["per_strategy"][s] = _drift_block(
            ref["ret"].values, recent["ret"].values, alpha_corrected)

    for col in present_mods:
        # Drift on the ENDORSED group's own mean over time (did this modifier's
        # endorsed signals get worse), not endorsed-vs-silent (that's what
        # modifier_edge_analyzer.py already measures).
        endorsed = df[df[col] > EPS].sort_values("signal_date")
        ref, recent = _split_reference_recent(endorsed, recent_days)
        report["per_modifier"][col] = _drift_block(
            ref["ret"].values, recent["ret"].values, alpha_corrected)

    for s in cusum_strategies:
        sub = df[df["strategy"] == s].sort_values("signal_date")
        report["per_strategy_cusum"][s] = cusum_check(sub["ret"].values)

    flagged_strats = [s for s, b in report["per_strategy"].items()
                       if b["verdict"] == "DRIFT_SUSPECTED"]
    flagged_mods = [c for c, b in report["per_modifier"].items()
                     if b["verdict"] == "DRIFT_SUSPECTED"]
    flagged_cusum = [s for s, b in report["per_strategy_cusum"].items()
                      if b["verdict"] == "DRIFT_SUSPECTED"]
    report["summary"] = {"drifting_strategies": flagged_strats, "drifting_modifiers": flagged_mods,
                          "drifting_strategies_cusum": flagged_cusum}
    report["conclusion"] = (
        "No drift detected — recent performance consistent with history."
        if not (flagged_strats or flagged_mods or flagged_cusum) else
        f"DRIFT SUSPECTED: strategies={flagged_strats or '—'} "
        f"modifiers={flagged_mods or '—'} cusum={flagged_cusum or '—'}. "
        "Report only — review manually before any pruned.json change.")
    return report


def format_report(rep: Dict[str, Any]) -> str:
    if "error" in rep:
        return f"drift monitor: {rep['error']}"
    lines = [
        "📉 <b>Drift Monitor</b>",
        f"signals={rep['n_signals']} (dropped {rep['corrupt_dropped']} corrupt) "
        f"recent_window={rep['recent_days']}d  tests={rep['n_tests']} α={rep['alpha_corrected']}",
        "",
    ]
    for label, section in (("strategy", rep["per_strategy"]), ("modifier", rep["per_modifier"])):
        rows = sorted(section.items(), key=lambda kv: kv[1].get("recent_mean", 0.0))
        for name, b in rows:
            flag = "⚠️" if b["verdict"] == "DRIFT_SUSPECTED" else "  "
            if b["verdict"] == "INSUFFICIENT_DATA":
                lines.append(f"{flag} [{label}] {name:24s} n_ref={b['n_reference']:4d} "
                             f"n_recent={b['n_recent']:4d} → INSUFFICIENT_DATA")
            else:
                lines.append(f"{flag} [{label}] {name:24s} ref={b['reference_mean']:+.3f}% "
                             f"recent={b['recent_mean']:+.3f}% t_p={b['t_p_value']} "
                             f"ks_p={b['ks_p_value']} → {b['verdict']}")
    if rep.get("per_strategy_cusum"):
        lines.append("")
        for name, b in sorted(rep["per_strategy_cusum"].items()):
            flag = "⚠️" if b["verdict"] == "DRIFT_SUSPECTED" else "  "
            if b["verdict"] == "INSUFFICIENT_DATA":
                lines.append(f"{flag} [cusum] {name:24s} n_total={b['n_total']:4d} → INSUFFICIENT_DATA")
            else:
                lines.append(f"{flag} [cusum] {name:24s} n_total={b['n_total']:4d} "
                              f"final_cusum={b['final_cusum']} → {b['verdict']}")
    lines += ["", f"<b>{rep['conclusion']}</b>"]
    return "\n".join(lines)


def run_nightly(send_telegram: bool = False) -> Dict[str, Any]:
    """Entry point for the post-market pipeline. Reports only — never prunes,
    never re-weights, never touches pruned.json or autonomous_edge_policy.py."""
    import json
    from pathlib import Path
    rep = analyze()
    logger.info("drift monitor: %s", rep.get("conclusion", rep.get("error")))
    try:
        Path("drift_report.json").write_text(json.dumps(rep, indent=2))
    except Exception as exc:
        logger.debug("drift_report write: %s", exc)
    return rep


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = analyze()
    print(format_report(rep).replace("<b>", "").replace("</b>", ""))
