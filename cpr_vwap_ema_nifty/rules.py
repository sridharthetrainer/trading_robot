"""
Rules file (rules.json) handling: load, validate, duplicate / conflict check,
add a condition.

A new condition is a DUPLICATE of an existing one when timeframe, CPR
relations, CPR width, open location, MA context and VWAP behaviour all match
AND the outcome matches -> skipped. If everything matches but the outcome
differs it is a CONFLICT -> added, flagged, and reported.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .engine import SETUP_KEYS

PKG_DIR = Path(__file__).resolve().parent
RULES_FILE = PKG_DIR / "rules.json"

REQUIRED = ("id", "date", "instrument", "timeframe", "bias", "setup", "context", "vwap_behavior",
            "entry", "exit", "observations", "outcome")
CONTEXT_KEYS = ("above_vwap", "above_ema20_5m", "above_ema50_5m", "above_ema200_5m", "above_ema200_1m")
ALLOWED = {
    "cpr_width": {"narrow", "normal", "wide"},
    "cpr_vs_prior_day": {"higher", "lower", "overlapping_higher", "overlapping_lower", "inside", "outside"},
    "cpr_vs_weekly": {"above", "below", "overlapping"},
    "cpr_vs_monthly": {"above", "below", "overlapping"},
    "open_vs_cpr": {"above", "below", "inside"},
    "open_vs_daily_h3": {"above", "below"},
    "open_vs_weekly_h3": {"above", "below"},
}
FILTERS = {"ema200_30m", "r1_pdh_break", "pwh_break", "target_r4_mh3"}


def load(path: Path = RULES_FILE) -> dict:
    return json.loads(Path(path).read_text())


def save(doc: dict, path: Path = RULES_FILE) -> None:
    Path(path).write_text(json.dumps(doc, indent=2) + "\n")


def validate(cond: dict) -> List[str]:
    errs = [f"missing field: {k}" for k in REQUIRED if k not in cond]
    if errs:
        return errs
    for k in SETUP_KEYS:
        v = cond["setup"].get(k)
        if v is None:
            errs.append(f"setup.{k} missing")
        elif v not in ALLOWED[k]:
            errs.append(f"setup.{k}={v!r} not in {sorted(ALLOWED[k])}")
    for k in cond["context"]:
        if k not in CONTEXT_KEYS:
            errs.append(f"context.{k} unknown (allowed {list(CONTEXT_KEYS)})")
    if str(cond["bias"]).upper() not in {"LONG", "SHORT"}:
        errs.append("bias must be LONG or SHORT")
    if cond["entry"].get("type") != "close_vs_vwap":
        errs.append("entry.type must be close_vs_vwap (only entry implemented)")
    if cond["exit"].get("type") != "close_vs_ema":
        errs.append("exit.type must be close_vs_ema (only exit implemented)")
    for f in cond.get("optional_filters", []):
        if f not in FILTERS:
            errs.append(f"optional filter {f!r} unknown (allowed {sorted(FILTERS)})")
    return errs


def signature(cond: dict) -> Tuple:
    return (
        cond.get("timeframe"),
        tuple(sorted((k, cond["setup"].get(k)) for k in SETUP_KEYS)),
        tuple(sorted((k, bool(v)) for k, v in cond.get("context", {}).items())),
        cond.get("vwap_behavior"),
    )


def _outcome_key(cond: dict) -> str:
    return str(cond.get("outcome_class") or cond.get("outcome") or "").strip().lower()


def check_new(cond: dict, existing: List[dict]) -> Dict[str, object]:
    """{'status': 'new' | 'duplicate' | 'conflict' | 'id_taken' | 'invalid', ...}"""
    errs = validate(cond)
    if errs:
        return {"status": "invalid", "errors": errs}
    if any(c["id"] == cond["id"] for c in existing):
        return {"status": "id_taken", "id": cond["id"]}
    sig = signature(cond)
    for c in existing:
        if signature(c) == sig:
            if _outcome_key(c) == _outcome_key(cond):
                return {"status": "duplicate", "of": c["id"]}
            return {"status": "conflict", "with": c["id"],
                    "existing_outcome": c.get("outcome"), "new_outcome": cond.get("outcome")}
    return {"status": "new"}


def add_condition(cond: dict, path: Path = RULES_FILE) -> Dict[str, object]:
    """Validate + duplicate-check + append. Duplicates are NOT written;
    conflicts ARE written with conflict_with set so both stay visible."""
    doc = load(path)
    res = check_new(cond, doc["conditions"])
    if res["status"] in {"new", "conflict"}:
        if res["status"] == "conflict":
            cond = {**cond, "conflict_with": res["with"]}
        doc["conditions"].append(cond)
        save(doc, path)
    return res
