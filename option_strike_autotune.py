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
from typing import Any, Dict, List, Tuple

from option_decision_journal import DEFAULT_JOURNAL_FILE, load_recent_option_decisions


AUTOTUNE_FILE = os.getenv("OPTION_STRIKE_AUTOTUNE_FILE", "option_strike_autotune.json")
MIN_SAMPLES = int(os.getenv("OPTION_STRIKE_AUTOTUNE_MIN_SAMPLES", "30"))
MIN_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_MIN_WEIGHT", "0.65"))
MAX_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_MAX_WEIGHT", "1.35"))
SHADOW_SAMPLE_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_SHADOW_WEIGHT", "0.5"))
SYNTHETIC_SHADOW_SAMPLE_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_SYNTHETIC_SHADOW_WEIGHT", "0.15"))
# Live multistrike observations: real chain premia, real EOD-labelled outcomes
# (option_strike_signals in option_chain_snapshots.db). Full weight — this is
# the only genuinely live-source evidence the learner has; the journal's
# "selected" rows are all replay/backfill research.
LIVE_STRIKE_SAMPLE_WEIGHT = float(os.getenv("OPTION_STRIKE_AUTOTUNE_LIVE_STRIKE_WEIGHT", "1.0"))
LIVE_STRIKE_DB = os.getenv("OPTION_STRIKE_SIGNALS_DB", "option_chain_snapshots.db")
LIVE_STRIKE_DAYS = int(os.getenv("OPTION_STRIKE_AUTOTUNE_LIVE_DAYS", "45"))
_LIVE_SOURCES = ("nse_live", "resilience_nse", "angel", "angel_fallback", "sensibull", "bse", "bse_oc")


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


def _load_live_strike_outcomes(
    db_path: str = LIVE_STRIKE_DB, days: int = LIVE_STRIKE_DAYS, limit: int = 8000
) -> List[Dict[str, Any]]:
    """Labelled live-source multistrike rows mapped to the journal-row shape.

    These are the bot's own generated strike signals, priced from the live
    Angel/NSE chain and outcome-labelled at EOD from real subsequent premia —
    the verified evidence stream the journal lacks (its 'selected' rows are
    replay/backfill research). net_pnl is on the same rupee scale as journal
    outcome pnl. Best-effort: returns [] on any error.
    """
    import sqlite3
    from datetime import datetime, timedelta

    rows: List[Dict[str, Any]] = []
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        placeholders = ",".join("?" for _ in _LIVE_SOURCES)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                f"""SELECT snapshot_time, underlying, expiry, strike, option_type,
                           flow, signal, tradable, score, price, entry_price,
                           spread_pct, oi, volume, outcome_label, gross_pnl, net_pnl
                      FROM option_strike_signals
                     WHERE outcome_label IN (-1,0,1)
                       AND lower(COALESCE(source,'')) IN ({placeholders})
                       AND snapshot_time >= ?
                     ORDER BY snapshot_time DESC LIMIT ?""",
                (*_LIVE_SOURCES, cutoff, int(limit)),
            )
            for r in cur:
                premium = _safe_float(r["entry_price"]) or _safe_float(r["price"])
                if premium <= 0:
                    continue
                dte = -1.0
                try:
                    exp = str(r["expiry"] or "")[:10]
                    snap = str(r["snapshot_time"] or "")[:10]
                    if exp and snap:
                        d_exp = datetime.strptime(exp, "%Y-%m-%d")
                        d_snap = datetime.strptime(snap, "%Y-%m-%d")
                        dte = float((d_exp - d_snap).days)
                except Exception:
                    dte = -1.0
                pnl = _safe_float(r["net_pnl"]) or _safe_float(r["gross_pnl"])
                rows.append({
                    "decision": "live_strike",
                    "side": "BUY",
                    "is_live_data": True,
                    "selected": {
                        "strike": _safe_float(r["strike"]),
                        "option_type": str(r["option_type"] or "").upper(),
                        "premium": premium,
                        "dte": dte,
                        "spread_pct": r["spread_pct"],
                        "oi": _safe_float(r["oi"]),
                        "volume": _safe_float(r["volume"]),
                        "flow": str(r["flow"] or ""),
                        "tradable": bool(r["tradable"]),
                    },
                    "outcome": {"label": int(r["outcome_label"]), "pnl": pnl},
                })
    except Exception:
        return rows
    return rows


def candidate_features(row: Dict[str, Any]) -> List[str]:
    selected = row.get("selected", {}) if isinstance(row.get("selected"), dict) else {}
    quality = row.get("quality", {}) if isinstance(row.get("quality"), dict) else {}
    side = str(row.get("side", "") or "").upper()

    premium = _safe_float(selected.get("premium"))
    option_type = str(selected.get("option_type") or "").upper()
    strike_type = str(selected.get("strike_type") or "").upper()
    style = str(selected.get("style") or "").lower()
    dte = _safe_float(selected.get("dte"), -1)
    synthetic_shadow = bool(selected.get("synthetic_shadow"))
    is_shadow = bool(selected.get("shadow"))
    spread = selected.get("spread_pct")
    spread_f = None if spread is None else _safe_float(spread)
    oi = _safe_float(selected.get("oi"))
    volume = _safe_float(selected.get("volume"))
    quality_score = _safe_float(selected.get("quality_score"))
    otm_pct = _safe_float(selected.get("otm_pct"))
    implied_move = _safe_float(quality.get("implied_move_pct"))
    move_used = _safe_float(quality.get("move_used_ratio"))

    feats = [f"side:{side or 'UNKNOWN'}"]
    if option_type in {"CE", "PE"}:
        feats.append(f"option_type:{option_type}")
    if strike_type:
        feats.append(f"strike_type:{strike_type}")
    if style:
        feats.append(f"style:{style}")
    if dte >= 0:
        if dte <= 0:
            feats.append("dte:expiry")
        elif dte <= 2:
            feats.append("dte:near")
        else:
            feats.append("dte:far")
    if is_shadow:
        feats.append("candidate:shadow")
    if synthetic_shadow:
        feats.append("candidate:synthetic_shadow")

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

    # Live multistrike rows only (journal rows lack these keys — no change there)
    flow = str(selected.get("flow") or "").upper()
    if flow:
        feats.append(f"flow:{flow}")
    if "tradable" in selected:
        feats.append("tradable:yes" if selected.get("tradable") else "tradable:no")

    return feats


def _empty_stat() -> Dict[str, Any]:
    return {
        "samples": 0.0,
        "wins": 0.0,
        "losses": 0.0,
        "pnl_sum": 0.0,
        "abs_pnl_sum": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
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
    if pnl > 0:
        stats["gross_profit"] += pnl * weight
    elif pnl < 0:
        stats["gross_loss"] += abs(pnl) * weight
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
    gross_profit = float(stats.get("gross_profit", 0.0) or 0.0)
    gross_loss = float(stats.get("gross_loss", 0.0) or 0.0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (3.0 if gross_profit > 0 else 0.0)
    confidence = min(1.0, samples / max(min_samples * 2, 1))
    win_edge = (win_rate - 0.50) * 2.0
    pnl_edge = math.tanh(avg_pnl / max(avg_abs, 1.0))
    pf_edge = math.tanh((profit_factor - 1.0) / 1.5)
    raw = 1.0 + confidence * ((0.25 * win_edge) + (0.30 * pnl_edge) + (0.25 * pf_edge))
    return round(_clamp(raw, MIN_WEIGHT, MAX_WEIGHT), 3)


def build_strike_autotune(
    journal_file: str = DEFAULT_JOURNAL_FILE,
    output_file: str = AUTOTUNE_FILE,
    min_samples: int = MIN_SAMPLES,
    limit: int = 5000,
    live_strike_db: str = LIVE_STRIKE_DB,
) -> Dict[str, Any]:
    rows = load_recent_option_decisions(path=journal_file, limit=limit)
    feature_stats: Dict[str, Dict[str, Any]] = defaultdict(_empty_stat)
    labelled = 0
    shadow_labelled = 0
    verified_labelled = 0
    verified_shadow_labelled = 0

    for row in rows:
        if str(row.get("decision", "")) != "selected":
            continue
        outcome = _outcome(row)
        if outcome is not None:
            won, pnl = outcome
            labelled += 1
            if bool(row.get("is_live_data")):
                verified_labelled += 1
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
            if bool(row.get("is_live_data")) and not shadow.get("synthetic_shadow"):
                verified_shadow_labelled += 1
            sample_weight = (
                SYNTHETIC_SHADOW_SAMPLE_WEIGHT
                if shadow.get("synthetic_shadow")
                else SHADOW_SAMPLE_WEIGHT
            )
            for feat in candidate_features(shadow_row):
                _add(
                    feature_stats[feat],
                    won,
                    pnl,
                    weight=sample_weight,
                    shadow=True,
                )

    # Live-source labelled strike observations (real chain premia, real EOD
    # outcomes) — the verified evidence the journal's research rows are not.
    live_strike_labelled = 0
    for live_row in (_load_live_strike_outcomes(db_path=live_strike_db) if live_strike_db else []):
        out = _outcome(live_row)
        if out is None:
            continue
        won, pnl = out
        live_strike_labelled += 1
        for feat in candidate_features(live_row):
            _add(feature_stats[feat], won, pnl, weight=LIVE_STRIKE_SAMPLE_WEIGHT)

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
        "verified_labelled_selected": verified_labelled,
        "verified_labelled_shadow": verified_shadow_labelled,
        "live_strike_labelled": live_strike_labelled,
        "verified_generated_outcomes": (
            verified_labelled + verified_shadow_labelled + live_strike_labelled
        ),
        "shadow_sample_weight": SHADOW_SAMPLE_WEIGHT,
        "synthetic_shadow_sample_weight": SYNTHETIC_SHADOW_SAMPLE_WEIGHT,
        "live_strike_sample_weight": LIVE_STRIKE_SAMPLE_WEIGHT,
        "feature_weights": weights,
        "feature_stats": {
            feat: {
                **stats,
                "samples": round(stats["samples"], 4),
                "wins": round(stats["wins"], 4),
                "losses": round(stats["losses"], 4),
                "win_rate": round(stats["wins"] / max(stats["samples"], 1), 4),
                "avg_pnl": round(stats["pnl_sum"] / max(stats["samples"], 1), 2),
                "profit_factor": round(
                    (stats["gross_profit"] / stats["gross_loss"])
                    if stats["gross_loss"] > 0
                    else (999.0 if stats["gross_profit"] > 0 else 0.0),
                    4,
                ),
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
