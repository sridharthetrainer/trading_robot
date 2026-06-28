"""Pre-trade fat-finger, tagging, throttling, and OTR telemetry controls."""

from __future__ import annotations

import json
import hashlib
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Tuple


AUDIT_FILE = Path(os.getenv("EXECUTION_COMPLIANCE_LOG", "execution_compliance.jsonl"))
_LOCK = threading.Lock()
_SUBMISSIONS: deque[float] = deque()
_DAY = ""
_DAY_ORDERS = 0


def _append(event: Dict[str, Any]) -> None:
    payload = {"ts": time.time(), **event}
    with _LOCK:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        # A process-level file lock keeps the hash chain linear when the main
        # bot, tracker, and watchdog record events at the same time.
        with AUDIT_FILE.open("a+", encoding="utf-8") as handle:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            handle.seek(0)
            previous_hash = "GENESIS"
            sequence = 0
            for line in handle:
                try:
                    previous = json.loads(line)
                    previous_hash = str(previous.get("event_hash") or previous_hash)
                    sequence = max(sequence, int(previous.get("sequence") or 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
            payload["sequence"] = sequence + 1
            payload["previous_hash"] = previous_hash
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
            payload["event_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def verify_audit_chain(path: str | Path | None = None) -> Dict[str, Any]:
    """Verify all hash-chained rows; legacy unchained rows remain preserved."""
    audit_path = Path(path) if path is not None else AUDIT_FILE
    result: Dict[str, Any] = {
        "ok": True, "path": str(audit_path), "chained_rows": 0,
        "legacy_rows": 0, "invalid_rows": 0, "last_hash": "GENESIS",
    }
    if not audit_path.exists():
        return result
    previous_hash = "GENESIS"
    previous_sequence = 0
    for line_number, line in enumerate(audit_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            result["invalid_rows"] += 1
            result["ok"] = False
            continue
        if not row.get("event_hash"):
            result["legacy_rows"] += 1
            continue
        supplied_hash = str(row.pop("event_hash"))
        expected_hash = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        sequence = int(row.get("sequence") or 0)
        if (
            supplied_hash != expected_hash
            or str(row.get("previous_hash")) != previous_hash
            or sequence != previous_sequence + 1
        ):
            result["invalid_rows"] += 1
            result["ok"] = False
            result.setdefault("first_invalid_line", line_number)
        previous_hash = supplied_hash
        previous_sequence = sequence
        result["chained_rows"] += 1
    result["last_hash"] = previous_hash
    return result


def preflight_order(
    *, symbol: str, qty: int, side: str, order_type: str, price: float,
    exchange: str, order_tag: str, live: bool,
) -> Tuple[bool, str]:
    global _DAY, _DAY_ORDERS
    symbol = str(symbol or "").upper().strip()
    side = str(side or "").upper().strip()
    order_type = str(order_type or "MARKET").upper().strip()
    exchange = str(exchange or "").upper().strip()
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    max_per_minute = max(1, int(os.getenv("MAX_ORDER_SUBMISSIONS_PER_MINUTE", "20")))
    max_per_day = max(1, int(os.getenv("MAX_ORDER_SUBMISSIONS_PER_DAY", "500")))
    max_qty = max(1, int(os.getenv("MAX_SINGLE_ORDER_QTY", "10000")))

    reason = ""
    if not symbol or side not in {"BUY", "SELL"} or qty <= 0 or qty > max_qty:
        reason = "fat_finger_invalid_order_fields"
    elif exchange not in {"NSE", "NFO", "BSE", "BFO"}:
        reason = "invalid_exchange"
    elif order_type not in {"MARKET", "LIMIT", "STOPLOSS_LIMIT", "STOPLOSS_MARKET", "SL", "SL-M"}:
        reason = "invalid_order_type"
    elif order_type == "LIMIT" and float(price or 0) <= 0:
        reason = "limit_price_missing"
    elif live and not str(order_tag or "").strip():
        reason = "live_algo_order_tag_missing"

    with _LOCK:
        if _DAY != today:
            _DAY, _DAY_ORDERS = today, 0
            _SUBMISSIONS.clear()
        while _SUBMISSIONS and _SUBMISSIONS[0] < now - 60:
            _SUBMISSIONS.popleft()
        if not reason and len(_SUBMISSIONS) >= max_per_minute:
            reason = "order_rate_limit_per_minute"
        if not reason and _DAY_ORDERS >= max_per_day:
            reason = "order_rate_limit_per_day"
        if not reason:
            _SUBMISSIONS.append(now)
            _DAY_ORDERS += 1

    _append({
        "event": "preflight", "allowed": not reason, "reason": reason or "pass",
        "symbol": symbol, "qty": int(qty), "side": side, "order_type": order_type,
        "price": float(price or 0), "exchange": exchange, "live": bool(live),
        "order_tag": str(order_tag or ""), "daily_submissions": _DAY_ORDERS,
    })
    return not reason, reason or "pass"


def record_order_result(*, symbol: str, order_id: Any, broker: str, error: str = "") -> None:
    _append({
        "event": "order_result", "symbol": str(symbol or ""),
        "order_id": str(order_id or ""), "broker": str(broker or ""),
        "success": bool(order_id), "error": str(error or "")[:300],
    })


def record_assurance_event(name: str, **details: Any) -> None:
    """Record a non-order lifecycle event in the same tamper-evident journal."""
    _append({
        "event": "assurance", "name": str(name or "unknown")[:80],
        "details": details,
    })
