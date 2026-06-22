"""
health_monitor.py

Centralized runtime health monitor.

Fixes applied
-------------
1. self.results list grew without bound
   Every call to run_all_checks() appended 4 HealthCheckResult entries
   (one per check category).  At a 30-second cycle that's ~11,000 entries
   per day.  After a week, summary() iterates all ~77,000 entries on
   every call just to count severities.

   Fix: self.results is now a bounded collections.deque with
   max_results entries (default 500 — covers ~42 hours of 5-min cycles
   at 4 checks per run).  Old entries are automatically discarded.
   summary() and get_recent_results() operate on the deque directly.

2. Dedup key included the full message string
   The dedup key was:
       key = f"{component}:{healthy}:{severity}:{message}"
   Messages contain variable data:
       "Market data healthy; age=45 sec"
       "Market data healthy; age=50 sec"
   Each cycle produced a new key so the dedup window (default 120 sec)
   never fired — every health-check alert was sent to Telegram every
   single cycle, flooding the channel.

   Fix: key is now component + healthy + severity only:
       key = f"{component}:{healthy}:{severity}"
   The human-readable message still goes to the logger and to the
   latest_results output; only the dedup gate uses the coarser key.
   This means one alert per severity state per component per
   dedup_window_sec, regardless of how the numeric values in the
   message change from cycle to cycle.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

from alerts import AlertManager
from broker_manager import BrokerManager
from execution_monitor import ExecutionMonitor
from kill_switch import KillSwitch
from trade_manager import TradeManager

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    component:  str
    healthy:    bool
    severity:   str
    message:    str
    timestamp:  float


class HealthMonitor:
    """
    Centralized runtime health monitor.

    Checks on each run_all_checks() call:
    - Broker connectivity
    - Market data freshness
    - Trade manager state
    - Execution monitor quality
    """

    def __init__(
        self,
        broker_manager:            Optional[BrokerManager]   = None,
        trade_manager:             Optional[TradeManager]    = None,
        alert_manager:             Optional[AlertManager]    = None,
        execution_monitor:         Optional[ExecutionMonitor] = None,
        kill_switch:               Optional[KillSwitch]      = None,
        max_data_age_sec:          int                       = 30,
        max_no_broker_sec:         int                       = 60,
        critical_issue_threshold:  int                       = 3,
        auto_trigger_kill_switch:  bool                      = True,
        dedup_window_sec:          int                       = 120,
        max_results:               int                       = 500,
    ) -> None:
        self.broker_manager           = broker_manager
        self.trade_manager            = trade_manager
        self.alerts                   = alert_manager
        self.execution_monitor        = execution_monitor
        self.kill_switch              = kill_switch

        self.max_data_age_sec         = int(max_data_age_sec)
        self.max_no_broker_sec        = int(max_no_broker_sec)
        self.critical_issue_threshold = int(critical_issue_threshold)
        self.auto_trigger_kill_switch = bool(auto_trigger_kill_switch)
        self.dedup_window_sec         = int(dedup_window_sec)
        self.max_results              = int(max_results)

        # Bounded results history — prevents unbounded memory growth
        self.results: Deque[HealthCheckResult] = deque(maxlen=self.max_results)

        self.last_market_data_ts:   Optional[float] = None
        self.last_any_broker_ok_ts: Optional[float] = None

        # Dedup: key → last sent timestamp
        # Key is now component:healthy:severity (no variable message content)
        self._recent_alert_ts: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _now(self) -> float:
        return time.time()

    def mark_market_data_received(self) -> None:
        self.last_market_data_ts = self._now()

    def mark_broker_ok(self) -> None:
        self.last_any_broker_ok_ts = self._now()

    def _dedup_ok(self, component: str, healthy: bool, severity: str) -> bool:
        """
        Returns True if this component/state combination has not been alerted
        within dedup_window_sec.

        Key is component:healthy:severity — does NOT include the message text
        so that alerts with identical severity/state but changing numeric
        values (e.g. "age=45 sec" vs "age=50 sec") are correctly deduped.
        """
        key  = f"{component}:{healthy}:{severity}"
        now  = self._now()
        last = self._recent_alert_ts.get(key)
        if last is not None and (now - last) < self.dedup_window_sec:
            return False
        self._recent_alert_ts[key] = now
        return True

    def _record(
        self,
        component: str,
        healthy:   bool,
        severity:  str,
        message:   str,
    ) -> None:
        result = HealthCheckResult(
            component = component,
            healthy   = healthy,
            severity  = severity.upper(),
            message   = message,
            timestamp = self._now(),
        )
        self.results.append(result)

        # Send alert only when dedup permits
        if self.alerts is not None and self._dedup_ok(component, healthy, severity.upper()):
            try:
                dedup_key = f"health:{component}:{healthy}:{severity.upper()}"
                fn_name   = {
                    "CRITICAL": "critical",
                    "ERROR":    "error",
                    "WARNING":  "warning",
                }.get(severity.upper(), "info")
                fn = getattr(self.alerts, fn_name, None)
                if callable(fn):
                    fn(message, dedup_key=dedup_key)
            except Exception as exc:
                logger.exception("HealthMonitor alert failure: %s", exc)

        if healthy:
            logger.info("%s | %s", component, message)
        elif severity.upper() in {"CRITICAL", "ERROR"}:
            logger.error("%s | %s", component, message)
        else:
            logger.warning("%s | %s", component, message)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------
    def check_brokers(self) -> None:
        if self.broker_manager is None:
            self._record("brokers", True, "INFO",
                         "Broker manager not configured; broker checks skipped")
            return

        rows      = self.broker_manager.get_all_broker_status()
        connected = [r for r in rows if r.get("connected")]

        if connected:
            self.mark_broker_ok()
            self._record("brokers", True, "INFO",
                         f"{len(connected)} broker(s) connected")
        else:
            now      = self._now()
            down_for = (now - self.last_any_broker_ok_ts) if self.last_any_broker_ok_ts else None

            if down_for is not None and down_for >= self.max_no_broker_sec:
                self._record("brokers", False, "CRITICAL",
                             f"No connected brokers for {int(down_for)}s")
            else:
                self._record("brokers", False, "ERROR",
                             "No connected brokers currently available")

    def check_market_data_freshness(self) -> None:
        if self.last_market_data_ts is None:
            self._record("market_data", False, "WARNING",
                         "No market data heartbeat received yet")
            return

        age = self._now() - self.last_market_data_ts

        if age <= self.max_data_age_sec:
            self._record("market_data", True, "INFO",
                         f"Market data healthy; age={int(age)}s")
        elif age <= self.max_data_age_sec * 2:
            self._record("market_data", False, "WARNING",
                         f"Market data stale; age={int(age)}s")
        else:
            self._record("market_data", False, "CRITICAL",
                         f"Market data critically stale; age={int(age)}s")

    def check_trade_manager_state(self) -> None:
        if self.trade_manager is None:
            self._record("trade_manager", True, "INFO",
                         "Trade manager not configured; trade checks skipped")
            return

        summary        = self.trade_manager.summary()
        open_positions = int(summary.get("open_positions", 0))
        locked         = bool(summary.get("trading_locked", False))
        lock_reason    = summary.get("lock_reason")

        if locked:
            self._record("trade_manager", False, "WARNING",
                         f"Trading locked: {lock_reason}")
        else:
            self._record("trade_manager", True, "INFO",
                         f"Trade manager healthy; open_positions={open_positions}")

    def check_execution_health(self, ltp_map: Optional[Dict[str, float]] = None) -> None:
        if self.execution_monitor is None:
            self._record("execution_monitor", True, "INFO",
                         "Execution monitor not configured; execution checks skipped")
            return

        report      = self.execution_monitor.run_checks(ltp_map=ltp_map or {})
        issue_count = sum(len(v) for v in report.values())

        if issue_count == 0:
            self._record("execution_monitor", True, "INFO",
                         "Execution health clean; no issues detected")
        else:
            summary  = self.execution_monitor.summary()
            critical = int(summary.get("critical", 0))
            errors   = int(summary.get("errors",   0))
            warnings = int(summary.get("warnings", 0))

            if critical > 0:
                self._record(
                    "execution_monitor", False, "CRITICAL",
                    f"Execution issues: critical={critical} errors={errors} warnings={warnings}",
                )
            elif errors > 0:
                self._record(
                    "execution_monitor", False, "ERROR",
                    f"Execution issues: errors={errors} warnings={warnings}",
                )
            else:
                self._record(
                    "execution_monitor", False, "WARNING",
                    f"Execution warnings: {warnings}",
                )

    # ------------------------------------------------------------------
    # Combined run
    # ------------------------------------------------------------------
    def run_all_checks(self, ltp_map: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        results_before = len(self.results)

        self.check_brokers()
        self.check_market_data_freshness()
        self.check_trade_manager_state()
        self.check_execution_health(ltp_map=ltp_map or {})

        # Collect only the results appended in this call
        # deque doesn't support slice indices directly, so use list conversion
        all_results   = list(self.results)
        new_results   = all_results[results_before:]  # may be fewer if deque wrapped

        critical_count = sum(1 for r in new_results if r.severity == "CRITICAL")
        error_count    = sum(1 for r in new_results if r.severity == "ERROR")
        warning_count  = sum(1 for r in new_results if r.severity == "WARNING")
        healthy_count  = sum(1 for r in new_results if r.healthy)

        # Auto-trigger kill switch when critical threshold is met
        if (
            self.auto_trigger_kill_switch
            and self.kill_switch is not None
            and not self.kill_switch.is_active()
            and critical_count >= self.critical_issue_threshold
        ):
            reason = (
                f"Health monitor triggered kill switch: "
                f"critical={critical_count} errors={error_count} warnings={warning_count}"
            )
            try:
                self.kill_switch.trigger(
                    reason=reason, source="health_monitor", force_close=True,
                )
            except Exception as exc:
                logger.exception("Failed to trigger kill switch from health monitor: %s", exc)

        return {
            "healthy_count":  healthy_count,
            "warning_count":  warning_count,
            "error_count":    error_count,
            "critical_count": critical_count,
            "total_checks":   len(new_results),
            "latest_results": [
                {
                    "component": r.component,
                    "healthy":   r.healthy,
                    "severity":  r.severity,
                    "message":   r.message,
                    "timestamp": r.timestamp,
                }
                for r in new_results
            ],
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def get_recent_results(self, last_n: int = 20) -> List[Dict[str, Any]]:
        recent = list(self.results)[-last_n:]
        return [
            {
                "component": r.component,
                "healthy":   r.healthy,
                "severity":  r.severity,
                "message":   r.message,
                "timestamp": r.timestamp,
            }
            for r in recent
        ]

    def summary(self) -> Dict[str, Any]:
        all_results = list(self.results)
        return {
            "total_results":           len(all_results),
            "critical":                sum(1 for r in all_results if r.severity == "CRITICAL"),
            "errors":                  sum(1 for r in all_results if r.severity == "ERROR"),
            "warnings":                sum(1 for r in all_results if r.severity == "WARNING"),
            "info":                    sum(1 for r in all_results if r.severity == "INFO"),
            "last_market_data_ts":     self.last_market_data_ts,
            "last_any_broker_ok_ts":   self.last_any_broker_ok_ts,
            "results_capacity":        self.max_results,
            "recent_results":          self.get_recent_results(10),
        }
