"""
self_healing.py

Production-ready self-healing controller.

What this improves
------------------
- Detects recoverable runtime failures
- Attempts broker/session recovery
- Attempts cooldown reset and reconnection flow
- Supports data-heartbeat recovery actions
- Can trigger alerts, lock trading, or kill switch if recovery fails
- Works with broker_manager.py, health_monitor.py, kill_switch.py, alerts.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from alerts import AlertManager
from broker_manager import BrokerManager
from health_monitor import HealthMonitor
from kill_switch import KillSwitch

logger = logging.getLogger(__name__)


@dataclass
class HealingEvent:
    timestamp: float
    component: str
    issue: str
    action: str
    success: bool
    details: Dict


class SelfHealingSystem:
    """
    Runtime recovery manager.

    Typical responsibilities
    ------------------------
    - recover broker connectivity issues
    - reset broker cooldowns after transient failures
    - detect stale market data and escalate
    - trigger alerts for repeated recovery failures
    - optionally activate kill switch if healing cannot restore system health
    """

    def __init__(
        self,
        broker_manager: Optional[BrokerManager] = None,
        health_monitor: Optional[HealthMonitor] = None,
        alert_manager: Optional[AlertManager] = None,
        kill_switch: Optional[KillSwitch] = None,
        max_recovery_attempts: int = 3,
        recovery_cooldown_sec: int = 30,
        auto_reset_broker_cooldowns: bool = True,
        auto_kill_on_repeated_failure: bool = True,
        repeated_failure_threshold: int = 3,
    ) -> None:
        self.broker_manager = broker_manager
        self.health_monitor = health_monitor
        self.alerts = alert_manager
        self.kill_switch = kill_switch

        self.max_recovery_attempts = int(max_recovery_attempts)
        self.recovery_cooldown_sec = int(recovery_cooldown_sec)
        self.auto_reset_broker_cooldowns = bool(auto_reset_broker_cooldowns)
        self.auto_kill_on_repeated_failure = bool(auto_kill_on_repeated_failure)
        self.repeated_failure_threshold = int(repeated_failure_threshold)

        self.events: List[HealingEvent] = []
        self.failure_counts: Dict[str, int] = {}
        self.last_attempt_ts: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _now(self) -> float:
        return time.time()

    def _record(
        self,
        component: str,
        issue: str,
        action: str,
        success: bool,
        details: Optional[Dict] = None,
    ) -> None:
        event = HealingEvent(
            timestamp=self._now(),
            component=component,
            issue=issue,
            action=action,
            success=bool(success),
            details=details or {},
        )
        self.events.append(event)

        key = f"{component}:{issue}"
        if success:
            self.failure_counts[key] = 0
        else:
            self.failure_counts[key] = self.failure_counts.get(key, 0) + 1

        logger_fn = logger.info if success else logger.warning
        logger_fn(
            "SELF-HEAL | component=%s issue=%s action=%s success=%s details=%s",
            component,
            issue,
            action,
            success,
            details or {},
        )

    def _notify(self, fn_name: str, *args, **kwargs) -> None:
        if self.alerts is None:
            return
        try:
            fn = getattr(self.alerts, fn_name, None)
            if callable(fn):
                fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("SelfHealing alert failed (%s): %s", fn_name, exc)

    def _can_attempt(self, key: str) -> bool:
        last_ts = self.last_attempt_ts.get(key)
        now = self._now()
        if last_ts is None:
            self.last_attempt_ts[key] = now
            return True
        if (now - last_ts) >= self.recovery_cooldown_sec:
            self.last_attempt_ts[key] = now
            return True
        return False

    def _maybe_trigger_kill_switch(self, component: str, issue: str) -> None:
        if not self.auto_kill_on_repeated_failure or self.kill_switch is None:
            return

        key = f"{component}:{issue}"
        failures = self.failure_counts.get(key, 0)

        if failures >= self.repeated_failure_threshold and not self.kill_switch.is_active():
            reason = (
                f"Self-healing failed repeatedly for {component}/{issue}. "
                f"failures={failures}, threshold={self.repeated_failure_threshold}"
            )
            try:
                self.kill_switch.trigger(
                    reason=reason,
                    source="self_healing",
                    force_close=True,
                )
            except Exception as exc:
                logger.exception("Failed to trigger kill switch from self-healing: %s", exc)

    # ------------------------------------------------------------------
    # Recovery actions
    # ------------------------------------------------------------------
    def recover_brokers(self) -> bool:
        """
        Attempt broker-side recovery:
        - reset broker cooldowns
        - re-check connectivity
        """
        component = "broker_manager"
        issue = "broker_connectivity"

        if self.broker_manager is None:
            self._record(component, issue, "skip_no_broker_manager", True, {})
            return True

        key = f"{component}:{issue}"
        if not self._can_attempt(key):
            self._record(component, issue, "cooldown_skip", False, {"cooldown_sec": self.recovery_cooldown_sec})
            return False

        attempts = 0
        success = False

        while attempts < self.max_recovery_attempts:
            attempts += 1

            try:
                if self.auto_reset_broker_cooldowns:
                    self.broker_manager.reset_cooldowns()

                if self.broker_manager.has_any_connected_broker():
                    success = True
                    break
            except Exception as exc:
                self._record(
                    component,
                    issue,
                    "broker_recovery_attempt_exception",
                    False,
                    {"attempt": attempts, "error": str(exc)},
                )

            time.sleep(1)

        self._record(
            component,
            issue,
            "recover_brokers",
            success,
            {"attempts": attempts},
        )

        if success:
            self._notify("info", "Broker recovery successful", dedup_key="self_heal_brokers_ok")
        else:
            self._notify("warning", "Broker recovery failed", dedup_key="self_heal_brokers_fail")
            self._maybe_trigger_kill_switch(component, issue)

        return success

    def recover_market_data(self) -> bool:
        """
        Attempt recovery from stale market-data conditions.

        This module does not own the data-feed connection directly,
        so recovery is limited to:
        - checking whether fresh data heartbeat resumes
        - escalating if it does not
        """
        component = "market_data"
        issue = "stale_data"

        if self.health_monitor is None:
            self._record(component, issue, "skip_no_health_monitor", True, {})
            return True

        key = f"{component}:{issue}"
        if not self._can_attempt(key):
            self._record(component, issue, "cooldown_skip", False, {"cooldown_sec": self.recovery_cooldown_sec})
            return False

        now = self._now()
        last_ts = self.health_monitor.last_market_data_ts

        if last_ts is not None and (now - last_ts) <= self.health_monitor.max_data_age_sec:
            self._record(
                component,
                issue,
                "market_data_already_healthy",
                True,
                {"age_sec": now - last_ts},
            )
            return True

        # Passive re-check loop; actual feed reconnection can be hooked later
        attempts = 0
        success = False
        while attempts < self.max_recovery_attempts:
            attempts += 1
            time.sleep(1)

            last_ts = self.health_monitor.last_market_data_ts
            if last_ts is not None and (self._now() - last_ts) <= self.health_monitor.max_data_age_sec:
                success = True
                break

        self._record(
            component,
            issue,
            "recover_market_data",
            success,
            {"attempts": attempts},
        )

        if success:
            self._notify("info", "Market data heartbeat recovered", dedup_key="self_heal_data_ok")
        else:
            self._notify("warning", "Market data recovery failed", dedup_key="self_heal_data_fail")
            self._maybe_trigger_kill_switch(component, issue)

        return success

    def recover_from_health_report(self, health_report: Dict) -> Dict:
        """
        Run recovery actions based on HealthMonitor.run_all_checks() result.
        """
        result = {
            "broker_recovered": None,
            "market_data_recovered": None,
            "actions_taken": [],
        }

        critical_count = int(health_report.get("critical_count", 0))
        latest_results = health_report.get("latest_results", []) or []

        broker_issue = any(r.get("component") == "brokers" and not r.get("healthy", True) for r in latest_results)
        data_issue = any(r.get("component") == "market_data" and not r.get("healthy", True) for r in latest_results)

        if broker_issue:
            ok = self.recover_brokers()
            result["broker_recovered"] = ok
            result["actions_taken"].append("recover_brokers")

        if data_issue:
            ok = self.recover_market_data()
            result["market_data_recovered"] = ok
            result["actions_taken"].append("recover_market_data")

        if critical_count == 0 and not result["actions_taken"]:
            self._record("system", "health_report", "no_recovery_needed", True, {})
            result["actions_taken"].append("none")

        return result

    # ------------------------------------------------------------------
    # Generic/manual recovery entry points
    # ------------------------------------------------------------------
    def run_recovery_cycle(self) -> Dict:
        """
        Full self-healing cycle:
        - ask HealthMonitor for current state
        - apply relevant recovery actions
        """
        if self.health_monitor is None:
            self._record("system", "health_monitor_missing", "run_recovery_cycle", False, {})
            return {
                "ok": False,
                "reason": "health_monitor_missing",
                "actions_taken": [],
            }

        try:
            report = self.health_monitor.run_all_checks()
            recovery_result = self.recover_from_health_report(report)
            ok = (
                report.get("critical_count", 0) == 0
                or any(v is True for k, v in recovery_result.items() if k.endswith("_recovered"))
            )
            return {
                "ok": ok,
                "health_report": report,
                "recovery_result": recovery_result,
            }
        except Exception as exc:
            self._record(
                "system",
                "recovery_cycle_exception",
                "run_recovery_cycle",
                False,
                {"error": str(exc)},
            )
            self._notify("error", f"Self-healing cycle failed: {exc}", dedup_key="self_heal_cycle_fail")
            self._maybe_trigger_kill_switch("system", "recovery_cycle_exception")
            return {
                "ok": False,
                "reason": str(exc),
                "actions_taken": [],
            }

    def manual_recover_brokers(self) -> bool:
        return self.recover_brokers()

    def manual_recover_market_data(self) -> bool:
        return self.recover_market_data()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def get_recent_events(self, last_n: int = 20) -> List[Dict]:
        recent = self.events[-last_n:]
        return [
            {
                "timestamp": e.timestamp,
                "component": e.component,
                "issue": e.issue,
                "action": e.action,
                "success": e.success,
                "details": e.details,
            }
            for e in recent
        ]

    def summary(self) -> Dict:
        success_count = sum(1 for e in self.events if e.success)
        fail_count = sum(1 for e in self.events if not e.success)

        return {
            "total_events": len(self.events),
            "success_count": success_count,
            "fail_count": fail_count,
            "max_recovery_attempts": self.max_recovery_attempts,
            "recovery_cooldown_sec": self.recovery_cooldown_sec,
            "auto_reset_broker_cooldowns": self.auto_reset_broker_cooldowns,
            "auto_kill_on_repeated_failure": self.auto_kill_on_repeated_failure,
            "repeated_failure_threshold": self.repeated_failure_threshold,
            "failure_counts": dict(self.failure_counts),
            "recent_events": self.get_recent_events(10),
        }
