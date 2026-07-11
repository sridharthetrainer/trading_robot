"""Safer live option-entry execution.

The router is intentionally narrow: it only handles option entry orders. It uses
bounded LIMIT orders, confirms fills, allows at most one reprice, and cancels
unfilled orders. Paper trades can still be journalled elsewhere for learning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

AUDIT_FILE = os.getenv("OPTION_EXECUTION_AUDIT_FILE", "option_execution_audit.jsonl")


@dataclass
class OptionExecutionReport:
    ok: bool
    symbol: str
    side: str
    qty: int
    broker_name: str = ""
    order_id: str = ""
    status: str = ""
    reason: str = ""
    decision_price: float = 0.0
    first_limit_price: float = 0.0
    final_limit_price: float = 0.0
    fill_price: float = 0.0
    filled_qty: int = 0
    attempts: int = 0
    slippage_pct: float = 0.0
    implementation_shortfall: float = 0.0
    latency_sec: float = 0.0
    cancelled_order_ids: list[str] = field(default_factory=list)
    raw_fill: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _round_to_tick(price: float, tick: float = 0.05) -> float:
    p = _f(price, 0.0)
    if p <= 0:
        return 0.0
    return round(round(p / tick) * tick, 2)


def _audit(payload: Dict[str, Any], path: str = AUDIT_FILE) -> None:
    try:
        row = {"ts": time.time(), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **payload}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("option execution audit write failed: %s", exc)


def _normalise_order_id(result: Any) -> str:
    if isinstance(result, tuple) and result:
        return str(result[0] or "")
    if isinstance(result, dict):
        return str(result.get("order_id") or result.get("orderid") or result.get("id") or "")
    return str(result or "")


def _fill_status(fill: Any) -> str:
    if not isinstance(fill, dict):
        return "timeout"
    return str(
        fill.get("status")
        or fill.get("orderstatus")
        or fill.get("order_status")
        or ""
    ).upper()


def _is_complete(fill: Any) -> bool:
    return _fill_status(fill) in {"COMPLETE", "FILLED", "EXECUTED"}


def _fill_price(fill: Any, fallback: float) -> float:
    if not isinstance(fill, dict):
        return fallback
    return _f(
        fill.get("averageprice")
        or fill.get("avg_price")
        or fill.get("average_price")
        or fill.get("fill_price")
        or fallback,
        fallback,
    )


def _fill_qty(fill: Any, fallback: int) -> int:
    if not isinstance(fill, dict):
        return 0
    return _i(
        fill.get("filled_qty")
        or fill.get("filledshares")
        or fill.get("filled_quantity")
        or fill.get("quantity")
        or fallback,
        fallback,
    )


def _limit_for_buy(*, decision_price: float, slippage_pct: float, reprice: bool) -> float:
    base = _f(decision_price)
    if base <= 0:
        return 0.0
    multiplier = slippage_pct * (1.0 if reprice else 0.5)
    return _round_to_tick(base * (1.0 + max(0.0, multiplier)))


def _strict_filled_qty(fill: Any) -> int:
    """Filled shares ONLY from explicit fill fields — never the order qty.

    Used after a cancel attempt, where a CANCELLED order dict still carries
    filledshares>0 when the order part-filled before the cancel landed.
    Falling back to order quantity here would fabricate a full fill.
    """
    if not isinstance(fill, dict):
        return 0
    return _i(
        fill.get("filledshares")
        or fill.get("filled_qty")
        or fill.get("filled_quantity")
        or 0,
        0,
    )


def _orderbook_row(broker: Any, order_id: str) -> Optional[Dict[str, Any]]:
    """Raw order-book row for order_id regardless of status.

    poll_order_fill returns None for cancelled orders, hiding the
    filledshares of a partial-fill-then-cancel — this reads the row itself.
    Best-effort: returns None on any failure.
    """
    try:
        angel = getattr(broker, "angel", None) or broker
        obj = getattr(angel, "obj", None)
        if obj is None or getattr(angel, "paper_trade", False):
            return None
        lock = getattr(angel, "_lock", None)
        if lock is not None:
            with lock:
                orders = obj.orderBook()
        else:
            orders = obj.orderBook()
        if isinstance(orders, dict):
            orders = orders.get("data", [])
        for o in orders or []:
            if isinstance(o, dict) and str(o.get("orderid", "")) == str(order_id):
                return o
    except Exception as exc:
        logger.debug("orderbook row lookup failed | order_id=%s error=%s", order_id, exc)
    return None


def execute_option_entry(
    *,
    broker: Any,
    symbol: str,
    qty: int,
    side: str = "BUY",
    exchange: str = "NFO",
    decision_price: float = 0.0,
    order_tag: str = "",
    max_slippage_pct: float = 0.0075,
    wait_sec: float = 4.0,
    reprice_attempts: int = 1,
    max_signal_age_sec: float = 90.0,
    signal_generated_at: float = 0.0,
) -> OptionExecutionReport:
    started = time.time()
    side = str(side or "BUY").upper()
    symbol = str(symbol or "").upper()
    qty = int(qty or 0)
    report = OptionExecutionReport(
        ok=False, symbol=symbol, side=side, qty=qty,
        decision_price=round(_f(decision_price), 4),
    )

    if side != "BUY":
        report.reason = "option_router_entry_supports_buy_only"
        _audit(report.to_dict())
        return report
    if broker is None:
        report.reason = "broker_unavailable"
        _audit(report.to_dict())
        return report
    if qty <= 0 or not symbol:
        report.reason = "invalid_symbol_or_qty"
        _audit(report.to_dict())
        return report
    if signal_generated_at and max_signal_age_sec > 0:
        age = time.time() - float(signal_generated_at)
        if age > max_signal_age_sec:
            report.reason = "stale_signal_before_order"
            report.status = f"age_sec={round(age, 2)}"
            _audit(report.to_dict())
            return report

    try:
        report.broker_name = broker.get_name() if hasattr(broker, "get_name") else type(broker).__name__
    except Exception:
        report.broker_name = type(broker).__name__

    attempts = 1 + max(0, int(reprice_attempts or 0))
    for attempt in range(1, attempts + 1):
        report.attempts = attempt
        try:
            ltp = _f(broker.get_ltp(symbol, exchange=exchange) if hasattr(broker, "get_ltp") else 0.0)
        except Exception as exc:
            report.reason = "ltp_fetch_failed"
            report.status = str(exc)[:160]
            break

        max_entry = _f(decision_price) * (1.0 + max(0.0, float(max_slippage_pct or 0.0)))
        if _f(decision_price) > 0 and ltp > max_entry:
            report.reason = "price_moved_beyond_slippage_cap"
            report.status = f"ltp={round(ltp, 4)} max_entry={round(max_entry, 4)}"
            break

        limit_price = _limit_for_buy(
            decision_price=_f(decision_price),
            slippage_pct=max(0.0, float(max_slippage_pct or 0.0)),
            reprice=attempt > 1,
        )
        if limit_price <= 0:
            report.reason = "invalid_limit_price"
            break
        if attempt == 1:
            report.first_limit_price = limit_price
        report.final_limit_price = limit_price

        try:
            placed = broker.place_order(
                symbol=symbol,
                qty=qty,
                buy_sell=side,
                order_type="LIMIT",
                price=limit_price,
                exchange=exchange,
                order_tag=order_tag,
            )
            order_id = _normalise_order_id(placed)
        except Exception as exc:
            report.reason = "broker_place_order_failed"
            report.status = str(exc)[:160]
            break

        if not order_id:
            report.reason = "broker_returned_no_order_id"
            break

        report.order_id = order_id
        fill = None
        try:
            if hasattr(broker, "poll_order_fill"):
                fill = broker.poll_order_fill(order_id, timeout_sec=max(1.0, float(wait_sec or 4.0)))
        except Exception as exc:
            report.status = f"poll_error:{str(exc)[:120]}"

        if _is_complete(fill):
            fill_price = _fill_price(fill, limit_price)
            filled_qty = _fill_qty(fill, qty)
            report.ok = filled_qty > 0
            report.reason = "filled" if report.ok else "zero_fill_qty"
            report.status = _fill_status(fill)
            report.fill_price = round(fill_price, 4)
            report.filled_qty = filled_qty
            report.raw_fill = fill if isinstance(fill, dict) else {}
            if report.decision_price > 0 and fill_price > 0:
                report.slippage_pct = round((fill_price - report.decision_price) / report.decision_price, 6)
                report.implementation_shortfall = round((fill_price - report.decision_price) * filled_qty, 2)
            report.latency_sec = round(time.time() - started, 3)
            _audit(report.to_dict())
            return report

        try:
            if hasattr(broker, "cancel_order"):
                broker.cancel_order(order_id)
                report.cancelled_order_ids.append(order_id)
        except Exception as exc:
            logger.warning("option router cancel failed | order_id=%s error=%s", order_id, exc)

        # Fill-after-cancel race: the order can complete (or part-fill)
        # between the poll timeout and the cancel landing. Returning not-ok
        # while shares sit at the broker creates an UNTRACKED live position —
        # so re-check once and honour whatever actually filled.
        try:
            # The row is read directly (not via poll_order_fill, which
            # returns None for terminal states and would hide the
            # filledshares of a partial-fill-then-cancel).
            recheck = _orderbook_row(broker, order_id)
            filled_after = (
                _fill_qty(recheck, qty) if _is_complete(recheck)
                else _strict_filled_qty(recheck)
            )
            if filled_after > 0:
                fill_price = _fill_price(recheck, limit_price)
                report.ok = True
                report.reason = "filled_after_cancel_race"
                report.status = _fill_status(recheck)
                report.fill_price = round(fill_price, 4)
                report.filled_qty = filled_after
                report.raw_fill = recheck if isinstance(recheck, dict) else {}
                if report.decision_price > 0 and fill_price > 0:
                    report.slippage_pct = round((fill_price - report.decision_price) / report.decision_price, 6)
                    report.implementation_shortfall = round((fill_price - report.decision_price) * filled_after, 2)
                report.latency_sec = round(time.time() - started, 3)
                _audit(report.to_dict())
                return report
        except Exception as exc:
            logger.warning("option router post-cancel recheck failed | order_id=%s error=%s", order_id, exc)

        if attempt < attempts:
            report.reason = "not_filled_repricing"
            continue
        report.reason = "not_filled_cancelled"
        report.status = _fill_status(fill)

    report.latency_sec = round(time.time() - started, 3)
    _audit(report.to_dict())
    return report
