"""Machine-readable SEBI/NSE retail-algo deployment readiness audit."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from execution_compliance import verify_audit_chain
from option_institutional_controls import retail_algo_readiness


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_report() -> Dict[str, Any]:
    try:
        import config
        paper = bool(getattr(config, "PAPER_TRADING", True))
        real = bool(getattr(config, "ENABLE_REAL_TRADING", False))
    except Exception:
        paper, real = True, False
    ips = [
        value.strip() for value in os.getenv("RETAIL_ALGO_STATIC_IPS", "").split(",")
        if value.strip()
    ]
    chain = verify_audit_chain()
    report = retail_algo_readiness(
        paper_trading=paper,
        enable_real_trading=real,
        static_ips=ips,
        broker_algo_registered=_bool("BROKER_RETAIL_ALGO_REGISTERED"),
        strategy_registered=_bool("OPTION_STRATEGY_EXCHANGE_REGISTERED"),
        order_tags_enabled=_bool("RETAIL_ALGO_ORDER_TAGS_ENABLED", True),
        audit_chain_ok=bool(chain.get("ok")),
    )
    report["audit_chain"] = chain
    report["external_actions"] = [
        "Obtain broker confirmation for the compliant retail-algo API path.",
        "Register/whitelist primary and optional secondary public static IP.",
        "Complete exchange/broker strategy registration where required.",
        "Do not set approval flags from code; record documentary evidence first.",
    ]
    report["policy"] = "fail_closed_external_approvals_required"
    return report


def write_report(path: str = "retail_algo_readiness.json") -> Dict[str, Any]:
    report = build_report()
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = write_report()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("live_ready") else 2)
