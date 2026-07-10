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
# Forward-holdout promotion (2026-07-10): candidates are only ever promoted to
# the live "filters"/"boosts" lists after holding up on signals from days
# strictly AFTER their discovery date. Until this existed, the consumer's
# rule_validation=="locked_forward_holdout" gate could never open — no code
# produced that status, so every nightly discovery was parked forever.
LEDGER_FILE      = Path(os.getenv("LEARNED_FILTER_LEDGER", "learned_filter_ledger.json"))
FWD_MIN_DAYS     = int(os.getenv("LEARNED_FILTER_FWD_MIN_DAYS", "5"))
FWD_MIN_N        = int(os.getenv("LEARNED_FILTER_FWD_MIN_N", "30"))
FWD_ALPHA        = float(os.getenv("LEARNED_FILTER_FWD_ALPHA", "0.05"))
LEDGER_MAX       = int(os.getenv("LEARNED_FILTER_LEDGER_MAX", "500"))
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

    def _get_raw(feat: str):
        v = signal_context.get(feat)
        if v is None:
            v = signal_context.get(f"{feat}_enc")
        if v is None and feat in ("strategy", "side"):
            v = signal_context.get(f"__{feat}")
        return v

    def _get(feat: str) -> float:
        """Get feature from signal_context, trying both raw and encoded names."""
        v = _get_raw(feat)
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def _fmt_part(v) -> str:
        # Match failure_autopsy's cross-bin key format: numeric parts render
        # like str(float) ("-1.0"), strings as-is — so live composition
        # compares equal with what discovery/validation saw in the matrix.
        try:
            return str(float(v))
        except Exception:
            return str(v).strip()

    def _match_condition(cond: Dict[str, Any]) -> bool:
        feat = cond.get("feature", "")
        # Cross feature ("strategy×htf_bias_enc"): compose "a × b" the same
        # way failure_autopsy builds its cross-bin keys, then string-compare.
        if "×" in feat:
            a, b = (p.strip() for p in feat.split("×", 1))
            va, vb = _get_raw(a), _get_raw(b)
            if va is None or vb is None:
                return False
            return f"{_fmt_part(va)} × {_fmt_part(vb)}" == str(cond.get("eq", "")).strip()
        if "eq" in cond:
            # 2026-07-10: this used to float-coerce BOTH sides, so a
            # categorical rule (eq "vrvp_zone") compared 0.0 != "vrvp_zone"
            # and could never match. Numeric compare when both sides are
            # numeric; honest string compare otherwise.
            raw = _get_raw(feat)
            try:
                if float(raw) != float(cond["eq"]):
                    return False
            except (TypeError, ValueError):
                if str(raw).strip() != str(cond["eq"]).strip():
                    return False
        val = _get(feat)
        if "gt" in cond and val <= cond["gt"]:
            return False
        if "lt" in cond and val >= cond["lt"]:
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
    # 2026-07-10: was lstrip("flag_"), which strips a character SET, not a
    # prefix — it mangled any feature starting with f/l/a/g/_ (ai_score →
    # "i_score", fii_cum_5d → "ii_cum_5d"), so those rules referenced
    # nonexistent features and could never match.
    clean_feat = feature[5:] if feature.startswith("flag_") else feature

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


# ── Forward-holdout validation & promotion (2026-07-10) ──────────────────────

_META_COL = {"strategy": "__strategy", "side": "__side"}


def _condition_mask(df, cond: Dict[str, Any]):
    """Vectorized condition evaluation against the SAME feature matrix the
    autopsy discovered the rule on (build_feature_matrix columns). Returns a
    boolean Series, or None if the condition references unknown columns."""
    import pandas as pd
    feat = str(cond.get("feature", ""))
    if "×" in feat:
        a, b = (p.strip() for p in feat.split("×", 1))
        col_a, col_b = _META_COL.get(a, a), _META_COL.get(b, b)
        if col_a not in df.columns or col_b not in df.columns:
            return None
        composed = (df[col_a].astype(str).str.strip()
                    + " × " + df[col_b].astype(str).str.strip())
        return composed == str(cond.get("eq", "")).strip()
    col = _META_COL.get(feat, feat)
    if col not in df.columns:
        return None
    s = df[col]
    mask = pd.Series(True, index=df.index)
    if "eq" in cond:
        num = pd.to_numeric(s, errors="coerce")
        try:
            target = float(cond["eq"])
            mask &= num == target
        except (TypeError, ValueError):
            mask &= s.astype(str).str.strip() == str(cond["eq"]).strip()
    num = pd.to_numeric(s, errors="coerce")
    if "gt" in cond:
        mask &= num > float(cond["gt"])
    if "lt" in cond:
        mask &= num < float(cond["lt"])
    if "gte" in cond:
        mask &= num >= float(cond["gte"])
    if "lte" in cond:
        mask &= num <= float(cond["lte"])
    return mask


def _load_ledger() -> Dict[str, Any]:
    try:
        if LEDGER_FILE.exists():
            return json.loads(LEDGER_FILE.read_text())
    except Exception as exc:
        logger.debug("ledger load: %s", exc)
    return {}


def validate_and_promote(df) -> Dict[str, Any]:
    """Forward-holdout validator — the missing half of the learned-filters
    loop. Nightly candidates are merged into a persistent ledger (first_seen
    per rule id); a rule is only evaluated on labelled signals from days
    STRICTLY AFTER its first_seen (data it has never been discovered on), and
    only promoted into the live "filters"/"boosts" lists when its forward
    effect is in the claimed direction AND significant at a Bonferroni-
    corrected one-sided alpha. Promotion is recomputed from scratch every
    night, so a promoted rule that decays forward auto-demotes. Called by
    post_market_ml right after generate_and_save() with the SAME feature
    matrix the discovery ran on.
    """
    from datetime import date
    from scipy import stats as _st

    summary = {"validated": 0, "promoted_filters": 0, "promoted_boosts": 0,
               "ledger_size": 0, "skipped": 0}
    try:
        data = json.loads(FILTERS_FILE.read_text())
    except Exception as exc:
        logger.debug("validate_and_promote: no filters file (%s)", exc)
        return summary
    if data.get("training_contract") != FILTER_TRAINING_CONTRACT:
        return summary

    today = date.today().isoformat()
    ledger = _load_ledger()

    # Merge tonight's candidates (first seen today unless already known).
    for kind, key in (("filter", "candidate_filters"), ("boost", "candidate_boosts")):
        for cand in data.get(key, []) or []:
            cid = str(cand.get("id", ""))
            if not cid:
                continue
            entry = ledger.get(cid) or {"first_seen": today, "kind": kind}
            entry.update({
                "kind": kind, "last_seen": today,
                "condition": cand.get("condition", {}),
                "mult": cand.get("mult", 1.0),
                "description": cand.get("description", ""),
                "evidence": cand.get("evidence", {}),
            })
            ledger[cid] = entry
    # Cap ledger growth (keep newest first_seen).
    if len(ledger) > LEDGER_MAX:
        keep = sorted(ledger.items(), key=lambda kv: kv[1].get("first_seen", ""),
                      reverse=True)[:LEDGER_MAX]
        ledger = dict(keep)
    summary["ledger_size"] = len(ledger)

    promoted_filters: List[Dict[str, Any]] = []
    promoted_boosts:  List[Dict[str, Any]] = []
    if df is not None and len(df) and "tb_label" in df.columns and "__signal_date" in df.columns:
        lab = df[df["tb_label"].isin([1, -1])].copy()
        lab["__signal_date"] = lab["__signal_date"].astype(str)

        # Which ledger entries have enough forward days to be judged at all?
        due = []
        for cid, entry in ledger.items():
            fwd_days = lab.loc[lab["__signal_date"] > str(entry.get("first_seen", today)),
                               "__signal_date"].nunique()
            entry["forward_days"] = int(fwd_days)
            if fwd_days >= FWD_MIN_DAYS:
                due.append(cid)
        alpha_corr = FWD_ALPHA / max(1, len(due))
        summary["validated"] = len(due)
        summary["alpha_corrected"] = round(alpha_corr, 6)

        for cid in due:
            entry = ledger[cid]
            fwd = lab[lab["__signal_date"] > str(entry["first_seen"])]
            mask = _condition_mask(fwd, entry.get("condition", {}))
            if mask is None:
                entry["forward"] = {"status": "unmatchable_condition"}
                summary["skipped"] += 1
                continue
            matched, unmatched = fwd[mask], fwd[~mask]
            fstat: Dict[str, Any] = {
                "n_matched": int(len(matched)), "n_unmatched": int(len(unmatched)),
                "checked": today,
            }
            if len(matched) < FWD_MIN_N or len(unmatched) < FWD_MIN_N:
                fstat["status"] = "insufficient_forward_samples"
                entry["forward"] = fstat
                continue
            m_wr = float(matched["tb_outcome"].mean())
            u_wr = float(unmatched["tb_outcome"].mean())
            t, p_two = _st.ttest_ind(matched["tb_outcome"], unmatched["tb_outcome"],
                                     equal_var=False)
            diff = m_wr - u_wr
            p_one = float(p_two) / 2.0
            fstat.update({"matched_wr": round(m_wr, 4), "unmatched_wr": round(u_wr, 4),
                          "diff": round(diff, 4), "p_one_sided": round(p_one, 6)})
            want_worse = entry.get("kind") == "filter"
            direction_ok = (diff < 0) if want_worse else (diff > 0)
            if direction_ok and p_one < alpha_corr:
                fstat["status"] = "PROMOTED"
                rule = {
                    "id": cid,
                    "description": entry.get("description", cid),
                    "condition": entry.get("condition", {}),
                    "mult": entry.get("mult", 1.0),
                    "evidence": entry.get("evidence", {}),
                    "forward": fstat,
                    "first_seen": entry.get("first_seen"),
                }
                (promoted_filters if want_worse else promoted_boosts).append(rule)
            else:
                fstat["status"] = ("wrong_direction" if not direction_ok
                                   else "not_significant")
            entry["forward"] = fstat

    promoted_filters = promoted_filters[:MAX_FILTERS]
    promoted_boosts = promoted_boosts[:MAX_BOOSTS]
    summary["promoted_filters"] = len(promoted_filters)
    summary["promoted_boosts"] = len(promoted_boosts)

    any_promoted = bool(promoted_filters or promoted_boosts)
    data["filters"] = promoted_filters
    data["boosts"] = promoted_boosts
    data["active"] = any_promoted
    data["rule_validation"] = ("locked_forward_holdout" if any_promoted
                               else "in_sample_discovery_only")
    data["activation_reason"] = (
        f"{len(promoted_filters)}f+{len(promoted_boosts)}b passed forward holdout "
        f"({FWD_MIN_DAYS}+ fwd days, one-sided alpha {summary.get('alpha_corrected', FWD_ALPHA)})"
        if any_promoted else
        "no candidate has passed the forward holdout yet")
    data["forward_validation"] = summary

    try:
        FILTERS_FILE.write_text(json.dumps(data, indent=2))
        LEDGER_FILE.write_text(json.dumps(ledger, indent=2))
        _CACHE["mtime"] = 0.0
    except Exception as exc:
        logger.warning("validate_and_promote write failed: %s", exc)
    logger.info("forward-holdout: %d validated, %d filter(s) + %d boost(s) promoted, "
                "ledger=%d", summary["validated"], len(promoted_filters),
                len(promoted_boosts), len(ledger))
    return summary
