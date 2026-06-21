from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class Broker(ABC):
    """
    Abstract broker contract.

    All broker adapters MUST implement this interface.
    """

    # ============================================================
    # CORE METHODS (MANDATORY)
    # ============================================================

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: int,
        buy_sell: str,
        order_type: str = "MARKET",
        price: float = 0,
        exchange: Optional[str] = None,
    ) -> Optional[str]:
        """
        Place an order.

        Returns:
            order_id (str) if success
            None if failed
        """
        raise NotImplementedError

    @abstractmethod
    def get_ltp(
        self,
        symbol: str,
        exchange: Optional[str] = None
    ) -> Optional[float]:
        """
        Latest traded price.
        """
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> float:
        """
        Available capital / margin.
        """
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        """
        Broker connection health.
        """
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        """
        Short broker identifier.
        """
        raise NotImplementedError

    # ============================================================
    # ADVANCED METHODS (SHOULD IMPLEMENT)
    # ============================================================

    def get_market_depth(
        self,
        symbol: str,
        exchange: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Market depth for smart routing.

        Expected format:
        {
            "bid": float,
            "ask": float,
            "last_price": float,
            "volume": float
        }
        """
        return None

    def get_order_status(
        self,
        order_id: str,
        exchange: Optional[str] = None
    ) -> Optional[Any]:
        """
        Order status lookup.

        Can return:
        - "FILLED"
        - "REJECTED"
        - "PENDING"
        - dict or tuple (normalized later)
        """
        return None

    def cancel_order(
        self,
        order_id: str,
        exchange: Optional[str] = None
    ) -> bool:
        """
        Cancel an order.
        """
        return False

    # ============================================================
    # HELPER METHODS (DEFAULT IMPLEMENTATION)
    # ============================================================

    def place_order_with_details(
        self,
        symbol: str,
        qty: int,
        buy_sell: str,
        order_type: str = "MARKET",
        price: float = 0,
        exchange: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Wrapper that returns structured response.
        """
        order_id = self.place_order(
            symbol=symbol,
            qty=qty,
            buy_sell=buy_sell,
            order_type=order_type,
            price=price,
            exchange=exchange,
        )

        return {
            "success": order_id is not None,
            "order_id": order_id,
            "fill_price": None,
            "status": "PLACED" if order_id else "FAILED",
            "raw_response": None,
        }

    def wait_for_terminal_status(
        self,
        order_id: str,
        exchange: Optional[str] = None,
        timeout_sec: int = 10,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Poll until order completes.
        """
        import time

        start = time.time()

        while time.time() - start < timeout_sec:
            status = self.get_order_status(order_id, exchange)

            if status:
                status_str = str(status).upper()

                if status_str in ("FILLED", "COMPLETE"):
                    return {
                        "order_id": order_id,
                        "status": "FILLED",
                        "terminal": True,
                        "timed_out": False,
                    }

                if status_str in ("REJECTED", "CANCELLED"):
                    return {
                        "order_id": order_id,
                        "status": status_str,
                        "terminal": True,
                        "timed_out": False,
                    }

            time.sleep(poll_interval_sec)

        return {
            "order_id": order_id,
            "status": "TIMEOUT",
            "terminal": False,
            "timed_out": True,
        }
