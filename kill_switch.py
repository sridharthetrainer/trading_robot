"""
kill_switch.py

Production-ready emergency kill switch.

Fixes applied
-------------
_notify("kill_switch_triggered", ...) called alerts.kill_switch_triggered()
which does not exist on AlertManager.  The kill-switch trigger was silently
failing to send any Telegram alert because getattr(alerts, "kill_switch_triggered")
returned None and the callable check short-circuited.

Fix: replace with _notify("critical", ...) which maps to alerts.critical()
— a method that actually exists.  The message includes the source and reason
so recipients can distinguish this alert from other critical alerts.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from alerts import AlertManager
from trade_manager import TradeManager

logger = logging.getLogger(__name__)


@dataclass
class KillSwitchEvent:
    triggered_at: float
    reason:       str
    source:       str
    force_close:  bool


class KillSwitch:
    """
    Global emergency trading stop.

    Typical triggers:
    - broker outage
    - repeated order failures
    - abnormal slippage
    - daily loss breach
    - manual operator intervention
    """

    def __init__(
        self,
        trade_manager:             Optional[TradeManager]  = None,
        alert_manager:             Optional[AlertManager]  = None,
        enabled:                   bool                    = True,
        auto_force_close_default:  bool                    = True,
    ) -> None:
        self.trade_manager            = trade_manager
        self.alerts                   = alert_manager
        self.enabled                  = bool(enabled)
        self.auto_force_close_default = bool(auto_force_close_default)

        self.active:         bool            = False
        self.active_reason:  Optional[str]   = None
        self.active_source:  Optional[str]   = None
        self.triggered_at:   Optional[float] = None
        self.events:         List[KillSwitchEvent] = []

    # ------------------------------------------------------------------
    # Alert helper
    # ------------------------------------------------------------------
    def _notify(self, fn_name: str, *args, **kwargs) -> None:
        if self.alerts is None:
            return
        try:
            fn = getattr(self.alerts, fn_name, None)
            if callable(fn):
                fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("KillSwitch alert failed (%s): %s", fn_name, exc)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def is_active(self) -> bool:
        return self.active

    def can_trade(self) -> bool:
        return self.enabled and not self.active

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def trigger(
        self,
        reason:      str,
        source:      str            = "manual",
        force_close: Optional[bool] = None,
        exchange:    Optional[str]  = None,
    ) -> bool:
        if not self.enabled:
            logger.warning("KillSwitch trigger ignored — kill switch is disabled")
            return False

        if force_close is None:
            force_close = self.auto_force_close_default

        now = time.time()
        self.active         = True
        self.active_reason  = str(reason)
        self.active_source  = str(source)
        self.triggered_at   = now
        self.events.append(KillSwitchEvent(
            triggered_at = now,
            reason       = str(reason),
            source       = str(source),
            force_close  = bool(force_close),
        ))

        logger.critical(
            "KILL SWITCH TRIGGERED | source=%s force_close=%s reason=%s",
            source, force_close, reason,
        )

        # Lock trade manager
        if self.trade_manager is not None:
            try:
                self.trade_manager.lock_trading(f"Kill switch triggered: {reason}")
            except Exception as exc:
                logger.exception("Failed to lock trade manager after kill switch: %s", exc)

        # Send Telegram alert — use .critical() which actually exists on AlertManager
        self._notify(
            "critical",
            f"KILL SWITCH | source={source} force_close={force_close} | {reason}",
            dedup_key="kill_switch_triggered",
        )

        # Force-close all open positions
        if force_close and self.trade_manager is not None:
            try:
                closed = self.trade_manager.close_all_trades(
                    reason=f"kill_switch:{source}",
                    exchange=exchange,
                )
                logger.critical("Kill switch force-closed %d trades", closed)
                self._notify(
                    "critical",
                    f"Kill switch force-closed {closed} open trades",
                    dedup_key="kill_switch_force_close",
                )
            except Exception as exc:
                logger.exception("Kill switch failed to close trades: %s", exc)
                self._notify(
                    "error",
                    f"Kill switch failed during forced close: {exc}",
                    dedup_key="kill_switch_close_fail",
                )

        return True

    def reset(self, reason: str = "manual_reset") -> bool:
        self.active        = False
        self.active_reason = None
        self.active_source = None
        self.triggered_at  = None

        logger.warning("Kill switch reset | reason=%s", reason)

        if self.trade_manager is not None:
            try:
                self.trade_manager.unlock_trading()
            except Exception as exc:
                logger.exception("Failed to unlock trade manager on kill switch reset: %s", exc)

        self._notify("warning", f"Kill switch reset: {reason}", dedup_key="kill_switch_reset")
        return True

    # ------------------------------------------------------------------
    # Convenience helper for rule-based triggers
    # ------------------------------------------------------------------
    def trigger_on_condition(
        self,
        condition:   bool,
        reason:      str,
        source:      str,
        force_close: Optional[bool] = None,
        exchange:    Optional[str]  = None,
    ) -> bool:
        if not condition:
            return False
        return self.trigger(
            reason=reason, source=source,
            force_close=force_close, exchange=exchange,
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def latest_event(self) -> Optional[Dict]:
        if not self.events:
            return None
        e = self.events[-1]
        return {
            "triggered_at": e.triggered_at,
            "reason":       e.reason,
            "source":       e.source,
            "force_close":  e.force_close,
        }

    def summary(self) -> Dict:
        return {
            "enabled":       self.enabled,
            "active":        self.active,
            "active_reason": self.active_reason,
            "active_source": self.active_source,
            "triggered_at":  self.triggered_at,
            "event_count":   len(self.events),
            "latest_event":  self.latest_event(),
        }
