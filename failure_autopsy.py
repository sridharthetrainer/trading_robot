"""
failure_autopsy.py  —  Analyse WHY signals fail.

For every feature in the training DataFrame, computes win rate by condition bin
and identifies "danger zones" — conditions with statistically high loss rates.

Output example:
  {
    "india_vix": {
      "HIGH (>18.0)": {"n": 89, "win_rate": 0.28, "loss_rate": 0.72,
                       "danger": true, "filter": {"gt": 18.0, "mult": 0.35}}
    },
    "volume_ratio": {
      "LOW (<0.30)": {"n": 47, "win_rate": 0.32, "loss_rate": 0.68,
                      "danger": true, "filter": {"lt": 0.30, "mult": 0.45}}
    },
    "strategy": {
      "TREND_BREAKOUT": {
        "HTF_MISALIGNED": {"n": 22, "win_rate": 0.25, "danger": true}
      }
    }
  }

Usage:
    from failure_autopsy import run_autopsy
    results = run_autopsy(df)     # df from ml_feature_builder.build_feature_matrix()
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# Minimum samples before a bin is considered statistically meaningful
MIN_SAMPLES = int(__import__("os").getenv("AUTOPSY_MIN_SAMPLES", "15"))
# Loss rate threshold to flag as a danger zone
DANGER_LOSS_RATE = float(__import__("os").getenv("AUTOPSY_DANGER_LOSS_RATE", "0.60"))
# Win rate threshold to flag as a strongly positive zone
POSITIVE_WIN_RATE = float(__import__("os").getenv("AUTOPSY_POSITIVE_WIN_RATE", "0.65"))


def _score_mult_from_loss_rate(loss_rate: float) -> float:
    """Map loss_rate to a score multiplier (lower loss rate → closer to 1.0)."""
    if loss_rate >= 0.80:
        return 0.20
    if loss_rate >= 0.70:
        return 0.35
    if loss_rate >= 0.65:
        return 0.45
    if loss_rate >= 0.60:
        return 0.55
    return 1.0   # not a danger zone


def _score_mult_from_win_rate(win_rate: float) -> float:
    """Map win_rate to a boost multiplier for strongly positive zones."""
    if win_rate >= 0.80:
        return 1.40
    if win_rate >= 0.70:
        return 1.25
    if win_rate >= 0.65:
        return 1.15
    return 1.0


def _bin_analysis(
    values: List[float],
    labels: List[int],
    feature_name: str,
) -> Dict[str, Any]:
    """
    Bin a continuous feature into 3 quantile bands (LOW / MID / HIGH)
    and compute win rates per band.
    """
    if not values or len(values) < MIN_SAMPLES * 2:
        return {}

    sorted_v = sorted(values)
    n        = len(sorted_v)
    p33      = sorted_v[n // 3]
    p67      = sorted_v[2 * n // 3]

    bins: Dict[str, Dict] = {
        f"LOW (<{p33:.2f})":  {"n": 0, "wins": 0},
        f"MID ({p33:.2f}–{p67:.2f})": {"n": 0, "wins": 0},
        f"HIGH (>{p67:.2f})": {"n": 0, "wins": 0},
    }
    bin_names = list(bins.keys())

    for v, lbl in zip(values, labels):
        if v <= p33:
            b = bin_names[0]
        elif v <= p67:
            b = bin_names[1]
        else:
            b = bin_names[2]
        bins[b]["n"]    += 1
        bins[b]["wins"] += int(lbl == 1)

    result: Dict[str, Any] = {}
    for bname, counts in bins.items():
        n_b = counts["n"]
        if n_b < MIN_SAMPLES:
            continue
        wr = counts["wins"] / n_b
        lr = 1.0 - wr
        entry: Dict[str, Any] = {
            "n":         n_b,
            "win_rate":  round(wr, 3),
            "loss_rate": round(lr, 3),
            "danger":    lr >= DANGER_LOSS_RATE,
            "positive":  wr >= POSITIVE_WIN_RATE,
        }
        if entry["danger"]:
            entry["filter"] = {
                "feature": feature_name,
                "bin":     bname,
                "mult":    _score_mult_from_loss_rate(lr),
            }
        if entry["positive"]:
            entry["boost"] = {
                "feature": feature_name,
                "bin":     bname,
                "mult":    _score_mult_from_win_rate(wr),
            }
        result[bname] = entry

    return result


def _categorical_analysis(
    values: List[str],
    labels: List[int],
) -> Dict[str, Any]:
    """Compute win rates per category value."""
    counts: Dict[str, Dict] = {}
    for v, lbl in zip(values, labels):
        key = str(v) or "UNKNOWN"
        if key not in counts:
            counts[key] = {"n": 0, "wins": 0}
        counts[key]["n"]    += 1
        counts[key]["wins"] += int(lbl == 1)

    result: Dict[str, Any] = {}
    for key, c in counts.items():
        if c["n"] < MIN_SAMPLES:
            continue
        wr = c["wins"] / c["n"]
        lr = 1.0 - wr
        entry: Dict[str, Any] = {
            "n":         c["n"],
            "win_rate":  round(wr, 3),
            "loss_rate": round(lr, 3),
            "danger":    lr >= DANGER_LOSS_RATE,
            "positive":  wr >= POSITIVE_WIN_RATE,
        }
        if entry["danger"]:
            entry["filter"] = {"mult": _score_mult_from_loss_rate(lr)}
        if entry["positive"]:
            entry["boost"] = {"mult": _score_mult_from_win_rate(wr)}
        result[key] = entry
    return result


def _cross_analysis(
    df: "pd.DataFrame",
    col_a: str,
    col_b: str,
) -> Dict[str, Any]:
    """
    Two-dimensional cross-condition analysis.
    E.g. strategy × regime interaction effects.
    """
    try:
        result: Dict[str, Any] = {}
        for val_a in df[col_a].unique():
            subset = df[df[col_a] == val_a]
            if len(subset) < MIN_SAMPLES:
                continue
            for val_b in subset[col_b].unique():
                cell = subset[subset[col_b] == val_b]
                if len(cell) < MIN_SAMPLES:
                    continue
                wr = cell["tb_outcome"].mean()
                lr = 1.0 - wr
                key = f"{val_a} × {val_b}"
                entry: Dict[str, Any] = {
                    "n":         len(cell),
                    "win_rate":  round(float(wr), 3),
                    "loss_rate": round(float(lr), 3),
                    "danger":    lr >= DANGER_LOSS_RATE,
                    "positive":  wr >= POSITIVE_WIN_RATE,
                }
                if entry["danger"]:
                    entry["filter"] = {
                        col_a: str(val_a),
                        col_b: str(val_b),
                        "mult": _score_mult_from_loss_rate(lr),
                    }
                if entry["danger"] or entry["positive"]:
                    result[key] = entry
        return result
    except Exception as exc:
        logger.debug("cross_analysis %s×%s failed: %s", col_a, col_b, exc)
        return {}


def run_autopsy(df: "pd.DataFrame") -> Dict[str, Any]:
    """
    Full failure autopsy on a labelled feature DataFrame.

    Returns a nested dict: {feature_name: {bin_name: stats_dict}}
    Only returns bins that are statistically meaningful (n >= MIN_SAMPLES).
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas required: pip install pandas")

    if df.empty or "tb_outcome" not in df.columns:
        return {}

    # Use executed signals only (timeout is ambiguous — exclude from loss analysis)
    analysis_df = df[df["tb_label"].isin([1, -1])].copy()
    if len(analysis_df) < MIN_SAMPLES * 2:
        logger.warning("Only %d win/loss signals — autopsy may be unreliable", len(analysis_df))

    labels = analysis_df["tb_outcome"].astype(int).tolist()
    results: Dict[str, Any] = {}

    # ── Continuous feature analysis ───────────────────────────────────────────
    continuous_features = [
        "score", "raw_score", "score_boost", "score_boost_pct",
        "india_vix", "volume_ratio", "n_agree", "n_net_agree",
        "fii_cum_5d", "pcr_atm", "iv_percentile",
        "pct_from_w_r1", "pct_from_m_r1", "expiry_dte",
        "ai_score", "rl_bias", "bhav_delivery",
        "hour_of_day", "day_of_week",
    ]
    for feat in continuous_features:
        if feat not in analysis_df.columns:
            continue
        values = analysis_df[feat].fillna(0).tolist()
        r = _bin_analysis(values, labels, feat)
        if r:
            results[feat] = r

    # ── Categorical / boolean features ───────────────────────────────────────
    bool_features = ["htf_aligned", "vix_high", "near_expiry", "vol_thin",
                     "open_session", "close_session", "monday_trade"]
    for feat in bool_features:
        if feat not in analysis_df.columns:
            continue
        values = [str(int(v)) for v in analysis_df[feat].fillna(0).tolist()]
        r = _categorical_analysis(values, labels)
        if r:
            results[f"flag_{feat}"] = r

    # ── Metadata-based analysis (strategy, regime) ───────────────────────────
    for meta_col in ("__strategy", "__side"):
        if meta_col not in analysis_df.columns:
            continue
        clean_name = meta_col.lstrip("_")
        values = analysis_df[meta_col].fillna("").tolist()
        r = _categorical_analysis(values, labels)
        if r:
            results[clean_name] = r

    # ── Two-dimensional cross analysis ───────────────────────────────────────
    if "__strategy" in analysis_df.columns:
        for cross_col in ("regime_enc", "htf_bias_enc", "flag_vix_high"):
            if cross_col not in analysis_df.columns:
                continue
            r = _cross_analysis(analysis_df, "__strategy", cross_col)
            if r:
                results[f"strategy×{cross_col}"] = r

    # ── Summary stats ─────────────────────────────────────────────────────────
    n_total  = len(analysis_df)
    n_wins   = int((analysis_df["tb_outcome"] == 1).sum())
    results["__summary"] = {
        "n_total":     n_total,
        "n_wins":      n_wins,
        "n_losses":    n_total - n_wins,
        "overall_wr":  round(n_wins / max(n_total, 1), 3),
        "danger_zones": sum(
            1 for feat_dict in results.values()
            if isinstance(feat_dict, dict)
            for cell in feat_dict.values()
            if isinstance(cell, dict) and cell.get("danger")
        ),
    }
    logger.info(
        "Autopsy complete: %d win/loss signals | overall WR=%.1f%% | %d danger zones",
        n_total,
        n_wins / max(n_total, 1) * 100,
        results["__summary"]["danger_zones"],
    )
    return results


def summarise_autopsy(results: Dict[str, Any]) -> str:
    """Return a human-readable text summary of autopsy results."""
    summ = results.get("__summary", {})
    lines = [
        "═" * 60,
        "FAILURE AUTOPSY REPORT",
        "═" * 60,
        f"Signals analysed : {summ.get('n_total', 0)} "
        f"(W={summ.get('n_wins', 0)} L={summ.get('n_losses', 0)})",
        f"Overall win rate : {summ.get('overall_wr', 0):.1%}",
        f"Danger zones     : {summ.get('danger_zones', 0)}",
        "",
        "── DANGER ZONES (high loss rate) ──────────────────────",
    ]
    for feat_name, feat_dict in sorted(results.items()):
        if feat_name.startswith("__") or not isinstance(feat_dict, dict):
            continue
        for bin_name, stats in feat_dict.items():
            if not isinstance(stats, dict) or not stats.get("danger"):
                continue
            mult = (stats.get("filter") or {}).get("mult", 1.0)
            lines.append(
                f"  {feat_name:30s} [{bin_name}]"
                f"  LR={stats['loss_rate']:.0%}  n={stats['n']}  mult×{mult:.2f}"
            )
    lines += [
        "",
        "── POSITIVE ZONES (high win rate) ─────────────────────",
    ]
    for feat_name, feat_dict in sorted(results.items()):
        if feat_name.startswith("__") or not isinstance(feat_dict, dict):
            continue
        for bin_name, stats in feat_dict.items():
            if not isinstance(stats, dict) or not stats.get("positive"):
                continue
            mult = (stats.get("boost") or {}).get("mult", 1.0)
            lines.append(
                f"  {feat_name:30s} [{bin_name}]"
                f"  WR={stats['win_rate']:.0%}  n={stats['n']}  mult×{mult:.2f}"
            )
    lines.append("═" * 60)
    return "\n".join(lines)
