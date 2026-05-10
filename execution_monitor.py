"""
execution_monitor.py

Execution monitor for live/paper trading.

Supports two execution paths:
1. broker_manager.place_order(...)
2. smart_router.route_and_execute(...)

Polls order status after placement and returns a normalized result.

Fixes applied
-------------
health_monitor.check_execution_health() calls:
    report  = self.execution_monitor.run_checks(ltp_map=...)
    summary = self.execution_monitor.summary()

Neither method existed on ExecutionMonitor — every health check cycle
raised AttributeError, silently crashing the health check and leaving
`healthy_count` at 0 which eventually triggered the kill-switch threshold.

Fix: Added internal execution tracking (a bounded deque of recent
results) plus run_checks() and summary() methods that analyze recent
execution quality and return structured issue lists and counts in the
format health_monitor expects.

run_checks() checks three conditions:
- CRITICAL: 3+ consecutive execution failures (broker may be down)
- ERROR:    recent failure rate > 50% over the last 20 executions
- WARNING:  recent timeout rate > 30% over the last 20 executions

All thresholds are constructor parameters so they can be tuned without
touching the code.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Any, Deque, Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success:      bool
    symbol:       str
    side:         str
    qty:          int
    exchange:     str
    order_type:   str
    broker_name:  Optional[str]
    order_id:     Optional[str]
    status:       str
    fill_price:   Optional[float]       = None
    attempts:     int                   = 0
    reason:       str                   = ""
    metadata:     Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutionMonitor:
    """
    Execution monitor for live/paper trading.

    Tracks recent execution outcomes and exposes run_checks() / summary()
    for health_monitor.py integration.
    """

    FINAL_STATUSES = {"FILLED", "COMPLETE", "REJECTED", "CANCELLED", "TIMEOUT"}

    def __init__(
        self,
        broker_manager,
        smart_router                 = None,
        max_retries:             int   = 2,
        fill_timeout_sec:        float = 5.0,
        poll_interval_sec:       float = 0.5,
        # Health-check thresholds
        track_window:            int   = 20,    # how many recent executions to analyse
        critical_consec_fails:   int   = 3,     # consecutive failures → CRITICAL
        error_failure_rate:      float = 0.50,  # failure rate > this → ERROR
        warn_timeout_rate:       float = 0.30,  # timeout rate > this → WARNING
    ) -> None:
        self.broker_manager      = broker_manager
        self.smart_router        = smart_router
        self.max_retries         = int(max_retries)
        self.fill_timeout_sec    = float(fill_timeout_sec)
        self.poll_interval_sec   = float(poll_interval_sec)

        self.track_window        = int(track_window)
        self.critical_consec_fails = int(critical_consec_fails)
        self.error_failure_rate  = float(error_failure_rate)
        self.warn_timeout_rate   = float(warn_timeout_rate)

        # Bounded history of recent execution outcomes
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=self.track_window)

        # Running totals (all time, not just window)
        self._total_executions   = 0
        self._total_failures     = 0
        self._total_timeouts     = 0
        self._consecutive_fails  = 0   # resets on any success

    # ------------------------------------------------------------------
    # Public API — order execution
    # ------------------------------------------------------------------
    def execute_order(
        self,
        symbol:            str,
        side:              str,
        qty:               int,
        order_type:        str   = "MARKET",
        exchange:          str   = "NFO",
        price:             float = 0.0,
        use_router:        bool  = False,
        confidence:        float = 0.0,
        required_balance:  float = 0.0,
    ) -> Dict[str, Any]:
        """
        Execute an order with retries and optional fill monitoring.

        Returns normalized ExecutionResult dict.
        """
        side       = str(side).upper().strip()
        order_type = str(order_type).upper().strip()

        if side not in {"BUY", "SELL"}:
            return self._reject(symbol, side, qty, exchange, order_type, "Invalid side")

        if qty <= 0:
            return self._reject(symbol, side, qty, exchange, order_type, "Invalid quantity")

        attempt      = 0
        last_reason  = "Unknown execution failure"

        while attempt <= self.max_retries:
            attempt += 1

            try:
                logger.info(
                    "Executing order | attempt=%s/%s | %s %s qty=%s type=%s exchange=%s",
                    attempt, self.max_retries + 1, side, symbol, qty, order_type, exchange,
                )

                broker_name           = None
                order_id              = None
                execution_metadata: Dict[str, Any] = {}

                if use_router:
                    if self.smart_router is None:
                        raise RuntimeError("Smart router requested but not configured")

                    routed = self.smart_router.route_and_execute(
                        symbol=symbol, side=side, qty=qty, exchange=exchange,
                        reference_price=price if price > 0 else None,
                        confidence=confidence, required_balance=required_balance,
                        force_order_type=order_type,
                    )

                    if not routed or not routed.get("success"):
                        raise RuntimeError((routed or {}).get("reason", "Smart router execution failed"))

                    broker_name = routed.get("broker_name")
                    order_id    = routed.get("order_id")
                    execution_metadata["route_result"] = routed
                    execution_metadata["path"]         = "smart_router"

                else:
                    raw_result = self.broker_manager.place_order(
                        symbol=symbol, qty=qty, buy_sell=side,
                        order_type=order_type, price=price, exchange=exchange,
                    )
                    broker_name, order_id = self._normalize_place_order_result(raw_result)
                    execution_metadata["raw_result"] = raw_result
                    execution_metadata["path"]       = "broker_manager"

                if not order_id:
                    raise RuntimeError("Order ID not returned")

                status, fill_price = self._wait_for_fill(
                    order_id=order_id, exchange=exchange, symbol=symbol,
                )

                if status in {"FILLED", "COMPLETE"}:
                    logger.info("Order filled | order_id=%s status=%s", order_id, status)
                    result = ExecutionResult(
                        success=True, symbol=symbol, side=side, qty=qty,
                        exchange=exchange, order_type=order_type,
                        broker_name=broker_name, order_id=order_id,
                        status="FILLED", fill_price=fill_price,
                        attempts=attempt, reason="Order filled successfully",
                        metadata=execution_metadata,
                    ).to_dict()
                    self._record_outcome(result)
                    return result

                if status in {"REJECTED", "CANCELLED"}:
                    raise RuntimeError(f"Order failed with status {status}")

                if status == "TIMEOUT":
                    raise RuntimeError("Order fill check timed out")

                raise RuntimeError(f"Unexpected order status: {status}")

            except Exception as exc:
                last_reason = str(exc)
                logger.warning(
                    "Execution failed | attempt=%s/%s | symbol=%s | error=%s",
                    attempt, self.max_retries + 1, symbol, exc,
                )
                if attempt <= self.max_retries:
                    time.sleep(1.0)

        logger.error(
            "Order execution failed after retries | symbol=%s side=%s qty=%s",
            symbol, side, qty,
        )
        result = ExecutionResult(
            success=False, symbol=symbol, side=side, qty=qty,
            exchange=exchange, order_type=order_type,
            broker_name=None, order_id=None,
            status="REJECTED", attempts=attempt, reason=last_reason,
        ).to_dict()
        self._record_outcome(result)
        return result

    # ------------------------------------------------------------------
    # Health-check API — called by health_monitor.py
    # ------------------------------------------------------------------
    def run_checks(self, ltp_map: Optional[Dict[str, float]] = None) -> Dict[str, List[str]]:
        """
        Analyse recent execution history and return categorised issue lists.

        Returns
        -------
        {
            "critical": [<issue description>, ...],
            "errors":   [<issue description>, ...],
            "warnings": [<issue description>, ...],
        }

        An empty list for a category means no issues at that severity.
        health_monitor uses `sum(len(v) for v in report.values())` to
        determine whether any issues exist.
        """
        issues: Dict[str, List[str]] = {"critical": [], "errors": [], "warnings": []}

        recent = list(self._recent)

        # ---- CRITICAL: consecutive failures (broker may be down) -------
        if self._consecutive_fails >= self.critical_consec_fails:
            issues["critical"].append(
                f"{self._consecutive_fails} consecutive execution failures — broker may be unreachable"
            )

        if not recent:
            return issues

        n         = len(recent)
        failures  = sum(1 for r in recent if not r.get("success", True))
        timeouts  = sum(1 for r in recent if r.get("status") == "TIMEOUT" or "timed out" in str(r.get("reason", "")).lower())

        failure_rate = failures  / n
        timeout_rate = timeouts  / n

        # ---- ERROR: high failure rate ----------------------------------
        if failure_rate > self.error_failure_rate and n >= 5:
            issues["errors"].append(
                f"High execution failure rate: {failure_rate:.0%} "
                f"({failures}/{n} in last {n} executions)"
            )

        # ---- WARNING: elevated timeout rate ----------------------------
        if timeout_rate > self.warn_timeout_rate and n >= 5 and "errors" not in issues or not issues["errors"]:
            issues["warnings"].append(
                f"Elevated fill timeout rate: {timeout_rate:.0%} "
                f"({timeouts}/{n} in last {n} executions)"
            )

        return issues

    def summary(self) -> Dict[str, Any]:
        """
        Return a summary dict for health_monitor.check_execution_health().

        Keys used by health_monitor: "critical", "errors", "warnings".
        """
        report = self.run_checks()
        return {
            "critical":         len(report["critical"]),
            "errors":           len(report["errors"]),
            "warnings":         len(report["warnings"]),
            "total_executions": self._total_executions,
            "total_failures":   self._total_failures,
            "total_timeouts":   self._total_timeouts,
            "consecutive_fails": self._consecutive_fails,
            "window_size":      len(self._recent),
        }

    # ------------------------------------------------------------------
    # Internal tracking
    # ------------------------------------------------------------------
    def _record_outcome(self, result: Dict[str, Any]) -> None:
        """Record an execution result for health tracking."""
        self._total_executions += 1
        self._recent.append(result)

        is_timeout = (
            result.get("status") == "TIMEOUT"
            or "timed out" in str(result.get("reason", "")).lower()
        )

        if not result.get("success", True):
            self._total_failures   += 1
            self._consecutive_fails += 1
            if is_timeout:
                self._total_timeouts += 1
        else:
            self._consecutive_fails = 0   # reset on any success

    def _reject(
        self,
        symbol: str, side: str, qty: int,
        exchange: str, order_type: str, reason: str,
    ) -> Dict[str, Any]:
        result = ExecutionResult(
            success=False, symbol=symbol, side=side, qty=qty,
            exchange=exchange, order_type=order_type,
            broker_name=None, order_id=None,
            status="REJECTED", attempts=0, reason=reason,
        ).to_dict()
        self._record_outcome(result)
        return result

    # ------------------------------------------------------------------
    # Fill/status helpers
    # ------------------------------------------------------------------
    def _wait_for_fill(
        self,
        order_id: str,
        exchange: str,
        symbol:   Optional[str] = None,
    ) -> tuple[str, Optional[float]]:
        start_ts = time.time()

        while (time.time() - start_ts) < self.fill_timeout_sec:
            try:
                if not hasattr(self.broker_manager, "get_order_status"):
                    return "FILLED", None

                status_raw = self.broker_manager.get_order_status(
                    order_id=order_id, exchange=exchange,
                )
                status, fill_price = self._normalize_status_result(status_raw)

                if status in self.FINAL_STATUSES:
                    return status, fill_price

                time.sleep(self.poll_interval_sec)

            except Exception:
                logger.exception("Error checking order status | order_id=%s", order_id)
                time.sleep(self.poll_interval_sec)

        return "TIMEOUT", None

    # ------------------------------------------------------------------
    # Normalizers
    # ------------------------------------------------------------------
    def _normalize_place_order_result(
        self, raw_result
    ) -> tuple[Optional[str], Optional[str]]:
        if raw_result is None:
            return None, None
        if isinstance(raw_result, dict):
            return raw_result.get("broker_name"), raw_result.get("order_id")
        if isinstance(raw_result, tuple):
            if len(raw_result) >= 2:
                return self._safe_str(raw_result[0]), self._safe_str(raw_result[1])
            if len(raw_result) == 1:
                return None, self._safe_str(raw_result[0])
        return None, self._safe_str(raw_result)

    def _normalize_status_result(
        self, raw_status
    ) -> tuple[str, Optional[float]]:
        if raw_status is None:
            return "PENDING", None
        if isinstance(raw_status, dict):
            status     = self._map_status(str(raw_status.get("status", "PENDING")).upper())
            fill_price = self._safe_float(
                raw_status.get("fill_price", raw_status.get("average_price"))
            )
            return status, fill_price
        if isinstance(raw_status, tuple):
            if len(raw_status) >= 2:
                return self._map_status(str(raw_status[0]).upper()), self._safe_float(raw_status[1])
            if len(raw_status) == 1:
                return self._map_status(str(raw_status[0]).upper()), None
        return self._map_status(str(raw_status).upper()), None

    def _map_status(self, status: str) -> str:
        if status in {"FILLED", "COMPLETE", "EXECUTED"}:
            return "FILLED"
        if status in {"REJECTED", "FAILED"}:
            return "REJECTED"
        if status in {"CANCELLED", "CANCELED"}:
            return "CANCELLED"
        if status in {"OPEN", "PENDING", "TRIGGER PENDING", "PLACED"}:
            return "PENDING"
        if status == "TIMEOUT":
            return "TIMEOUT"
        return status

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_str(value) -> Optional[str]:
        try:
            return None if value is None else str(value)
        except Exception:
            return None

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        try:
            return None if value is None else float(value)
        except Exception:
            return None
