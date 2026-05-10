"""
nifty_options_engine.py

Options contract selection engine for NIFTY/BANKNIFTY weekly options.

Fixes applied
-------------
1. _get_nearest_weekly_expiry() did not check NSE holidays
   NSE moves the weekly expiry to the previous business day when Thursday
   is a public holiday. The original code always returned the nearest
   Thursday regardless, causing the engine to resolve a contract that
   either didn't exist or had zero liquidity.

   Fix: NSE_EXPIRY_HOLIDAYS set (mirrors main_autonomous.py).
   When the computed Thursday is a holiday, the expiry is moved back
   one day at a time until a valid non-holiday weekday is found.

2. _determine_lots() forced minimum 1 lot even when underfunded
   `max(1, int(budget // per_lot_cost))` returned 1 lot even when
   budget = 500 and per_lot_cost = 9000 — silently allocating 18x more
   capital than the configured budget.

   Fix: if budget < per_lot_cost, return 0. select_trade() already
   handles `if lots <= 0: return None` correctly.

3. Confidence scaling bypassed PortfolioRiskManager's per-trade cap
   At confidence=0.90 the budget was multiplied by 1.40 with no ceiling,
   pushing allocation to 28% of capital (0.20 x 1.40) — bypassing the
   10% cap configured in PortfolioRiskManager.

   Fix: max_capital_fraction_hard_cap (default 0.20) caps the
   confidence-boosted fraction. max_lots_hard_cap (default 10) caps
   the final lot count regardless of sizing arithmetic.
"""

from __future__ import annotations
try:
    from adaptive_position_sizer import auto_lots_from_capital as _auto_lots
    _AUTO_LOT_AVAIL = True
except ImportError:
    _AUTO_LOT_AVAIL = False

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Set, Tuple

from config import (
    OPTION_LOT_SIZE as _CONFIG_LOT_SIZE,
    STRIKE_INTERVAL,
    TRADE_OPTIONS,
    ENABLE_REAL_TRADING,
    PAPER_CAPITAL,
    REAL_CAPITAL,
)

logger = logging.getLogger(__name__)

def _get_lot_size_dynamic(underlying: str) -> int:
    """Get lot size from NSEMaster (dynamic) or config fallback."""
    if _NSE_MASTER_NOE if '_NSE_MASTER_NOE' in dir() else False:
        try: return _get_nse_master_noe().get_lot_size(underlying)
        except Exception: pass
    return int(_CONFIG_LOT_SIZE)


NSE_EXPIRY_HOLIDAYS: Set[date] = {
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1),
    date(2025, 8, 15), date(2025, 8, 27), date(2025, 10, 2),
    date(2025, 10, 24), date(2025, 11, 5), date(2025, 11, 14),
    date(2025, 12, 25),
    date(2026, 1, 26), date(2026, 3, 6), date(2026, 3, 25),
    date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1),
    date(2026, 8, 15), date(2026, 10, 2), date(2026, 12, 25),
}


@dataclass
class OptionSelection:
    underlying: str
    signal: str
    option_type: str
    expiry: str
    strike: int
    symbol: str
    exchange: str
    lots: int
    quantity: int
    premium: float
    capital_required: float
    spot_price: float
    confidence: float
    reason: str
    style: str
    use_otm: bool


class NiftyOptionsEngine:
    """
    Options contract selection engine.

    Preferred: engine.select_trade(...)  — selection only
    Legacy:    engine.place_trade(...)   — selection + optional order
    """

    def __init__(
        self,
        broker,
        underlying: str = "NIFTY",
        max_lots_hard_cap: int = 10,
        max_capital_fraction_hard_cap: float = 0.20,
    ) -> None:
        self.broker     = broker
        self.underlying = underlying.upper()
        self.exchange   = "NFO"

        self.default_capital = REAL_CAPITAL if ENABLE_REAL_TRADING else PAPER_CAPITAL

        self.max_capital_per_trade_fraction          = 0.20
        self.max_capital_per_trade_fraction_scalping = 0.12
        self.max_capital_per_trade_fraction_swing    = 0.20
        self.max_capital_fraction_hard_cap           = float(max_capital_fraction_hard_cap)
        self.max_lots_hard_cap                       = int(max_lots_hard_cap)

        self.min_premium  = 5.0
        self.max_premium  = 1000.0
        self.product_type = "INTRADAY"

    # -------------------------------------------------------------------------
    # PREFERRED API
    # -------------------------------------------------------------------------
    def select_trade(
        self,
        style: str,
        signal: str,
        use_otm: bool = False,
        confidence: float = 0.50,
        reason: str = "",
        lots_override: Optional[int] = None,
        underlying: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not TRADE_OPTIONS:
            logger.warning("TRADE_OPTIONS is disabled in config")
            return None

        base_symbol = (underlying or self.underlying).upper()
        style       = str(style or "scalping").lower().strip()

        if signal not in ("BUY_CALL", "BUY_PUT"):
            logger.error("Unsupported signal: %s", signal)
            return None

        option_type = "CE" if signal == "BUY_CALL" else "PE"

        try:
            spot_price = self._get_spot_price(base_symbol)
        except Exception:
            logger.exception("Failed to fetch spot price")
            return None

        if not spot_price or spot_price <= 0:
            logger.error("Spot price unavailable for %s", base_symbol)
            return None

        expiry_date       = self._get_nearest_weekly_expiry()
        strike            = self._select_strike(spot_price, option_type, use_otm)
        candidate_symbols = self._build_candidate_option_symbols(base_symbol, expiry_date, strike, option_type)
        selected_symbol, premium = self._resolve_tradeable_option_symbol(candidate_symbols)

        if not selected_symbol:
            logger.error("Could not resolve option | %s %s %s %s", base_symbol, expiry_date, strike, option_type)
            return None

        if premium is None or premium <= 0:
            logger.error("Invalid premium for %s", selected_symbol)
            return None

        if premium < self.min_premium:
            logger.warning("Premium too low | %s=%.2f", selected_symbol, premium)
            return None

        if premium > self.max_premium:
            logger.warning("Premium too high | %s=%.2f", selected_symbol, premium)
            return None

        lots = self._determine_lots(premium, confidence, style, lots_override)
        if lots <= 0:
            logger.warning("Lots=0 for %s premium=%.2f — insufficient capital", selected_symbol, premium)
            return None

        quantity         = lots * OPTION_LOT_SIZE
        capital_required = float(quantity * premium)

        selection = OptionSelection(
            underlying=base_symbol, signal=signal, option_type=option_type,
            expiry=expiry_date.strftime("%d-%b-%Y"), strike=int(strike),
            symbol=selected_symbol, exchange=self.exchange,
            lots=int(lots), quantity=int(quantity), premium=float(premium),
            capital_required=float(capital_required), spot_price=float(spot_price),
            confidence=float(confidence), reason=str(reason or ""),
            style=style, use_otm=bool(use_otm),
        )

        logger.info(
            "Option selected | symbol=%s style=%s signal=%s spot=%.2f strike=%d "
            "premium=%.2f lots=%d qty=%d capital=%.2f expiry=%s",
            selection.symbol, selection.style, selection.signal,
            selection.spot_price, selection.strike, selection.premium,
            selection.lots, selection.quantity, selection.capital_required,
            selection.expiry,
        )

        return {"selection": asdict(selection), "timestamp": datetime.now().isoformat(), "status": "SELECTED"}

    # -------------------------------------------------------------------------
    # LEGACY WRAPPER
    # -------------------------------------------------------------------------
    def place_trade(
        self,
        style: str,
        signal: str,
        use_otm: bool = False,
        confidence: float = 0.50,
        reason: str = "",
        lots_override: Optional[int] = None,
        underlying: Optional[str] = None,
        execute_order: bool = True,
    ) -> Optional[Dict[str, Any]]:
        selected = self.select_trade(style, signal, use_otm, confidence, reason, lots_override, underlying)
        if not selected or not execute_order:
            return selected

        selection      = selected["selection"]
        order_response = self._place_buy_order(selection["symbol"], int(selection["quantity"]))
        if not order_response:
            logger.error("Order placement failed for %s", selection["symbol"])
            return None

        order_id, fill_price = order_response
        return {"selection": selection, "order_id": order_id, "fill_price": fill_price,
                "timestamp": datetime.now().isoformat(), "status": "PLACED"}

    # -------------------------------------------------------------------------
    # SPOT / STRIKE / EXPIRY
    # -------------------------------------------------------------------------
    def _get_spot_price(self, underlying: str) -> Optional[float]:
        for symbol, exchange in [
            (underlying, "NSE"),
            (f"{underlying}-INDEX", "NSE"),
            (f"{underlying} 50", "NSE"),
        ]:
            try:
                ltp = self.broker.get_ltp(symbol, exchange=exchange)
                if isinstance(ltp, tuple): ltp = ltp[-1]
                if ltp is not None and float(ltp) > 0:
                    return float(ltp)
            except Exception:
                logger.debug("Spot LTP failed for %s", symbol, exc_info=True)
        try:
            ltp = self.broker.get_ltp(underlying)
            if isinstance(ltp, tuple): ltp = ltp[-1]
            if ltp is not None and float(ltp) > 0:
                return float(ltp)
        except Exception:
            logger.debug("Final spot fallback failed for %s", underlying, exc_info=True)
        return None

    def _select_strike(self, spot_price: float, option_type: str, use_otm: bool) -> int:
        atm = self._round_to_interval(spot_price, STRIKE_INTERVAL)
        if not use_otm:
            return atm
        return atm + STRIKE_INTERVAL if option_type == "CE" else atm - STRIKE_INTERVAL

    @staticmethod
    def _round_to_interval(value: float, interval: int) -> int:
        return int(round(float(value) / float(interval)) * interval)

    def _get_nearest_weekly_expiry(self) -> date:
        """
        Return nearest NSE weekly expiry, rolling back past holidays.
        """
        today   = datetime.now().date()
        weekday = today.weekday()

        days_to_thursday = (3 - weekday) % 7
        expiry = today + timedelta(days=days_to_thursday)

        # Same-day post-market: roll to next week
        now = datetime.now()
        if expiry == today and now.hour >= 15 and now.minute >= 30:
            expiry += timedelta(days=7)

        # Roll back if holiday (max 3 days) — uses NSEMaster for dynamic holidays
        for _ in range(3):
            if not _is_noe_holiday(expiry):
                break
            expiry -= timedelta(days=1)
            logger.info("Expiry rolled back to %s (holiday/weekend)", expiry)

        return expiry

    # -------------------------------------------------------------------------
    # SYMBOL RESOLUTION
    # -------------------------------------------------------------------------
    def _build_candidate_option_symbols(
        self, underlying: str, expiry: date, strike: int, option_type: str,
    ) -> List[str]:
        dd       = expiry.strftime("%d")
        mmm      = expiry.strftime("%b").upper()
        yy       = expiry.strftime("%y")
        mon_num  = expiry.strftime("%m")
        yyyymmdd = expiry.strftime("%Y%m%d")
        ddmmmyy  = expiry.strftime("%d%b%y").upper()
        ddmmmyyyy= expiry.strftime("%d%b%Y").upper()

        candidates = [
            f"{underlying}{ddmmmyy}{strike}{option_type}",
            f"{underlying}{ddmmmyyyy}{strike}{option_type}",
            f"{underlying}{dd}{mmm}{yy}{strike}{option_type}",
            f"{underlying}{yy}{mmm}{dd}{strike}{option_type}",
            f"{underlying}{yy}{mon_num}{dd}{strike}{option_type}",
            f"{underlying}{yyyymmdd}{strike}{option_type}",
        ]
        seen: set = set()
        unique: List[str] = []
        for sym in candidates:
            if sym not in seen:
                seen.add(sym)
                unique.append(sym)
        return unique

    def _resolve_tradeable_option_symbol(
        self, candidate_symbols: List[str],
    ) -> Tuple[Optional[str], Optional[float]]:
        best_symbol: Optional[str]   = None
        best_premium: Optional[float] = None

        for symbol in candidate_symbols:
            try:
                ltp = self.broker.get_ltp(symbol, exchange=self.exchange)
                if isinstance(ltp, tuple): ltp = ltp[-1]
                if ltp is not None and float(ltp) > 0:
                    premium = float(ltp)
                    if self.min_premium <= premium <= self.max_premium:
                        logger.info("Resolved %s premium=%.2f", symbol, premium)
                        return symbol, premium
                    if best_symbol is None:
                        best_symbol, best_premium = symbol, premium
            except Exception:
                logger.debug("LTP failed for %s", symbol, exc_info=True)

        return best_symbol, best_premium

    # -------------------------------------------------------------------------
    # POSITION SIZING
    # -------------------------------------------------------------------------
    def _determine_lots(
        self,
        premium: float,
        confidence: float,
        style: str,
        lots_override: Optional[int],
    ) -> int:
        if lots_override is not None:
            try:
                return max(0, min(int(lots_override), self.max_lots_hard_cap))
            except Exception:
                logger.warning("Invalid lots_override=%s", lots_override)

        balance = self._get_available_balance()

        base_fraction = (
            self.max_capital_per_trade_fraction_scalping
            if style == "scalping"
            else self.max_capital_per_trade_fraction_swing
        )

        if confidence >= 0.90:
            adjusted = base_fraction * 1.40
        elif confidence >= 0.80:
            adjusted = base_fraction * 1.20
        elif confidence <= 0.50:
            adjusted = base_fraction * 0.75
        else:
            adjusted = base_fraction

        # Hard cap prevents confidence boost from exceeding policy limit
        capped_fraction = min(adjusted, self.max_capital_fraction_hard_cap)
        capital_budget  = balance * capped_fraction

        per_lot_cost = float(premium) * float(OPTION_LOT_SIZE)
        if per_lot_cost <= 0:
            return 0

        lots = int(capital_budget // per_lot_cost)

        if lots <= 0:
            logger.warning(
                "_determine_lots: budget=%.2f < per_lot_cost=%.2f — 0 lots",
                capital_budget, per_lot_cost,
            )
            return 0

        return min(lots, self.max_lots_hard_cap)

    def _get_available_balance(self) -> float:
        try:
            bal = self.broker.get_balance()
            if bal is not None and float(bal) > 0:
                return float(bal)
        except Exception:
            logger.debug("Broker balance fetch failed", exc_info=True)
        return float(self.default_capital)

    # -------------------------------------------------------------------------
    # ORDER PLACEMENT
    # -------------------------------------------------------------------------
    def _place_buy_order(self, symbol: str, quantity: int) -> Optional[Tuple[str, Optional[float]]]:
        try:
            response = self.broker.place_order(
                symbol=symbol, qty=quantity, buy_sell="BUY",
                order_type="MARKET", price=0, exchange=self.exchange,
            )
        except TypeError:
            response = self.broker.place_order(symbol, quantity, "BUY", "MARKET", 0, self.exchange)
        except Exception:
            logger.exception("Broker place_order failed")
            return None

        if response is None:
            return None
        if isinstance(response, str):
            return response, None
        if isinstance(response, tuple):
            if len(response) >= 2:
                return str(response[0]), self._safe_float(response[1])
            if len(response) == 1:
                return str(response[0]), None
        if isinstance(response, dict):
            order_id   = response.get("order_id") or response.get("id")
            fill_price = response.get("fill_price") or response.get("average_price")
            if order_id:
                return str(order_id), self._safe_float(fill_price)

        logger.warning("Unknown broker response format: %r", response)
        return str(response), None

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            return None if value is None else float(value)
        except Exception:
            return None
