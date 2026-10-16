"""
Rules file (rules.json) handling: load, validate, storage-rule check,
duplicate / conflict check, add a condition, freeze fingerprint.

STORAGE RULE (user, 2026-10-16): rules.json stores ONLY rule fields plus the
trade date ('date' = the session the item describes, so it can be revisited
and backtested later) and logging dates ('added'). No prices, candles,
indicator values, levels-crossed lists, trade logs or backtest results --
those are pulled from the date at run time or written to results/ only.
validate() enforces this with a whitelist: unknown fields are rejected and
no numeric value is allowed except exit.period.

A new condition is a DUPLICATE of an existing one when timeframe, CPR
relations, CPR width, open location, MA context and VWAP behaviour all match
AND the outcome class matches -> skipped. If everything matches but the
outcome class differs it is a CONFLICT -> added, flagged, and reported.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

from .engine import SETUP_KEYS

PKG_DIR = Path(__file__).resolve().parent
RULES_FILE = PKG_DIR / "rules.json"

REQUIRED = ("id", "date", "instrument", "timeframe", "bias", "setup", "context", "vwap_behavior",
            "entry", "exit", "outcome_class")
OPTIONAL = ("added", "role", "optional_filters", "conflict_with")
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
OUTCOME_CLASSES = {"win", "loss", "flat", "unknown"}
ENTRY_KEYS = {"type", "timeframe", "fill"}
EXIT_KEYS = {"type", "timeframe", "period", "wick_counts", "fill"}
TIMEFRAMES = {"1m", "5m", "15m", "30m"}


def load(path: Path = RULES_FILE) -> dict:
    return json.loads(Path(path).read_text())


def save(doc: dict, path: Path = RULES_FILE) -> None:
    Path(path).write_text(json.dumps(doc, indent=2) + "\n")


def _iso_date_ok(v) -> bool:
    try:
        date.fromisoformat(str(v))
        return True
    except ValueError:
        return False


def storage_violations(cond: dict) -> List[str]:
    """Storage rule: only whitelisted rule fields; no numbers except exit.period."""
    errs = [f"storage rule: field {k!r} not allowed in rules.json (store data/results in results/)"
            for k in cond if k not in REQUIRED and k not in OPTIONAL]
    errs += [f"storage rule: entry.{k} not allowed" for k in cond.get("entry", {}) if k not in ENTRY_KEYS]
    errs += [f"storage rule: exit.{k} not allowed" for k in cond.get("exit", {}) if k not in EXIT_KEYS]

    def walk(obj, path):
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)) and path != "exit.period":
            errs.append(f"storage rule: numeric value at {path} (prices / indicator values are not stored)")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
    walk(cond, "")
    return errs


def validate(cond: dict) -> List[str]:
    errs = [f"missing field: {k}" for k in REQUIRED if k not in cond]
    if errs:
        return errs
    errs += storage_violations(cond)
    if not _iso_date_ok(cond["date"]):
        errs.append("date must be YYYY-MM-DD (the trade date the condition describes)")
    if "added" in cond and not _iso_date_ok(cond["added"]):
        errs.append("added must be YYYY-MM-DD")
    for k in SETUP_KEYS:
        v = cond["setup"].get(k)
        if v is None:
            errs.append(f"setup.{k} missing")
        elif v not in ALLOWED[k]:
            errs.append(f"setup.{k}={v!r} not in {sorted(ALLOWED[k])}")
    for k in cond["setup"]:
        if k not in SETUP_KEYS:
            errs.append(f"setup.{k} unknown")
    for k, v in cond["context"].items():
        if k not in CONTEXT_KEYS:
            errs.append(f"context.{k} unknown (allowed {list(CONTEXT_KEYS)})")
        elif not isinstance(v, bool):
            errs.append(f"context.{k} must be true/false")
    if str(cond["bias"]).upper() not in {"LONG", "SHORT"}:
        errs.append("bias must be LONG or SHORT")
    if cond["timeframe"] not in TIMEFRAMES:
        errs.append(f"timeframe must be one of {sorted(TIMEFRAMES)}")
    if cond["entry"].get("type") != "close_vs_vwap":
        errs.append("entry.type must be close_vs_vwap (only entry implemented)")
    if cond["exit"].get("type") != "close_vs_ema":
        errs.append("exit.type must be close_vs_ema (only exit implemented)")
    if cond["outcome_class"] not in OUTCOME_CLASSES:
        errs.append(f"outcome_class must be one of {sorted(OUTCOME_CLASSES)}")
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
    return str(cond.get("outcome_class") or "").strip().lower()


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
                    "existing_outcome_class": c.get("outcome_class"), "new_outcome_class": cond.get("outcome_class")}
    return {"status": "new"}


def add_condition(cond: dict, path: Path = RULES_FILE) -> Dict[str, object]:
    """Validate (incl. storage rule) + duplicate-check + append. Duplicates are
    NOT written; conflicts ARE written with conflict_with set. 'added' (today)
    and role 'observation' are filled in when missing."""
    doc = load(path)
    res = check_new(cond, doc["conditions"])
    cond = {**cond, "added": cond.get("added") or date.today().isoformat(),
            "role": cond.get("role") or "observation"}
    if res["status"] in {"new", "conflict"}:
        if res["status"] == "conflict":
            cond = {**cond, "conflict_with": res["with"]}
        doc["conditions"].append(cond)
        save(doc, path)
    return res


FROZEN_KEYS = ("definition", "engine_mapping", "forward_bar")


def fingerprint(rule: dict) -> str:
    """sha256 of the frozen parts of a pre-registered rule."""
    blob = json.dumps({k: rule.get(k) for k in FROZEN_KEYS}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def frozen_ok(rule: dict) -> bool:
    return bool(rule.get("frozen")) and rule.get("fingerprint") == fingerprint(rule)
