"""
live_eligibility.py

Build and read a conservative per-strategy live eligibility manifest.

The manifest is intentionally stricter than the strategy selector. A strategy
may still be useful for paper training and labelling, but it should not route
live orders unless rigorous validation says it is live-ready.
"""

from __future__ import annotations

import json
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_MANIFEST_FILE = "live_eligibility.json"
DEFAULT_VALIDATION_FILE = "validation_results.json"
DEFAULT_EDGE_FILE = "strategy_validation_report.json"

PASS_VERDICTS = {"PASS", "POSITIVE"}
BLOCK_VERDICTS = {"FAIL", "INSUFFICIENT_DATA", "NEGATIVE_EDGE"}
DEFAULT_MAX_VALIDATION_AGE_DAYS = 14
DEFAULT_MIN_DEFLATED_SHARPE = 0.95
DEFAULT_MIN_PROFITABLE_WINDOWS = 0.60
DEFAULT_MIN_VALIDATION_WINDOWS = 4


def _load_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _file_info(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {
            "path": path,
            "exists": False,
            "mtime": None,
            "age_days": None,
            "sha256": "",
        }
    try:
        data = p.read_bytes()
        mtime = p.stat().st_mtime
        age_days = max(0.0, (time.time() - mtime) / 86400.0)
        return {
            "path": path,
            "exists": True,
            "mtime": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
            "age_days": round(age_days, 3),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    except Exception:
        return {
            "path": path,
            "exists": True,
            "mtime": None,
            "age_days": None,
            "sha256": "",
        }


def _results_map(raw: Dict[str, Any]) -> Dict[str, Any]:
    results = raw.get("results", raw)
    return results if isinstance(results, dict) else {}


def _normalize_name(name: str) -> str:
    return str(name or "").strip().lower()


def _edge_statuses(edge_raw: Dict[str, Any]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    strategies = edge_raw.get("strategies", {})
    if isinstance(strategies, dict):
        for name, data in strategies.items():
            if isinstance(data, dict):
                statuses[_normalize_name(name)] = str(data.get("status", "")).upper()

    for name in edge_raw.get("edge_confirmed", []) or []:
        statuses.setdefault(_normalize_name(name), "EDGE_CONFIRMED")
    for name in edge_raw.get("negative_edge", []) or []:
        statuses[_normalize_name(name)] = "NEGATIVE_EDGE"
    for name in edge_raw.get("insufficient_data", []) or []:
        statuses.setdefault(_normalize_name(name), "INSUFFICIENT_DATA")
    for name in edge_raw.get("marginal", []) or []:
        statuses.setdefault(_normalize_name(name), "MARGINAL")
    return statuses


def build_manifest(
    validation_file: str = DEFAULT_VALIDATION_FILE,
    edge_file: str = DEFAULT_EDGE_FILE,
    output_file: str = DEFAULT_MANIFEST_FILE,
    extra_strategies: Optional[Iterable[str]] = None,
    max_validation_age_days: int = DEFAULT_MAX_VALIDATION_AGE_DAYS,
    min_deflated_sharpe: float = DEFAULT_MIN_DEFLATED_SHARPE,
    min_profitable_windows: float = DEFAULT_MIN_PROFITABLE_WINDOWS,
    min_validation_windows: int = DEFAULT_MIN_VALIDATION_WINDOWS,
) -> Dict[str, Any]:
    """
    Build a per-strategy live eligibility manifest.

    Rule:
    - If rigorous validation exists for a strategy, only PASS/POSITIVE is live.
    - FAIL/INSUFFICIENT_DATA/NEGATIVE_EDGE are blocked.
    - Strategies without rigorous validation are blocked by default, even if
      an edge report is marginal/positive. They remain available for paper
      training unless another config gate disables them.
    """
    validation_raw = _load_json(validation_file)
    validation = _results_map(validation_raw)
    edge_raw = _load_json(edge_file)
    edge_status = _edge_statuses(edge_raw)
    validation_info = _file_info(validation_file)
    edge_info = _file_info(edge_file)

    warnings = []
    if not validation_info["exists"]:
        warnings.append("validation_file_missing")
    if not edge_info["exists"]:
        warnings.append("edge_file_missing")

    validation_age = validation_info.get("age_days")
    validation_stale = (
        isinstance(validation_age, (int, float))
        and max_validation_age_days > 0
        and validation_age > float(max_validation_age_days)
    )
    if validation_stale:
        warnings.append("validation_file_stale")

    names = set(edge_status)
    names.update(_normalize_name(k) for k in validation.keys())
    if extra_strategies:
        names.update(_normalize_name(k) for k in extra_strategies)
    names.discard("")

    strategies: Dict[str, Dict[str, Any]] = {}
    live_ready_count = 0

    for name in sorted(names):
        v = validation.get(name, {})
        if not isinstance(v, dict):
            v = {}
        verdict = str(v.get("verdict", "")).upper()
        edge = edge_status.get(name, "")

        if verdict:
            live_ready = verdict in PASS_VERDICTS
            reason = "" if live_ready else f"validation_{verdict.lower()}"
        else:
            live_ready = False
            reason = "missing_rigorous_validation"

        if live_ready and validation_stale:
            live_ready = False
            reason = "stale_validation_results"

        # Re-check the evidence behind a PASS. This prevents a hand-edited or
        # legacy verdict from bypassing after-cost, stability and multiple-test
        # safeguards.
        if live_ready:
            evidence_checks = {
                "positive_after_cost_pnl": float(v.get("dev_avg_pnl", 0) or 0) > 0,
                "minimum_trades": bool(v.get("min_trade_ok", False)),
                "parameter_stability": bool(v.get("stability_ok", False)),
                "deflated_sharpe": float(v.get("deflated_sharpe", 0) or 0) >= float(min_deflated_sharpe),
                "profitable_windows": float(v.get("dev_pct_profitable", 0) or 0) >= float(min_profitable_windows),
                "validation_windows": int(v.get("dev_windows", 0) or 0) >= int(min_validation_windows),
            }
            failed_checks = [key for key, ok in evidence_checks.items() if not ok]
            if failed_checks:
                live_ready = False
                reason = "validation_evidence_failed:" + ",".join(failed_checks)
        else:
            evidence_checks = {}
            failed_checks = []

        if edge in BLOCK_VERDICTS:
            live_ready = False
            reason = f"edge_{edge.lower()}"

        if live_ready:
            live_ready_count += 1

        strategies[name] = {
            "live_ready": bool(live_ready),
            "paper_training_only": not bool(live_ready),
            "block_reason": reason,
            "validation_verdict": verdict,
            "edge_status": edge,
            "dev_avg_sharpe": v.get("dev_avg_sharpe"),
            "dev_avg_pnl": v.get("dev_avg_pnl"),
            "dev_avg_trades": v.get("dev_avg_trades"),
            "deflated_sharpe": v.get("deflated_sharpe"),
            "min_trade_ok": v.get("min_trade_ok"),
            "stability_ok": v.get("stability_ok"),
            "dev_pct_profitable": v.get("dev_pct_profitable"),
            "dev_windows": v.get("dev_windows"),
            "evidence_checks": evidence_checks,
            "failed_evidence_checks": failed_checks,
        }

    manifest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "policy": "validation_pass_required",
        "validation_file": validation_file,
        "edge_file": edge_file,
        "max_validation_age_days": max_validation_age_days,
        "minimum_evidence": {
            "deflated_sharpe": float(min_deflated_sharpe),
            "profitable_windows": float(min_profitable_windows),
            "validation_windows": int(min_validation_windows),
            "positive_after_cost_pnl": True,
            "minimum_trades": True,
            "parameter_stability": True,
        },
        "warnings": warnings,
        "source_files": {
            "validation": validation_info,
            "edge": edge_info,
        },
        "live_ready_count": live_ready_count,
        "total_strategies": len(strategies),
        "live_ready": live_ready_count > 0,
        "paper_training_only": live_ready_count == 0,
        "live_block_reason": (
            "" if live_ready_count > 0
            else "no_strategy_passed_live_eligibility_manifest"
        ),
        "strategies": strategies,
    }

    Path(output_file).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_manifest(path: str = DEFAULT_MANIFEST_FILE) -> Dict[str, Any]:
    return _load_json(path)


def strategy_status(strategy: str, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    manifest = manifest or load_manifest()
    strategies = manifest.get("strategies", {})
    if not isinstance(strategies, dict):
        strategies = {}
    name = _normalize_name(strategy or "fallback")
    status = strategies.get(name)
    if isinstance(status, dict):
        return dict(status)
    return {
        "live_ready": False,
        "paper_training_only": True,
        "block_reason": "strategy_missing_from_live_eligibility_manifest",
        "validation_verdict": "",
        "edge_status": "",
    }


def main() -> int:
    manifest = build_manifest()
    print(
        "live_eligibility.json written | "
        f"live_ready={manifest['live_ready_count']}/{manifest['total_strategies']}"
    )
    if manifest["paper_training_only"]:
        print(f"live blocked: {manifest['live_block_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
