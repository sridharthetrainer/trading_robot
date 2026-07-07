"""
modifier_edge_analyzer.py — autonomous, significance-gated PER-MODIFIER attribution.

The confluence engine (signal_engine.generate_signal) adds 30+ score modifiers
(participant_oi, theta, news, mtf_pivot, ...), each a hand-coded constant added on
faith and never measured. This module measures, for every modifier that is LOGGED
per signal in signal_log, whether the signals it ENDORSED (modifier > 0 in the
trade's own direction) actually had better realized outcomes than the signals it
stayed SILENT on — with the same guardrails strategy_edge_analyzer uses so it
never reacts to noise:

  1. Minimum sample   — need >= MIN_SAMPLES endorsed signals.
  2. Coverage         — a modifier that almost never fires is DEAD (not wired / inert).
  3. Significance     — Welch two-sample t-test that mean(endorsed) != mean(silent).
  4. Multiple testing — at a Bonferroni-corrected alpha (0.05 / #modifiers tested).
  5. Temporal gate    — the lift sign must hold in BOTH time halves (cheap OOS check).

It REPORTS. It does NOT re-weight or prune anything: dropping a modifier is a human
decision after a locked-holdout pass — same discipline as strategy_edge_analyzer.
This only covers the modifiers signal_log actually records; the unlogged confluence
modifiers (GEX, skew, whale, sector-rotation, OI, S/R, pivot_boss, ...) are a known
instrumentation blind spot, to be widened separately.

Verdicts per modifier:
  INSUFFICIENT  — too few endorsed signals (< MIN_SAMPLES)
  DEAD          — fires on < MIN_COVERAGE of signals (not wired / inert)
  HELPS         — endorsed signals significantly BEAT silent ones (keep)
  HURTS         — endorsed signals significantly WORSE than silent (noise/contrarian → prune or flip)
  NOISE         — no significant difference (adds nothing measurable → prune candidate)
  UNSTABLE_OOS  — looked significant but the lift sign flips across time halves
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MIN_SAMPLES  = int(os.getenv("MOD_EDGE_MIN_SAMPLES", "30"))
MIN_COVERAGE = float(os.getenv("MOD_EDGE_MIN_COVERAGE", "0.02"))   # fires >= 2% of signals
ALPHA        = float(os.getenv("MOD_EDGE_ALPHA", "0.05"))
COST_PCT     = float(os.getenv("EDGE_ANALYZER_COST_PCT", "0.12"))  # round-trip %, for context
EPS          = float(os.getenv("MOD_EDGE_EPS", "0.05"))            # |mod| > EPS == "fired"

# Signed additive modifiers logged per signal (see signal_log.py schema).
# Excludes time_bucket_wt (a multiplier) and volume_ratio (unsigned context).
_MOD_COLS: List[str] = [
    "bhav_delivery", "cross_asset_mod", "participant_mod", "expiry_mod",
    "sip_boost", "bulk_deal_mod", "theta_mod", "rebal_mod", "news_mod",
    "mtf_pivot_mod", "ai_score", "rl_bias", "weinstein_mod",
    # widened instrumentation (2026-06-14) — populate after schema migration
    "gex_mod", "skew_mod", "whale_mod", "sr_level_mod", "pivot_boss_mod", "oi_mod",
    # 2026-06-20 batch (logging fixed 2026-07-07: _sig_meta was dropped at the
    # candidate build) + the structure/profile family that was logged but untested
    "sector_mod", "crsi_mod", "nr_mod", "volume_mod",
    "structure_mod", "market_quality_mod", "market_profile_mod",
    "candidate_quality_mod",
]


def _load_clean(days: int = 400):
    """Labelled signals with a direction-aware realized return `ret` (%) plus the
    logged modifier columns. Reuses signal_quality to drop scale-corrupt rows."""
    import sqlite3
    import numpy as np
    import pandas as pd
    from signal_quality import clean_signal_frame

    con = sqlite3.connect("signal_log.db")
    try:
        # only select modifier columns that actually exist (schema may pre-date
        # a widening; the analyze() step further filters to df.columns)
        existing = {r[1] for r in con.execute("PRAGMA table_info(signal_log)").fetchall()}
        present_mods = [c for c in _MOD_COLS if c in existing]
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


def _lift_sign_stable(sub_endorsed, sub_silent) -> bool:
    """The endorsed-vs-silent lift must keep its sign across both time halves."""
    import numpy as np
    if len(sub_endorsed) < 2 * MIN_SAMPLES:
        return False
    e = sub_endorsed.sort_values("signal_date")
    s = sub_silent.sort_values("signal_date")
    em, sm = len(e) // 2, len(s) // 2
    if em == 0 or sm == 0:
        return False
    full  = np.sign(e["ret"].mean() - s["ret"].mean())
    older = np.sign(e["ret"].iloc[:em].mean() - s["ret"].iloc[:sm].mean())
    newer = np.sign(e["ret"].iloc[em:].mean() - s["ret"].iloc[sm:].mean())
    return full != 0 and older == full and newer == full


def _strong_vs_weak_block(block: Dict[str, Any], strong, weak,
                          alpha_corrected: float) -> Dict[str, Any]:
    """Quartile fallback for always-on modifiers: top quartile of the modifier's
    value ("strong endorsement") vs bottom quartile ("weak"). Positive lift =
    the modifier's strength ranks outcomes (informative); negative lift =
    anti-predictive. Keys mirror the main test so downstream consumers
    (format_report, pruning.suggest_from_analyzers) keep working."""
    import numpy as np
    from scipy import stats

    s_ret, w_ret = strong["ret"].values, weak["ret"].values
    s_mean, w_mean = float(np.mean(s_ret)), float(np.mean(w_ret))
    lift = s_mean - w_mean
    t_stat, p_value = stats.ttest_ind(s_ret, w_ret, equal_var=False)  # Welch
    significant = bool(p_value < alpha_corrected)

    block.update({
        "basis": "quartile_strong_vs_weak",
        "n_strong": int(len(strong)),
        "n_weak": int(len(weak)),
        "strong_mean": round(s_mean, 4),
        "weak_mean": round(w_mean, 4),
        "lift": round(lift, 4),
        "strong_win_rate": round(float(np.mean(s_ret > 0)), 4),
        "strong_net_of_cost": round(s_mean - COST_PCT, 4),
        "t_stat": round(float(t_stat), 3),
        "p_value": round(float(p_value), 6),
        "significant": significant,
    })
    if not significant:
        block["verdict"] = "NOISE"
    elif lift > 0:
        block["verdict"] = "HELPS"
    else:
        block["verdict"] = "HURTS"
    if block["verdict"] in ("HELPS", "HURTS") and not _lift_sign_stable(strong, weak):
        block["note"] = "lift sign not stable across time halves"
        block["verdict"] = "UNSTABLE_OOS"
    return block


def _modifier_block(df, col: str, alpha_corrected: float) -> Dict[str, Any]:
    """Endorsed (mod>EPS) vs silent (|mod|<=EPS) two-sample test of mean return."""
    import numpy as np
    from scipy import stats

    n_total   = int(len(df))
    fired     = df[df[col].abs() > EPS]
    coverage  = len(fired) / n_total if n_total else 0.0
    endorsed  = df[df[col] > EPS]
    headwind  = df[df[col] < -EPS]
    silent    = df[df[col].abs() <= EPS]

    block: Dict[str, Any] = {
        "coverage": round(coverage, 4),
        "n_endorsed": int(len(endorsed)),
        "n_headwind": int(len(headwind)),
        "n_silent": int(len(silent)),
    }

    if coverage < MIN_COVERAGE:
        block["verdict"] = "DEAD"
        return block
    if len(endorsed) < MIN_SAMPLES or len(silent) < MIN_SAMPLES:
        # Always-on modifiers (participant_mod / ai_score / whale_mod fire on
        # ~100% of signals) have no silent control group, so the endorsed-vs-
        # silent test can never run. Fall back to strong-vs-weak: top value
        # quartile vs bottom quartile. Same verdicts; basis flags the test.
        if len(silent) < MIN_SAMPLES and df[col].nunique() > 3:
            q1, q3 = float(df[col].quantile(0.25)), float(df[col].quantile(0.75))
            strong, weak = df[df[col] >= q3], df[df[col] <= q1]
            if q3 > q1 and len(strong) >= MIN_SAMPLES and len(weak) >= MIN_SAMPLES:
                return _strong_vs_weak_block(block, strong, weak, alpha_corrected)
        block["verdict"] = "INSUFFICIENT"
        return block

    e_ret, s_ret = endorsed["ret"].values, silent["ret"].values
    e_mean, s_mean = float(np.mean(e_ret)), float(np.mean(s_ret))
    lift = e_mean - s_mean
    t_stat, p_value = stats.ttest_ind(e_ret, s_ret, equal_var=False)  # Welch
    significant = bool(p_value < alpha_corrected)

    block.update({
        "endorsed_mean": round(e_mean, 4),
        "silent_mean": round(s_mean, 4),
        "lift": round(lift, 4),
        "endorsed_win_rate": round(float(np.mean(e_ret > 0)), 4),
        "endorsed_net_of_cost": round(e_mean - COST_PCT, 4),
        "t_stat": round(float(t_stat), 3),
        "p_value": round(float(p_value), 6),
        "significant": significant,
    })

    if not significant:
        block["verdict"] = "NOISE"
    elif lift > 0:
        block["verdict"] = "HELPS"
    else:
        block["verdict"] = "HURTS"

    if block["verdict"] in ("HELPS", "HURTS") and not _lift_sign_stable(endorsed, silent):
        block["note"] = "lift sign not stable across time halves"
        block["verdict"] = "UNSTABLE_OOS"
    return block


def analyze(days: int = 400) -> Dict[str, Any]:
    """Run per-modifier attribution. Returns a structured report."""
    df, qc = _load_clean(days)
    if df is None or len(df) == 0:
        return {"error": "no clean signals", "qc": qc}

    present = [c for c in _MOD_COLS if c in df.columns]
    n_tests = max(1, len(present))
    alpha_corrected = ALPHA / n_tests                # Bonferroni

    report: Dict[str, Any] = {
        "n_signals": int(len(df)),
        "corrupt_dropped": qc.get("dropped", 0),
        "cost_pct": COST_PCT,
        "n_tests": n_tests,
        "alpha_corrected": round(alpha_corrected, 6),
        "by_modifier": {},
    }
    for col in present:
        report["by_modifier"][col] = _modifier_block(df, col, alpha_corrected)

    helps = [c for c, b in report["by_modifier"].items() if b["verdict"] == "HELPS"]
    hurts = [c for c, b in report["by_modifier"].items() if b["verdict"] == "HURTS"]
    dead  = [c for c, b in report["by_modifier"].items() if b["verdict"] == "DEAD"]
    noise = [c for c, b in report["by_modifier"].items() if b["verdict"] == "NOISE"]
    report["summary"] = {"helps": helps, "hurts": hurts, "dead": dead, "noise": noise}
    report["conclusion"] = (
        f"HELPS={helps or '—'} | HURTS={hurts or '—'} | "
        f"DEAD={dead or '—'} | NOISE={noise or '—'}. "
        "Prune/flip is a human call after a locked-holdout pass — report only.")
    return report


def format_report(rep: Dict[str, Any]) -> str:
    if "error" in rep:
        return f"modifier edge analyzer: {rep['error']}"
    lines = [
        "🔬 <b>Per-Modifier Edge Attribution</b>",
        f"signals={rep['n_signals']} (dropped {rep['corrupt_dropped']} corrupt) "
        f"cost={rep['cost_pct']}%  tests={rep['n_tests']} α={rep['alpha_corrected']}",
        "",
    ]
    rows = sorted(rep["by_modifier"].items(),
                  key=lambda kv: kv[1].get("lift", 0.0), reverse=True)
    for c, b in rows:
        v = b["verdict"]
        flag = {"HELPS": "✅", "HURTS": "❌", "DEAD": "💤",
                "NOISE": "  ", "INSUFFICIENT": "  ", "UNSTABLE_OOS": "⚠️"}.get(v, "  ")
        if "lift" in b:
            lines.append(f"{flag} {c:16s} cov={b['coverage']:.0%} n={b['n_endorsed']:4d} "
                         f"lift={b['lift']:+.3f}% p={b.get('p_value')} → {v}")
        else:
            lines.append(f"{flag} {c:16s} cov={b['coverage']:.0%} "
                         f"n={b['n_endorsed']:4d} → {v}")
    lines += ["", f"<b>{rep['conclusion']}</b>"]
    return "\n".join(lines)


def run_nightly(send_telegram: bool = False) -> Dict[str, Any]:
    """Entry point for the post-market pipeline. Reports only — never re-weights."""
    import json
    from pathlib import Path
    rep = analyze()
    logger.info("modifier edge analyzer: %s", rep.get("conclusion", rep.get("error")))
    try:
        Path("modifier_edge_report.json").write_text(json.dumps(rep, indent=2))
    except Exception as exc:
        logger.debug("modifier_edge_report write: %s", exc)
    return rep


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    rep = analyze()
    print(format_report(rep).replace("<b>", "").replace("</b>", ""))
