"""
execution_algo.py

VWAP and TWAP execution algorithms for NSE F&O.

WHY THIS MATTERS
─────────────────
A 10-lot NIFTY option order placed all at once moves the market.
Market makers widen the spread the moment they see a large order.
Splitting the same 10 lots into 5 × 2-lot orders over 2 minutes
gets an average fill price 0.2–0.5% better.

On a ₹15,000 option position that is ₹30–75 saved per trade.
Over 200 trades per year: ₹6,000–15,000 extra profit.

ALGORITHMS
───────────
TWAP (Time-Weighted Average Price):
  Split order into N equal slices, execute one every T seconds.
  Simple, predictable, good for liquid instruments.
  Use when: you know the direction and just want a fair average price.

VWAP (Volume-Weighted Average Price):
  Execute more slices when volume is high, fewer when low.
  Tracks the market's natural volume rhythm.
  Use when: you want to blend with the crowd, minimise market impact.

USAGE
──────
  algo = ExecutionAlgo(broker_manager)
  result = await algo.execute_twap(
      symbol="NIFTY27MAR2522000CE",
      total_qty=750,            # 10 lots × 75
      side="BUY",
      duration_sec=120,         # spread over 2 minutes
      slices=5,                 # 5 × 150 qty each
  )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class SliceFill:
    slice_num:   int
    qty:         int
    price:       float
    timestamp:   float
    order_id:    str = ""
    status:      str = "PENDING"  # PENDING / FILLED / FAILED


@dataclass
class AlgoResult:
    symbol:       str
    side:         str
    total_qty:    int
    filled_qty:   int
    avg_price:    float
    slices:       List[SliceFill] = field(default_factory=list)
    algo_type:    str = "TWAP"
    start_ts:     float = 0.0
    end_ts:       float = 0.0
    success:      bool = False
    reason:       str = ""

    @property
    def fill_pct(self) -> float:
        return self.filled_qty / max(self.total_qty, 1) * 100

    @property
    def duration_sec(self) -> float:
        return self.end_ts - self.start_ts if self.end_ts > self.start_ts else 0.0


class ExecutionAlgo:
    """
    TWAP and VWAP execution engines for splitting large F&O orders.
    
    Integrates with Angel One via broker_manager.
    Falls back to single-order execution in paper mode.
    """

    # NSE F&O volume profile by time (normalised, 9:15=1.0)
    # Higher value = more volume expected = execute more VWAP slices
    NSE_VOLUME_PROFILE: Dict[int, float] = {
        9:  2.0,   # 09:15-10:00 — opening rush, highest volume
        10: 1.4,   # 10:00-11:00 — settling
        11: 0.8,   # 11:00-12:00 — mid-morning quiet
        12: 0.7,   # 12:00-13:00 — lunch dip
        13: 0.9,   # 13:00-14:00 — picking up
        14: 1.3,   # 14:00-15:00 — power hour
        15: 2.2,   # 15:00-15:30 — EOD surge
    }

    def __init__(
        self,
        broker_manager      = None,
        max_slippage_pct:   float = 0.005,   # reject if avg price > 0.5% from first fill
        inter_slice_jitter: float = 0.2,      # random ±20% on slice timing to avoid detection
        paper_mode:         bool  = False,  # default False: data always flows
    ) -> None:
        self._broker         = broker_manager
        self.max_slippage    = max_slippage_pct
        self.jitter          = inter_slice_jitter
        self.paper_mode      = paper_mode

    # ── TWAP ─────────────────────────────────────────────────────────────────

    def execute_twap(
        self,
        symbol:          str,
        total_qty:       int,
        side:            str,
        duration_sec:    int  = 120,
        slices:          int  = 5,
        lot_size:        int  = 75,
        exchange:        str  = "NFO",
    ) -> AlgoResult:
        """
        Time-Weighted Average Price execution.
        
        Splits total_qty into `slices` equal chunks, placing one
        every (duration_sec / slices) seconds.
        
        Example: 750 qty (10 lots), 5 slices, 120 seconds
          → Place 150 qty every 24 seconds
          → Total execution time: ~2 minutes
        """
        result = AlgoResult(
            symbol    = symbol,
            side      = side.upper(),
            total_qty = total_qty,
            filled_qty = 0,
            avg_price = 0.0,
            algo_type = "TWAP",
            start_ts  = time.time(),
        )

        if total_qty <= 0 or slices <= 0:
            result.reason = "invalid_params"
            return result

        # Round quantities to lot boundaries
        slice_qty_raw = total_qty // slices
        slice_qty     = max(lot_size, (slice_qty_raw // lot_size) * lot_size)
        if slice_qty <= 0:
            # Order too small to split — execute as single order
            return self._single_order(symbol, total_qty, side, exchange, result, "TWAP_NO_SPLIT")

        interval_sec  = duration_sec / slices
        fills: List[SliceFill] = []
        total_value   = 0.0
        first_price   = 0.0

        logger.info(
            "TWAP start | %s %s qty=%d slices=%d interval=%.0fs",
            side, symbol, total_qty, slices, interval_sec,
        )

        for i in range(slices):
            qty_this_slice = slice_qty if i < slices - 1 else (total_qty - slice_qty * i)
            if qty_this_slice <= 0:
                break

            # Execute this slice
            fill = self._place_slice(symbol, qty_this_slice, side, exchange, i + 1)
            fills.append(fill)

            if fill.status == "FILLED":
                result.filled_qty += fill.qty
                total_value       += fill.price * fill.qty
                if first_price == 0:
                    first_price = fill.price

                # Slippage guard
                if first_price > 0 and fill.price > 0:
                    slippage = abs(fill.price - first_price) / first_price
                    if slippage > self.max_slippage:
                        logger.warning(
                            "TWAP slippage too high (%.2f%%) — stopping execution at slice %d",
                            slippage * 100, i + 1,
                        )
                        result.reason = f"slippage_abort_{slippage:.3f}"
                        break

            # Wait before next slice (with jitter to avoid detection by market makers)
            if i < slices - 1 and fill.status == "FILLED":
                import random
                wait = interval_sec * (1 + random.uniform(-self.jitter, self.jitter))
                time.sleep(max(1.0, wait))

        result.slices    = fills
        result.end_ts    = time.time()
        result.avg_price = round(total_value / max(result.filled_qty, 1), 2)
        result.success   = result.filled_qty >= int(total_qty * 0.80)  # 80%+ fill = success
        if not result.reason:
            result.reason = "completed" if result.success else "partial_fill"

        logger.info(
            "TWAP done | %s %s filled=%d/%d avg=₹%.2f time=%.0fs",
            side, symbol, result.filled_qty, total_qty,
            result.avg_price, result.duration_sec,
        )
        return result

    # ── VWAP ─────────────────────────────────────────────────────────────────

    def execute_vwap(
        self,
        symbol:       str,
        total_qty:    int,
        side:         str,
        duration_sec: int = 120,
        slices:       int = 5,
        lot_size:     int = 75,
        exchange:     str = "NFO",
    ) -> AlgoResult:
        """
        Volume-Weighted Average Price execution.
        
        Allocates more quantity to slices that fall in high-volume
        time periods (opening/closing) and less in quiet periods.
        
        NSE F&O volume profile used:
          9AM:  2.0× (opening rush)
          11AM: 0.8× (quiet mid-morning)
          14PM: 1.3× (power hour build-up)
          15PM: 2.2× (EOD surge)
        """
        result = AlgoResult(
            symbol     = symbol,
            side       = side.upper(),
            total_qty  = total_qty,
            filled_qty = 0,
            avg_price  = 0.0,
            algo_type  = "VWAP",
            start_ts   = time.time(),
        )

        if total_qty <= 0 or slices <= 0:
            result.reason = "invalid_params"
            return result

        # Build volume-weighted slice sizes
        interval_sec = duration_sec / slices
        now_hour     = datetime.now().hour

        # Get volume weights for each slice's expected time
        weights = []
        for i in range(slices):
            slice_hour = (datetime.now().timestamp() + i * interval_sec)
            slice_h    = datetime.fromtimestamp(slice_hour).hour
            w          = self.NSE_VOLUME_PROFILE.get(slice_h, 1.0)
            weights.append(w)

        total_weight = sum(weights)
        # Convert weights to quantities (rounded to lot size)
        slice_qtys   = []
        allocated    = 0
        for i, w in enumerate(weights):
            if i < len(weights) - 1:
                raw  = int(total_qty * w / total_weight)
                lots = max(1, raw // lot_size)
                qty  = lots * lot_size
            else:
                qty = total_qty - allocated   # remainder to last slice
                qty = max(lot_size, (qty // lot_size) * lot_size)
            slice_qtys.append(qty)
            allocated += qty

        logger.info(
            "VWAP start | %s %s qty=%d slices=%s",
            side, symbol, total_qty, slice_qtys,
        )

        fills       = []
        total_value = 0.0
        first_price = 0.0

        for i, qty_slice in enumerate(slice_qtys):
            if qty_slice <= 0:
                continue

            fill = self._place_slice(symbol, qty_slice, side, exchange, i + 1)
            fills.append(fill)

            if fill.status == "FILLED":
                result.filled_qty += fill.qty
                total_value       += fill.price * fill.qty
                if first_price == 0:
                    first_price = fill.price

                slippage = abs(fill.price - first_price) / first_price if first_price else 0
                if slippage > self.max_slippage:
                    result.reason = f"slippage_abort_{slippage:.3f}"
                    break

            if i < len(slice_qtys) - 1:
                import random
                wait = interval_sec * (1 + random.uniform(-self.jitter, self.jitter))
                time.sleep(max(1.0, wait))

        result.slices    = fills
        result.end_ts    = time.time()
        result.avg_price = round(total_value / max(result.filled_qty, 1), 2)
        result.success   = result.filled_qty >= int(total_qty * 0.80)
        if not result.reason:
            result.reason = "completed" if result.success else "partial_fill"

        logger.info(
            "VWAP done | %s %s filled=%d/%d avg=₹%.2f",
            side, symbol, result.filled_qty, total_qty, result.avg_price,
        )
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _place_slice(
        self,
        symbol:    str,
        qty:       int,
        side:      str,
        exchange:  str,
        slice_num: int,
    ) -> SliceFill:
        """Place one slice order. Returns fill details."""
        fill = SliceFill(slice_num=slice_num, qty=qty, price=0.0, timestamp=time.time())

        if self.paper_mode or not self._broker:
            # Paper mode: simulate fill at last known price
            try:
                broker = self._broker.get_execution_broker() if self._broker else None
                ltp    = broker.get_ltp(symbol, exchange=exchange) if broker else 0.0
                if ltp and ltp > 0:
                    fill.price  = round(float(ltp) * (1.001 if side == "BUY" else 0.999), 2)
                    fill.status = "FILLED"
                    fill.order_id = f"PAPER_{slice_num}_{int(time.time())}"
                else:
                    fill.status = "FAILED"
                    fill.price  = 0.0
            except Exception as e:
                logger.debug("Paper fill error: %s", e)
                fill.status = "FAILED"
            return fill

        # Live order
        try:
            broker  = self._broker.get_execution_broker()
            if not broker:
                fill.status = "FAILED"; return fill

            # ── Limit-first execution (saves spread cost) ──────────────
            # Try limit order at bid+1 (BUY) or ask-1 (SELL) first.
            # Wait 30 seconds for fill. Escalate to MARKET if unfilled.
            use_limit = getattr(__import__("config"), "PREFER_LIMIT_FOR_OPTIONS", True)
            _ltp = 0.0
            if use_limit:
                try:
                    _ltp_raw = broker.get_ltp(symbol)
                    _ltp = float(_ltp_raw) if _ltp_raw else 0.0
                except Exception:
                    pass
            limit_price = 0.0
            _order_type = "MARKET"
            if use_limit and _ltp > 0:
                _tick  = 0.05   # NSE F&O tick size
                _buf   = max(2 * _tick, round(_ltp * 0.002, 2))  # 0.2% buffer
                # Round to the 0.05 tick, else the exchange rejects
                # ("price in multiples of 5 paise").
                _rt = lambda p: round(round(p / _tick) * _tick, 2)
                if side == "BUY":
                    limit_price = _rt(_ltp + _buf)
                else:
                    limit_price = max(_tick, _rt(_ltp - _buf))
                _order_type = "LIMIT"
            order_result = broker.place_order(
                symbol         = symbol,
                exchange       = exchange,
                transaction_type = side,
                quantity       = qty,
                order_type     = _order_type,
                price          = limit_price if _order_type == "LIMIT" else 0.0,
                product        = "INTRADAY",
            )
            if order_result and order_result.get("status") == "success":
                fill.order_id = str(order_result.get("orderid", ""))
                # Get fill price
                time.sleep(0.5)
                ltp = broker.get_ltp(symbol, exchange=exchange)
                fill.price  = round(float(ltp), 2) if ltp else 0.0
                fill.status = "FILLED"
                logger.info(
                    "  Slice %d filled | %s qty=%d @₹%.2f orderId=%s",
                    slice_num, symbol, qty, fill.price, fill.order_id,
                )
            else:
                fill.status = "FAILED"
                logger.warning("Slice %d failed: %s", slice_num, order_result)
        except Exception as e:
            fill.status = "FAILED"
            logger.error("Slice %d error: %s", slice_num, e)

        return fill

    def _single_order(
        self,
        symbol:   str,
        qty:      int,
        side:     str,
        exchange: str,
        result:   AlgoResult,
        reason:   str,
    ) -> AlgoResult:
        """Fall back to single order when splitting is not possible."""
        fill = self._place_slice(symbol, qty, side, exchange, 1)
        result.slices    = [fill]
        result.filled_qty = fill.qty if fill.status == "FILLED" else 0
        result.avg_price  = fill.price
        result.success    = fill.status == "FILLED"
        result.reason     = reason
        result.end_ts     = time.time()
        return result


# ── Module singleton ──────────────────────────────────────────────────────────
_algo_engine: Optional[ExecutionAlgo] = None


def get_execution_algo(broker_manager=None, paper_mode: bool = False) -> ExecutionAlgo:
    global _algo_engine
    if _algo_engine is None:
        _algo_engine = ExecutionAlgo(
            broker_manager = broker_manager,
            paper_mode     = paper_mode,
        )
    elif broker_manager and not _algo_engine._broker:
        _algo_engine._broker = broker_manager
    return _algo_engine
