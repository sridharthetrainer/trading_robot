from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    broker_name: Optional[str]
    exchange: str
    order_type: str
    requested_price: float
    expected_price: float
    slippage_pct: float
    confidence: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "broker_name": self.broker_name,
            "exchange": self.exchange,
            "order_type": self.order_type,
            "requested_price": self.requested_price,
            "expected_price": self.expected_price,
            "slippage_pct": self.slippage_pct,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class SmartOrderRouter:
    """
    Smart order router for options/index trading.

    Responsibilities:
    - decide broker
    - decide MARKET vs LIMIT
    - reject bad fills when slippage/spread is too large
    - provide a consistent execution interface for TradeManager

    Expected broker_manager support:
    - get_ltp(symbol, exchange=...)
    - place_order(...)
    - optionally:
        - get_market_depth(symbol, exchange=...)
        - get_execution_broker(symbol, required_balance)
        - current_broker / primary_broker
    """

    def __init__(
        self,
        broker_manager,
        max_slippage_pct: float = 0.5,
        max_spread_pct: float = 0.8,
        limit_price_buffer_pct: float = 0.10,
        stale_quote_seconds: int = 3,
        prefer_limit_for_options: bool = True,
        retry_attempts: int = 2,
        retry_sleep_sec: float = 0.75,
    ) -> None:
        self.broker_manager = broker_manager
        self.max_slippage_pct = float(max_slippage_pct)
        self.max_spread_pct = float(max_spread_pct)
        self.limit_price_buffer_pct = float(limit_price_buffer_pct)
        self.stale_quote_seconds = int(stale_quote_seconds)
        self.prefer_limit_for_options = bool(prefer_limit_for_options)
        self.retry_attempts = int(retry_attempts)
        self.retry_sleep_sec = float(retry_sleep_sec)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def route_and_execute(
        self,
        symbol: str,
        side: str,
        qty: int,
        exchange: str = "NFO",
        reference_price: Optional[float] = None,
        confidence: float = 0.0,
        required_balance: float = 0.0,
        force_order_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Main entry point.

        Returns:
            {
                "success": True/False,
                "broker_name": ...,
                "order_id": ...,
                "order_type": ...,
                "price": ...,
                "decision": {...},
                "reason": ...,
            }
        """
        side = str(side).upper().strip()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")

        if qty <= 0:
            raise ValueError("qty must be positive")

        decision = self.build_route_decision(
            symbol=symbol,
            side=side,
            exchange=exchange,
            reference_price=reference_price,
            confidence=confidence,
            required_balance=required_balance,
            force_order_type=force_order_type,
        )

        if decision is None:
            return None

        logger.info(
            "Smart route | symbol=%s side=%s qty=%s broker=%s type=%s exp=%.2f slip=%.3f%% reason=%s",
            symbol,
            side,
            qty,
            decision.broker_name,
            decision.order_type,
            decision.expected_price,
            decision.slippage_pct,
            decision.reason,
        )

        for attempt in range(1, self.retry_attempts + 2):
            try:
                order_result = self._place_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    exchange=decision.exchange,
                    order_type=decision.order_type,
                    price=decision.requested_price,
                    required_balance=required_balance,
                )

                if not order_result:
                    raise RuntimeError("Broker manager returned no order result")

                broker_name, order_id = self._normalize_order_result(order_result)
                if not order_id:
                    raise RuntimeError("Order id missing")

                return {
                    "success": True,
                    "broker_name": broker_name or decision.broker_name,
                    "order_id": order_id,
                    "order_type": decision.order_type,
                    "price": decision.requested_price,
                    "decision": decision.to_dict(),
                    "reason": decision.reason,
                }

            except Exception as exc:
                logger.warning(
                    "Route execution failed | attempt=%s/%s symbol=%s err=%s",
                    attempt,
                    self.retry_attempts + 1,
                    symbol,
                    exc,
                )
                if attempt <= self.retry_attempts:
                    time.sleep(self.retry_sleep_sec)
                else:
                    return {
                        "success": False,
                        "broker_name": decision.broker_name,
                        "order_id": None,
                        "order_type": decision.order_type,
                        "price": decision.requested_price,
                        "decision": decision.to_dict(),
                        "reason": str(exc),
                    }

        return None

    def build_route_decision(
        self,
        symbol: str,
        side: str,
        exchange: str = "NFO",
        reference_price: Optional[float] = None,
        confidence: float = 0.0,
        required_balance: float = 0.0,
        force_order_type: Optional[str] = None,
    ) -> Optional[RouteDecision]:
        broker_name = self._resolve_broker_name(symbol, required_balance)
        quote = self._get_quote(symbol=symbol, exchange=exchange)

        if quote is None:
            logger.warning("No quote available for routing | symbol=%s", symbol)
            return None

        ltp = float(quote["ltp"])
        bid = quote.get("bid")
        ask = quote.get("ask")

        if reference_price is None or reference_price <= 0:
            reference_price = ltp

        spread_pct = self._compute_spread_pct(bid, ask, ltp)
        expected_slippage_pct = self._compute_expected_slippage_pct(
            side=side,
            reference_price=float(reference_price),
            bid=bid,
            ask=ask,
            ltp=ltp,
        )

        if spread_pct > self.max_spread_pct:
            logger.warning(
                "Spread too wide | symbol=%s spread=%.3f%% max=%.3f%%",
                symbol,
                spread_pct,
                self.max_spread_pct,
            )
            return None

        if expected_slippage_pct > self.max_slippage_pct:
            logger.warning(
                "Expected slippage too high | symbol=%s slip=%.3f%% max=%.3f%%",
                symbol,
                expected_slippage_pct,
                self.max_slippage_pct,
            )
            return None

        order_type = self._select_order_type(
            symbol=symbol,
            exchange=exchange,
            confidence=confidence,
            spread_pct=spread_pct,
            force_order_type=force_order_type,
        )

        requested_price = 0.0
        expected_price = ltp
        reason_parts = []

        if order_type == "LIMIT":
            requested_price = self._limit_price_from_quote(
                side=side,
                bid=bid,
                ask=ask,
                ltp=ltp,
            )
            expected_price = requested_price
            reason_parts.append("limit selected")
        else:
            requested_price = 0.0
            expected_price = ask if side == "BUY" and ask is not None else ltp
            expected_price = bid if side == "SELL" and bid is not None else expected_price
            reason_parts.append("market selected")

        if confidence >= 0.80:
            reason_parts.append("high confidence")
        elif confidence > 0:
            reason_parts.append("normal confidence")

        reason_parts.append(f"spread={spread_pct:.3f}%")
        reason_parts.append(f"slippage={expected_slippage_pct:.3f}%")

        return RouteDecision(
            broker_name=broker_name,
            exchange=exchange,
            order_type=order_type,
            requested_price=round(requested_price, 2) if requested_price else 0.0,
            expected_price=round(expected_price, 2),
            slippage_pct=round(expected_slippage_pct, 4),
            confidence=round(float(confidence), 4),
            reason=" | ".join(reason_parts),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_broker_name(self, symbol: str, required_balance: float) -> Optional[str]:
        try:
            if hasattr(self.broker_manager, "get_execution_broker"):
                broker = self.broker_manager.get_execution_broker(symbol, required_balance)
                if broker is not None and hasattr(broker, "get_name"):
                    return broker.get_name()

            if hasattr(self.broker_manager, "current_broker"):
                broker = getattr(self.broker_manager, "current_broker")
                if broker is not None and hasattr(broker, "get_name"):
                    return broker.get_name()

            if hasattr(self.broker_manager, "primary_broker"):
                broker = getattr(self.broker_manager, "primary_broker")
                if broker is not None and hasattr(broker, "get_name"):
                    return broker.get_name()
        except Exception as exc:
            logger.warning("Broker resolution failed: %s", exc)

        return None

    def _get_quote(self, symbol: str, exchange: str) -> Optional[Dict[str, Any]]:
        bid = ask = None
        ltp = None

        try:
            if hasattr(self.broker_manager, "get_market_depth"):
                depth = self.broker_manager.get_market_depth(symbol, exchange=exchange)
                if isinstance(depth, dict):
                    bid = self._safe_float(depth.get("bid"))
                    ask = self._safe_float(depth.get("ask"))
        except Exception as exc:
            logger.debug("Market depth unavailable for %s: %s", symbol, exc)

        try:
            ltp_result = self.broker_manager.get_ltp(symbol, exchange=exchange)
            if isinstance(ltp_result, tuple):
                ltp = self._safe_float(ltp_result[-1])
            else:
                ltp = self._safe_float(ltp_result)
        except Exception as exc:
            logger.warning("LTP fetch failed for %s: %s", symbol, exc)
            ltp = None

        if ltp is None:
            return None

        if bid is None:
            bid = ltp
        if ask is None:
            ask = ltp

        return {
            "ltp": ltp,
            "bid": bid,
            "ask": ask,
            "ts": time.time(),
        }

    def _select_order_type(
        self,
        symbol: str,
        exchange: str,
        confidence: float,
        spread_pct: float,
        force_order_type: Optional[str] = None,
    ) -> str:
        if force_order_type:
            return str(force_order_type).upper()

        if exchange.upper() == "NFO" and self.prefer_limit_for_options:
            if spread_pct > 0.20:
                return "LIMIT"
            if confidence < 0.70:
                return "LIMIT"
            return "MARKET"

        return "MARKET"

    def _limit_price_from_quote(
        self,
        side: str,
        bid: Optional[float],
        ask: Optional[float],
        ltp: float,
    ) -> float:
        side = side.upper()

        if bid is None or ask is None or bid <= 0 or ask <= 0:
            if side == "BUY":
                return round(ltp * (1 + self.limit_price_buffer_pct / 100.0), 2)
            return round(ltp * (1 - self.limit_price_buffer_pct / 100.0), 2)

        spread = max(ask - bid, 0.0)

        if side == "BUY":
            px = bid + min(spread * 0.60, ltp * (self.limit_price_buffer_pct / 100.0))
            return round(min(px, ask), 2)

        px = ask - min(spread * 0.60, ltp * (self.limit_price_buffer_pct / 100.0))
        return round(max(px, bid), 2)

    def _compute_spread_pct(
        self,
        bid: Optional[float],
        ask: Optional[float],
        ltp: float,
    ) -> float:
        if bid is None or ask is None or ltp <= 0:
            return 0.0
        return abs(ask - bid) / ltp * 100.0

    def _compute_expected_slippage_pct(
        self,
        side: str,
        reference_price: float,
        bid: Optional[float],
        ask: Optional[float],
        ltp: float,
    ) -> float:
        if reference_price <= 0:
            return 0.0

        side = side.upper()

        if side == "BUY":
            expected = ask if ask is not None else ltp
        else:
            expected = bid if bid is not None else ltp

        return abs(expected - reference_price) / reference_price * 100.0

    def _place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        exchange: str,
        order_type: str,
        price: float,
        required_balance: float,
    ):
        """
        Tries common broker manager interfaces.
        """
        if hasattr(self.broker_manager, "place_order_with_fallback"):
            return self.broker_manager.place_order_with_fallback(
                symbol=symbol,
                qty=qty,
                buy_sell=side,
                required_balance=required_balance,
                order_type=order_type,
                price=price,
                exchange=exchange,
            )

        if hasattr(self.broker_manager, "place_order"):
            return self.broker_manager.place_order(
                symbol=symbol,
                qty=qty,
                buy_sell=side,
                order_type=order_type,
                price=price,
                exchange=exchange,
            )

        raise AttributeError("BrokerManager has no supported order placement method")

    def _normalize_order_result(self, result):
        """
        Supports:
        - (order_id, broker_name)
        - (broker_name, order_id)
        - {"order_id": ..., "broker_name": ...}
        - raw order_id
        """
        if isinstance(result, dict):
            return result.get("broker_name"), result.get("order_id")

        if isinstance(result, tuple) and len(result) == 2:
            a, b = result
            a_str = str(a) if a is not None else ""
            b_str = str(b) if b is not None else ""

            # Heuristic: broker names are usually alphabetic; order ids often mixed
            if a_str.isalpha() or "angel" in a_str.lower() or "dhan" in a_str.lower():
                return a_str, b_str

            if b_str.isalpha() or "angel" in b_str.lower() or "dhan" in b_str.lower():
                return b_str, a_str

            # Fallback to old style used in your project examples
            return a_str, b_str

        return None, str(result)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    print("smart_order_router.py loaded successfully")
