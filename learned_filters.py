"""
learned_filters.py  —  Auto-upgrade layer: writes learned filter rules and
applies them to live signals at scoring time.

Two roles:
  1. WRITE  — post_market_ml calls generate_and_save() to write learned_filters.json
              from autopsy results + ML importances + model win-prob predictions.
  2. READ   — signal_engine calls apply_learned_filters() on every candidate signal.
              Cache-aware: re-reads JSON only when the file changes on disk.

learned_filters.json schema:
  {
    "version":      "2026-06-13T22:00:00",
    "overall_wr":   0.453,
    "n_signals":    2286,
    "filters": [
      {
        "id":          "vix_danger_high",
        "description": "VIX > 18.0  →  loss rate 72%",
        "condition":   {"feature": "india_vix", "gt": 18.0},
        "mult":        0.35,
        "evidence":    {"n": 89, "loss_rate": 0.72}
      }, ...
    ],
    "boosts": [
      {
        "id":          "htf_aligned_boost",
        "condition":   {"feature": "htf_aligned", "eq": 1},
        "mult":        1.25,
        "evidence":    {"n": 120, "win_rate": 0.71}
      }, ...
    ],
    "ml_model_auc": 0.623
  }
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

FILTERS_FILE    = Path(os.getenv("LEARNED_FILTERS_FILE", "learned_filters.json"))
MAX_FILTERS     = int(os.getenv("LEARNED_FILTERS_MAX", "30"))   # cap to avoid noise
MAX_BOOSTS      = int(os.getenv("LEARNED_BOOSTS_MAX",  "20"))
# Conservative defaults until 90+ days of labeled data exist.
# Unlock via .env: LEARNED_FILTER_MIN_MULT=0.20 LEARNED_FILTER_MAX_MULT=1.50
FILTER_MIN_MULT = float(os.getenv("LEARNED_FILTER_MIN_MULT", "0.80"))
FILTER_MAX_MULT = float(os.getenv("LEARNED_FILTER_MAX_MULT", "1.20"))

# In-memory cache: avoid re-reading file every signal
_CACHE: Dict[str, Any] = {"mtime": 0.0, "data": {}}
FILTER_TRAINING_CONTRACT = "all_generated_signals_v4_causal_representations"


def _load_filters() -> Dict[str, Any]:
    """Load learned_filters.json with file-mtime cache."""
    try:
        mtime = FILTERS_FILE.stat().st_mtime if FILTERS_FILE.exists() else 0.0
        if mtime == 0.0:
            return {}
        if _CACHE["mtime"] == mtime:
            return _CACHE["data"]
        with open(FILTERS_FILE) as f:
            data = json.load(f)
        _CACHE["mtime"] = mtime
        _CACHE["data"]  = data
        logger.debug("learned_filters reloaded from %s", FILTERS_FILE)
        return data
    except Exception as exc:
        logger.debug("learned_filters load failed: %s", exc)
        return {}


def apply_learned_filters(signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply learned filter rules to a live signal context.

    Parameters
    ----------
    signal_context : flat numeric feature dict (from ml_feature_builder.build_signal_context)
                     OR the raw signal dict from signal_engine.

    Returns
    -------
    {
      "mult":       combined score multiplier (product of all matched rules),
      "reason":     comma-separated list of matched rule descriptions,
      "win_prob":   ML model win probability (if model available),
      "filters_hit": list of matched filter ids,
    }
    """
    data = _load_filters()
    if (
        not data
        or data.get("training_contract") != FILTER_TRAINING_CONTRACT
        or not data.get("active", False)
        or data.get("rule_validation") != "locked_forward_holdout"
    ):
        return {"mult": 1.0, "reason": "", "win_prob": 0.5, "filters_hit": []}

    combined_mult = 1.0
    reasons       = []
    filters_hit   = []

    def _get(feat: str) -> float:
        """Get feature from signal_context, trying both raw and encoded names."""
        v = signal_context.get(feat)
        if v is None:
            v = signal_context.get(f"{feat}_enc")
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def _match_condition(cond: Dict[str, Any]) -> bool:
        feat = cond.get("feature", "")
        val  = _get(feat)
        if "gt" in cond and val <= cond["gt"]:
            return False
        if "lt" in cond and val >= cond["lt"]:
            return False
        if "eq" in cond and val != cond["eq"]:
            return False
        if "gte" in cond and val < cond["gte"]:
            return False
        if "lte" in cond and val > cond["lte"]:
            return False
        return True

    # Apply danger filters (multiply down)
    for rule in data.get("filters", []):
        cond = rule.get("condition", {})
        if not _match_condition(cond):
            continue
        mult = float(rule.get("mult", 1.0))
        combined_mult *= mult
        reasons.append(rule.get("description", rule.get("id", "filter")))
        filters_hit.append(rule.get("id", ""))

    # Apply positive boosts (multiply up)
    for rule in data.get("boosts", []):
        cond = rule.get("condition", {})
        if not _match_condition(cond):
            continue
        mult = float(rule.get("mult", 1.0))
        combined_mult *= mult
        reasons.append(f"+{rule.get('description', rule.get('id', 'boost'))}")
        filters_hit.append(rule.get("id", ""))

    # Hard clamp
    combined_mult = max(FILTER_MIN_MULT, min(FILTER_MAX_MULT, combined_mult))

    # ML model win probability (if available, purely informational here)
    win_prob = 0.5
    try:
        from ml_trainer import predict as _ml_predict
        ml_result = _ml_predict(signal_context, symbol=str(signal_context.get("__symbol", "")))
        win_prob  = ml_result.get("win_prob", 0.5)
        if ml_result.get("available") and ml_result.get("cv_auc", 0) >= 0.62:
            # Only adjust score if model clears the CV-AUC gate (>= 0.62).
            # Current cross_symbol model sits at ~0.568 (see ml-layer-audit) -> gated OUT.
            if win_prob < 0.35:
                ml_mult = 0.85   # conservative until 90+ days of data
                combined_mult = max(FILTER_MIN_MULT, combined_mult * ml_mult)
                reasons.append(f"ml_prob={win_prob:.2f}")
                filters_hit.append("ml_low_prob")
            elif win_prob > 0.70:
                ml_mult = 1.10   # conservative until 90+ days of data
                combined_mult = min(FILTER_MAX_MULT, combined_mult * ml_mult)
                reasons.append(f"ml_prob={win_prob:.2f}")
                filters_hit.append("ml_high_prob")
    except Exception:
        pass

    return {
        "mult":        round(combined_mult, 4),
        "reason":      " | ".join(reasons),
        "win_prob":    win_prob,
        "filters_hit": filters_hit,
    }


# ── Writer (called by post_market_ml) ─────────────────────────────────────────

def generate_and_save(
    autopsy_results: Dict[str, Any],
    training_results: Dict[str, Any],
) -> Path:
    """
    Translate autopsy danger zones and ML feature importances into
    learned_filters.json.  Called once post-market by post_market_ml.py.

    Returns path to written file.
    """
    filters: List[Dict[str, Any]] = []
    boosts:  List[Dict[str, Any]] = []

    summary = autopsy_results.get("__summary", {})

    # ── Extract filters and boosts from autopsy ───────────────────────────────
    for feat_name, feat_dict in autopsy_results.items():
        if feat_name.startswith("__") or not isinstance(feat_dict, dict):
            continue
        for bin_name, stats in feat_dict.items():
            if not isinstance(stats, dict):
                continue

            if stats.get("danger"):
                flt = stats.get("filter", {})
                mult   = float(flt.get("mult", 0.5))
                n      = stats["n"]
                lr     = stats["loss_rate"]
                bin_lbl = bin_name.replace(feat_name, "").strip()

                # Build condition dict based on bin_name patterns
                cond = _bin_to_condition(feat_name, bin_name)

                filters.append({
                    "id":          f"{feat_name}_{bin_lbl}".replace(" ", "_")[:40],
                    "description": f"{feat_name} {bin_name} → loss rate {lr:.0%}",
                    "condition":   cond,
                    "mult":        round(mult, 3),
                    "evidence":    {"n": n, "loss_rate": round(lr, 3)},
                })

            if stats.get("positive"):
                bst  = stats.get("boost", {})
                mult = float(bst.get("mult", 1.2))
                n    = stats["n"]
                wr   = stats["win_rate"]
                cond = _bin_to_condition(feat_name, bin_name)
                boosts.append({
                    "id":          f"boost_{feat_name}_{bin_name}".replace(" ", "_")[:40],
                    "description": f"{feat_name} {bin_name} → win rate {wr:.0%}",
                    "condition":   cond,
                    "mult":        round(mult, 3),
                    "evidence":    {"n": n, "win_rate": round(wr, 3)},
                })

    # Sort by evidence strength and cap
    filters.sort(key=lambda x: x["evidence"]["loss_rate"], reverse=True)
    boosts.sort(key=lambda x:  x["evidence"]["win_rate"],  reverse=True)
    filters = filters[:MAX_FILTERS]
    boosts  = boosts[:MAX_BOOSTS]

    ml_auc = (training_results.get("cross_symbol") or {}).get("cv_auc_mean", 0)

    cross_result = training_results.get("cross_symbol") or {}
    model_promoted = bool(cross_result.get("promoted", False))
    # These univariate autopsy rules are discovered on the same sample. Keep
    # them as candidates, but do not let in-sample bins alter live scores.
    payload = {
        "version":      __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "overall_wr":   summary.get("overall_wr", 0),
        "n_signals":    summary.get("n_total", 0),
        "training_contract": FILTER_TRAINING_CONTRACT,
        "model_promoted": model_promoted,
        "active": False,
        "activation_reason": "autopsy_rules_require_locked_forward_holdout",
        "rule_validation": "in_sample_discovery_only",
        "filters":      [],
        "boosts":       [],
        "candidate_filters": filters,
        "candidate_boosts": boosts,
        "ml_model_auc": ml_auc,
    }

    FILTERS_FILE.write_text(json.dumps(payload, indent=2))
    # Invalidate cache so signal_engine picks up new file immediately
    _CACHE["mtime"] = 0.0

    logger.info(
        "learned_filters.json written: %d filters + %d boosts | ML AUC=%.3f",
        0, 0, ml_auc,
    )
    return FILTERS_FILE


def _bin_to_condition(feature: str, bin_name: str) -> Dict[str, Any]:
    """
    Parse bin_name strings like "LOW (<0.30)", "HIGH (>18.0)", "1" (boolean)
    into a structured condition dict for apply_learned_filters().
    """
    import re
    clean_feat = feature.lstrip("flag_")

    # Boolean / categorical bin: "0" or "1"
    if bin_name in ("0", "1"):
        return {"feature": clean_feat, "eq": float(bin_name)}

    # HIGH (>value)
    m = re.search(r">(\d+\.?\d*)", bin_name)
    if m and "HIGH" in bin_name.upper():
        return {"feature": clean_feat, "gt": float(m.group(1))}

    # LOW (<value)
    m = re.search(r"<(\d+\.?\d*)", bin_name)
    if m and "LOW" in bin_name.upper():
        return {"feature": clean_feat, "lt": float(m.group(1))}

    # Categorical: strategy name, regime, etc.
    return {"feature": clean_feat, "eq": bin_name}
