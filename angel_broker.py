from __future__ import annotations

# Exchange routing for order placement
def get_exchange_for_symbol(symbol: str, order_type: str = "EQUITY") -> str:
    """
    Determine correct exchange for a symbol.
    BSE indices (SENSEX, BANKEX) use BSE/BFO.
    NSE indices and stocks use NSE/NFO.
    """
    s = symbol.upper()
    if s in ("SENSEX", "BANKEX"):
        return "BFO" if order_type == "FNO" else "BSE"
    if order_type == "FNO":
        return "NFO"
    return "NSE"

"""
angel_broker.py

Angel One broker adapter implementing the Broker interface.

Fixes applied
-------------
get_balance() returned 1_000_000 (₹10,00,000) as the paper-trade
fallback when the underlying balance fetch raised an exception.

The configured default capital is ₹1,00,000 (config.py CAPITAL=100000).
Returning a 10× larger value as the fallback caused every risk and
position-sizing calculation to believe the account was 10× larger:
- AdaptivePositionSizer would approve 10× more lots
- PortfolioRiskManager exposure limits would be 10× more permissive
- BrokerManager.get_execution_broker() balance ranking was wrong

Fix: read the configured capital from config.py on import and use that
as the fallback.  Falls back further to 100_000 if config is unavailable.
"""


import logging
from typing import Optional

from broker_interface import Broker
from angel import AngelOne
from quote_cache import QuoteCache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback balance — read from config so it matches the actual account size
# ---------------------------------------------------------------------------
_FALLBACK_PAPER_BALANCE = 100_000.0
try:
    import config as _cfg
    _FALLBACK_PAPER_BALANCE = float(
        getattr(_cfg, "PAPER_CAPITAL",
        getattr(_cfg, "CAPITAL", 100_000.0))
    )
except Exception:
    pass


class AngelBroker(Broker):
    def __init__(
        self,
        api_key:     str,
        client_id:   str,
        password:    str,
        totp_secret: str,
        paper_trade: bool = False,
    ) -> None:
        self.angel       = AngelOne(api_key, client_id, password, totp_secret, paper_trade)
        self.paper_trade = paper_trade
        self.quote_cache = QuoteCache(
            ttl_seconds              = 8.0,
            min_request_gap_seconds  = 0.35,
            max_size                 = 2000,
        )

    def place_order(
        self,
        symbol:     str,
        qty:        int,
        buy_sell:   str,
        order_type: str           = "MARKET",
        price:      float         = 0,
        exchange:   Optional[str] = None,
        order_tag:  str           = "",
    ) -> Optional[str]:
        result = self.angel.place_order(
            symbol=symbol, qty=qty, buy_sell=buy_sell,
            order_type=order_type, price=price, exchange=exchange,
            order_tag=order_tag,
        )
        # AngelOne.place_order returns tuple(order_id, fill_price) or None
        if result and isinstance(result, tuple):
            return result[0]
        return result

    def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        exchange = exchange or ("NFO" if ("CE" in symbol or "PE" in symbol) else "NSE")

        cached = self.quote_cache.get(symbol, exchange)
        if cached is not None:
            logger.debug("Quote cache hit | %s %s -> %s", exchange, symbol, cached)
            return cached

        self.quote_cache.wait_for_slot(symbol, exchange)
        ltp = self.angel.get_ltp(symbol, exchange=exchange)

        if ltp is not None:
            self.quote_cache.set(symbol, exchange, ltp)

        return ltp

    def get_balance(self) -> float:
        try:
            bal = self.angel.get_balance()
            if bal is not None and float(bal) > 0:
                return float(bal)
        except Exception as exc:
            logger.error("Balance fetch failed: %s", exc)

        # Paper-trade fallback uses the configured capital, not an arbitrary constant
        logger.debug(
            "Using fallback balance %.0f (paper_trade=%s)",
            _FALLBACK_PAPER_BALANCE, self.paper_trade,
        )
        return _FALLBACK_PAPER_BALANCE


    def place_sl_order(
        self,
        symbol:        str,
        qty:           int,
        buy_sell:      str,
        trigger_price: float,
        exchange:      str = "NFO",
        order_tag:     str = "",
    ) -> Optional[str]:
        """Place a broker-side SL-M (Stop-Loss Market) order."""
        try:
            return self.angel.place_sl_order(
                symbol=symbol, qty=qty, buy_sell=buy_sell,
                trigger_price=trigger_price, exchange=exchange,
                order_tag=order_tag,
            )
        except Exception as exc:
            logger.error("place_sl_order failed: %s", exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""
        try:
            return self.angel.cancel_order(order_id)
        except Exception as exc:
            logger.error("cancel_order failed: %s", exc)
            return False

    def poll_order_fill(
        self, order_id: str, timeout_sec: float = 10.0
    ) -> Optional[dict]:
        """Poll until order is COMPLETE (or timeout). Returns order dict or None."""
        try:
            return self.angel.poll_order_fill(order_id, timeout_sec)
        except Exception as exc:
            logger.error("poll_order_fill failed: %s", exc)
            return None

    def get_order_status(self, order_id: str, exchange: str = "NFO") -> Optional[str]:
        """Return current broker status string for order_id."""
        try:
            return self.angel.get_order_status(order_id, exchange)
        except Exception as exc:
            logger.debug("get_order_status failed: %s", exc)
            return None

    def is_connected(self) -> bool:
        return self.angel.obj is not None  # always check real connection

    def get_name(self) -> str:
        return "AngelOne"
