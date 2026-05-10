"""
broker_manager.py

Multi-broker manager supporting both config-dict and broker-list init styles.

Fixes applied
-------------
1. Missing methods required by other modules
   health_monitor.py calls get_all_broker_status() — did not exist.
   self_healing.py calls reset_cooldowns() and has_any_connected_broker()
   — neither existed.
   execution_monitor.py calls get_order_status() — did not exist.
   All four methods are added below.

2. Balance-fallback gap in get_execution_broker()
   After the ranked-by-balance loop failed to find a broker meeting the
   required_balance threshold, it fell through to a second identical loop
   and returned None with an error.  In paper-trading (where required_balance
   is often 0) this was fine, but in live mode with a non-zero required_balance
   the system would refuse to execute even when brokers were available.
   Fixed: if no broker meets the threshold, fall back to the highest-balance
   available broker with a warning rather than refusing entirely.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from angel_broker import AngelBroker

try:
    # dhan_broker removed — dhan_client used instead
    try:
        from dhan_client import get_historical_data as _dhan_hist
    except Exception:
        _dhan_hist = None
    DhanBroker = None  # replaced by dhan_client
except Exception:
    DhanBroker = None

logger = logging.getLogger(__name__)


@dataclass
class BrokerHealth:
    name:           str
    failures:       int   = 0
    cooldown_until: float = 0.0
    last_error:     str   = ""

    def in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until


class BrokerManager:
    """
    Supports both init patterns present in the project:

    1) Config-style (used by LiveSignalEngine):
        BrokerManager({
            "API_KEY": ..., "CLIENT_ID": ..., ...
        })

    2) Broker-list style:
        BrokerManager(
            brokers=[primary_broker, secondary_broker],
            ...
        )
    """

    def __init__(
        self,
        config:                       Optional[Dict[str, Any]] = None,
        brokers:                      Optional[List[Any]]      = None,
        failure_cooldown_sec:         int                      = 30,
        max_failures_before_cooldown: int                      = 2,
        prefer_highest_balance:       bool                     = True,
    ) -> None:
        self.failure_cooldown_sec         = int(failure_cooldown_sec)
        self.max_failures_before_cooldown = int(max_failures_before_cooldown)
        self.prefer_highest_balance       = bool(prefer_highest_balance)

        self.primary_broker   = None
        self.secondary_broker = None
        self.current_broker   = None
        self.brokers: List[Any] = []

        if brokers is None and isinstance(config, dict):
            self._init_from_config(config)
        elif brokers is not None:
            self._init_from_brokers(brokers)
        elif isinstance(config, list):
            self._init_from_brokers(config)

        self.health: Dict[str, BrokerHealth] = {
            self._broker_name(b): BrokerHealth(name=self._broker_name(b))
            for b in self.brokers
        }

        if self.brokers:
            self.primary_broker = self.brokers[0]
            self.current_broker = self.primary_broker
            if len(self.brokers) > 1:
                self.secondary_broker = self.brokers[1]

        logger.info(
            "BrokerManager initialized | brokers=%s",
            [self._broker_name(b) for b in self.brokers],
        )

    # ------------------------------------------------------------------
    # Init helpers
    # ------------------------------------------------------------------
    def _init_from_config(self, config: Dict[str, Any]) -> None:
        """Init Angel as primary, Dhan as secondary (free fallback)."""
        self.config = config
        api_key     = config.get("API_KEY",     "")
        client_id   = config.get("CLIENT_ID",   "")
        password    = config.get("PASSWORD",    "")
        totp_secret = config.get("TOTP_SECRET", "")
        paper_trade = bool(config.get("PAPER_TRADE", True))

        try:
            angel = AngelBroker(
                api_key=api_key, client_id=client_id,
                password=password, totp_secret=totp_secret,
                paper_trade=paper_trade,
            )
            self.brokers.append(angel)
            logger.info("Primary broker initialized: %s", self._broker_name(angel))
        except Exception as exc:
            logger.exception("Failed to initialize Angel broker: %s", exc)

        dhan_client = config.get("DHAN_CLIENT_CODE")
        dhan_token  = config.get("DHAN_TOKEN_ID")

        if dhan_client and dhan_token and DhanBroker is not None:
            try:
                dhan = DhanBroker(dhan_client, dhan_token, paper_trade=paper_trade)
                self.brokers.append(dhan)
                logger.info("Secondary broker initialized: %s", self._broker_name(dhan))
            except Exception as exc:
                logger.warning("Could not initialize Dhan broker: %s", exc)

    def _init_from_brokers(self, brokers: List[Any]) -> None:
        self.brokers = [
            b for b in brokers
            if b is not None and hasattr(b, "get_name")
        ]

    def _broker_name(self, broker: Any) -> str:
        try:
            return broker.get_name()
        except Exception:
            return type(broker).__name__

    # ------------------------------------------------------------------
    # Health / availability
    # ------------------------------------------------------------------
    def _mark_failure(self, broker: Any, error: str = "") -> None:
        name  = self._broker_name(broker)
        state = self.health.setdefault(name, BrokerHealth(name=name))
        state.failures   += 1
        state.last_error  = error
        if state.failures >= self.max_failures_before_cooldown:
            state.cooldown_until = time.time() + self.failure_cooldown_sec
            logger.warning("Broker %s in cooldown for %ss", name, self.failure_cooldown_sec)

    def _mark_success(self, broker: Any) -> None:
        name  = self._broker_name(broker)
        state = self.health.setdefault(name, BrokerHealth(name=name))
        state.failures       = 0
        state.cooldown_until = 0.0
        state.last_error     = ""

    def _is_usable(self, broker: Any) -> bool:
        if broker is None:
            return False
        name  = self._broker_name(broker)
        state = self.health.get(name)
        if state and state.in_cooldown():
            return False
        try:
            if hasattr(broker, "is_connected"):
                return bool(broker.is_connected())
            return True
        except Exception:
            return False

    def reset_cooldowns(self) -> None:
        """
        Reset all broker cooldown timers so they become eligible for use.
        Called by self_healing.py after a recovery attempt.
        """
        for state in self.health.values():
            state.cooldown_until = 0.0
            state.failures       = 0
        logger.info("All broker cooldowns reset")

    def has_any_connected_broker(self) -> bool:
        """
        Return True if at least one broker passes the connectivity check.
        Called by self_healing.py to verify recovery success.
        """
        return any(self._is_usable(b) for b in self.brokers)

    def get_all_broker_status(self) -> List[Dict[str, Any]]:
        """
        Return connectivity + health status for all registered brokers.
        Called by health_monitor.py in check_brokers().

        Returns a list of dicts:
        [{"name": "AngelOne", "connected": True, "failures": 0, ...}, ...]
        """
        result = []
        for broker in self.brokers:
            name  = self._broker_name(broker)
            state = self.health.get(name, BrokerHealth(name=name))
            try:
                connected = bool(broker.is_connected()) if hasattr(broker, "is_connected") else True
            except Exception:
                connected = False

            result.append({
                "name":           name,
                "connected":      connected,
                "failures":       state.failures,
                "in_cooldown":    state.in_cooldown(),
                "cooldown_until": state.cooldown_until,
                "last_error":     state.last_error,
            })
        return result

    # ------------------------------------------------------------------
    # Broker selection
    # ------------------------------------------------------------------
    def get_execution_broker(self, symbol: str = "", required_balance: float = 0.0):
        """
        Return the best available broker for an order.

        If prefer_highest_balance is True, ranks by balance and returns
        the first broker meeting required_balance.  If none meet the
        threshold, falls back to the highest-balance available broker
        with a warning rather than refusing entirely.
        """
        candidates = [b for b in self.brokers if self._is_usable(b)]
        if not candidates:
            logger.error("No usable broker available")
            return None

        if self.prefer_highest_balance:
            ranked = []
            for b in candidates:
                try:
                    bal = float(b.get_balance()) if hasattr(b, "get_balance") else 0.0
                except Exception:
                    bal = 0.0
                ranked.append((bal, b))
            ranked.sort(key=lambda x: x[0], reverse=True)

            # First pass: respect required_balance
            for balance, broker in ranked:
                if balance >= required_balance:
                    return broker

            # Fallback: return highest-balance broker even if below threshold
            if ranked:
                best_bal, best_broker = ranked[0]
                logger.warning(
                    "No broker meets required_balance=%.2f; "
                    "using best available (balance=%.2f, broker=%s)",
                    required_balance, best_bal, self._broker_name(best_broker),
                )
                return best_broker

        # Non-balance-ranked fallback
        for broker in candidates:
            return broker

        logger.error("No broker available for %s", symbol)
        return None

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def place_order_with_fallback(
        self, symbol: str, qty: int, buy_sell: str, required_balance: float, **kwargs
    ):
        """Returns (order_id, broker_name) or (None, None)."""
        broker = self.get_execution_broker(symbol, required_balance)
        if not broker:
            return None, None

        stop_loss  = kwargs.pop("stop_loss", None)
        target     = kwargs.pop("target",    None)
        order_type = kwargs.get("order_type", "MARKET")
        price      = kwargs.get("price",      0)
        exchange   = kwargs.get("exchange")

        try:
            if stop_loss and target and hasattr(broker, "place_bracket_order"):
                order_id = broker.place_bracket_order(
                    symbol=symbol, qty=qty, buy_sell=buy_sell,
                    entry_price=price, stop_loss_price=stop_loss,
                    target_price=target, exchange=exchange,
                )
            else:
                order_id = broker.place_order(
                    symbol=symbol, qty=qty, buy_sell=buy_sell,
                    order_type=order_type, price=price, exchange=exchange,
                )
            self._mark_success(broker)
            return order_id, self._broker_name(broker)

        except Exception as exc:
            self._mark_failure(broker, str(exc))
            logger.error("Order failed on %s: %s", self._broker_name(broker), exc)
            return None, None

    def place_order(
        self, symbol: str, qty: int, buy_sell: str,
        order_type: str = "MARKET", price: float = 0, exchange: Optional[str] = None
    ):
        """Compatibility helper. Returns (broker_name, order_id)."""
        order_id, broker_name = self.place_order_with_fallback(
            symbol=symbol, qty=qty, buy_sell=buy_sell,
            required_balance=0, order_type=order_type,
            price=price, exchange=exchange,
        )
        return broker_name, order_id

    # ------------------------------------------------------------------
    # Order status
    # ------------------------------------------------------------------
    def get_order_status(self, order_id: str, exchange: Optional[str] = None) -> Optional[Any]:
        """
        Query order status from any available broker.
        Called by execution_monitor.py in _wait_for_fill().
        Returns the raw broker response or None.
        """
        for broker in self.brokers:
            if not self._is_usable(broker):
                continue
            try:
                if hasattr(broker, "get_order_status"):
                    status = broker.get_order_status(order_id, exchange=exchange)
                    if status is not None:
                        self._mark_success(broker)
                        return status
            except Exception as exc:
                self._mark_failure(broker, str(exc))
                logger.debug("get_order_status failed on %s: %s", self._broker_name(broker), exc)
        return None

    # ------------------------------------------------------------------
    # Market data helpers
    # ------------------------------------------------------------------
    def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        for broker in self.brokers:
            if not self._is_usable(broker):
                continue
            try:
                ltp = broker.get_ltp(symbol, exchange)
                if ltp:
                    self._mark_success(broker)
                    return ltp
            except Exception as exc:
                self._mark_failure(broker, str(exc))
        return None
