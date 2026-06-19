"""
option_strike_autotune.py

Strike-level autotune for option selection.

Reads option_decision_journal.jsonl rows that contain outcomes, learns which
strike features have positive/negative expectancy, and emits bounded multipliers
for future strike ranking.

Outcome fields supported on a selected decision row:
  - outcome: {"label": 1|-1|0, "pnl": number}
  - outcome_label / tb_label: 1|-1|0
  - pnl: number

Rows without outcomes are ignored for learning. Runtime scoring is neutral until
there are enough labelled samples.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from option_decision_journal import DEFAULT_JOURNAL_FILE, load_recent_option_decisions


AUTOTUNE_FILE = os.getenv("OPTION_STRIKE_AUTOTUNE_FILE", "option_strike_autotune.json")
MIN_SAMPLES = int(os.getenv("OPTION_STRIKE_AUTOTUNE_MIN_SAMPLES", "30"))
MIN_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_MIN_WEIGHT", "0.65"))
MAX_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_MAX_WEIGHT", "1.35"))
SHADOW_SAMPLE_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_SHADOW_WEIGHT", "0.5"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _outcome(row: Dict[str, Any]) -> Tuple[bool, float] | None:
    outcome = row.get("outcome")
    label = None
    pnl = _safe_float(row.get("pnl"), 0.0)
    if isinstance(outcome, dict):
        label = outcome.get("label", outcome.get("tb_label"))
        pnl = _safe_float(outcome.get("pnl"), pnl)
    if label is None:
        label = row.get("outcome_label", row.get("tb_label"))
    try:
        label_i = int(label)
    except Exception:
        if pnl != 0:
            return pnl > 0, pnl
        return None
    if label_i == 0:
        return pnl > 0, pnl
    return label_i > 0, pnl


def candidate_features(row: Dict[str, Any]) -> List[str]:
    selected = row.get("selected", {}) if isinstance(row.get("selected"), dict) else {}
    quality = row.get("quality", {}) if isinstance(row.get("quality"), dict) else {}
    side = str(row.get("side", "") or "").upper()

    premium = _safe_float(selected.get("premium"))
    spread = selected.get("spread_pct")
    spread_f = None if spread is None else _safe_float(spread)
    oi = _safe_float(selected.get("oi"))
    volume = _safe_float(selected.get("volume"))
    quality_score = _safe_float(selected.get("quality_score"))
    otm_pct = _safe_float(selected.get("otm_pct"))
    implied_move = _safe_float(quality.get("implied_move_pct"))
    move_used = _safe_float(quality.get("move_used_ratio"))

    feats = [f"side:{side or 'UNKNOWN'}"]

    if premium > 0:
        if premium < 5:
            feats.append("premium:<5")
        elif premium < 15:
            feats.append("premium:5-15")
        elif premium < 35:
            feats.append("premium:15-35")
        else:
            feats.append("premium:>=35")

    if spread_f is not None:
        if spread_f > 0.20:
            feats.append("spread:>20pct")
        elif spread_f > 0.12:
            feats.append("spread:12-20pct")
        else:
            feats.append("spread:<12pct")

    if oi > 0:
        feats.append("oi:low" if oi < 500 else "oi:good")
    if volume > 0:
        feats.append("volume:low" if volume < 500 else "volume:good")
    if quality_score > 0:
        feats.append("quality:high" if quality_score >= 0.65 else "quality:low")
    if otm_pct > 0:
        feats.append("otm:near" if otm_pct <= 1.5 else "otm:far")
    if implied_move > 0:
        feats.append("implied_move:high" if implied_move >= 0.8 else "implied_move:normal")
    if move_used > 0:
        feats.append("move_used:high" if move_used >= 0.6 else "move_used:room")

    return feats


def _empty_stat() -> Dict[str, Any]:
    return {
        "samples": 0.0,
        "wins": 0.0,
        "losses": 0.0,
        "pnl_sum": 0.0,
        "abs_pnl_sum": 0.0,
        "real_samples": 0,
        "shadow_samples": 0,
    }


def _add(stats: Dict[str, Any], won: bool, pnl: float, weight: float = 1.0, shadow: bool = False) -> None:
    weight = max(0.0, float(weight or 0.0))
    stats["samples"] += weight
    stats["wins"] += weight if won else 0.0
    stats["losses"] += 0.0 if won else weight
    stats["pnl_sum"] += pnl * weight
    stats["abs_pnl_sum"] += abs(pnl) * weight
    if shadow:
        stats["shadow_samples"] += 1
    else:
        stats["real_samples"] += 1


def _weight_from_stats(stats: Dict[str, Any], min_samples: int = MIN_SAMPLES) -> float:
    samples = float(stats.get("samples", 0) or 0)
    if samples < min_samples:
        return 1.0
    win_rate = float(stats.get("wins", 0) or 0) / max(samples, 1)
    avg_pnl = float(stats.get("pnl_sum", 0.0) or 0.0) / max(samples, 1)
    avg_abs = float(stats.get("abs_pnl_sum", 0.0) or 0.0) / max(samples, 1)
    confidence = min(1.0, samples / max(min_samples * 2, 1))
    win_edge = (win_rate - 0.50) * 2.0
    pnl_edge = math.tanh(avg_pnl / max(avg_abs, 1.0))
    raw = 1.0 + confidence * ((0.35 * win_edge) + (0.20 * pnl_edge))
    return round(_clamp(raw, MIN_WEIGHT, MAX_WEIGHT), 3)


def build_strike_autotune(
    journal_file: str = DEFAULT_JOURNAL_FILE,
    output_file: str = AUTOTUNE_FILE,
    min_samples: int = MIN_SAMPLES,
    limit: int = 5000,
) -> Dict[str, Any]:
    rows = load_recent_option_decisions(path=journal_file, limit=limit)
    feature_stats: Dict[str, Dict[str, Any]] = defaultdict(_empty_stat)
    labelled = 0
    shadow_labelled = 0

    for row in rows:
        if str(row.get("decision", "")) != "selected":
            continue
        outcome = _outcome(row)
        if outcome is not None:
            won, pnl = outcome
            labelled += 1
            for feat in candidate_features(row):
                _add(feature_stats[feat], won, pnl)

        for shadow in row.get("strikes", []) if isinstance(row.get("strikes"), list) else []:
            if not isinstance(shadow, dict):
                continue
            shadow_outcome = shadow.get("shadow_outcome")
            if not isinstance(shadow_outcome, dict):
                continue
            shadow_row = dict(row)
            shadow_row["selected"] = shadow
            shadow_row["outcome"] = shadow_outcome
            out = _outcome(shadow_row)
            if out is None:
                continue
            won, pnl = out
            shadow_labelled += 1
            for feat in candidate_features(shadow_row):
                _add(
                    feature_stats[feat],
                    won,
                    pnl,
                    weight=SHADOW_SAMPLE_WEIGHT,
                    shadow=True,
                )

    weights = {
        feat: _weight_from_stats(stats, min_samples=min_samples)
        for feat, stats in sorted(feature_stats.items())
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "journal_file": journal_file,
        "min_samples": min_samples,
        "labelled_selected": labelled,
        "labelled_shadow": shadow_labelled,
        "shadow_sample_weight": SHADOW_SAMPLE_WEIGHT,
        "feature_weights": weights,
        "feature_stats": {
            feat: {
                **stats,
                "samples": round(stats["samples"], 4),
                "wins": round(stats["wins"], 4),
                "losses": round(stats["losses"], 4),
                "win_rate": round(stats["wins"] / max(stats["samples"], 1), 4),
                "avg_pnl": round(stats["pnl_sum"] / max(stats["samples"], 1), 2),
            }
            for feat, stats in sorted(feature_stats.items())
        },
    }
    Path(output_file).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_autotune(path: str = AUTOTUNE_FILE) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def score_candidate_with_autotune(
    candidate: Dict[str, Any],
    quality: Dict[str, Any] | None = None,
    side: str = "",
    autotune: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    autotune = autotune if autotune is not None else load_autotune()
    weights = autotune.get("feature_weights", {}) if isinstance(autotune, dict) else {}
    if not isinstance(weights, dict) or not weights:
        return {"multiplier": 1.0, "features": [], "weights": {}, "reason": "neutral_no_autotune"}

    row = {"selected": candidate or {}, "quality": quality or {}, "side": side}
    feats = candidate_features(row)
    used = {feat: _safe_float(weights.get(feat), 1.0) for feat in feats if feat in weights}
    if not used:
        return {"multiplier": 1.0, "features": feats, "weights": {}, "reason": "neutral_no_matching_features"}
    multiplier = 1.0
    for weight in used.values():
        multiplier *= weight
    return {
        "multiplier": round(_clamp(multiplier, MIN_WEIGHT, MAX_WEIGHT), 4),
        "features": feats,
        "weights": used,
        "reason": "autotune_applied",
    }


def main() -> int:
    payload = build_strike_autotune()
    print(
        "option_strike_autotune.json written | "
        f"labelled_selected={payload['labelled_selected']} "
        f"labelled_shadow={payload.get('labelled_shadow', 0)} "
        f"features={len(payload['feature_weights'])}"
    )
    if payload["labelled_selected"] < payload["min_samples"]:
        print("autotune neutral: insufficient labelled selected option decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
