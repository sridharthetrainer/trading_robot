"""
advisory_engine.models
======================
Shared dataclasses / enums for the advisory-signal pipeline.

Layer flow:  raw text -> AdvisorySignal (L1) -> MarketSnapshot (L2)
             -> ValidationReport (L3) -> OrderPlan / ManagedPosition (L4)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

import pandas as pd


# ── Enums ──────────────────────────────────────────────────────────────────────

class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Segment(str, Enum):
    EQUITY = "EQUITY"
    DERIVATIVE_CALL = "DERIVATIVE_CALL"
    DERIVATIVE_PUT = "DERIVATIVE_PUT"


class StrategyTag(str, Enum):
    BOLLINGER_MACD = "BOLLINGER_MACD"   # 1H trend reversal / breakdown
    VWAP_OI = "VWAP_OI"                 # 15M intraday options momentum squeeze
    TRENDLINE_EMA = "TRENDLINE_EMA"     # Daily structural breakout
    UNKNOWN = "UNKNOWN"


class OrderState(str, Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    ENTRY_PLACED = "ENTRY_PLACED"
    ENTRY_FILLED = "ENTRY_FILLED"
    EXITS_PLACED = "EXITS_PLACED"       # SL + target live, OCO-linked
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_STOP = "CLOSED_STOP"
    CANCELLED = "CANCELLED"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"                     # default — simulate fills, never call broker
    LIVE = "LIVE"                       # route through AngelOne (requires explicit opt-in)


# ── Layer 1 output ─────────────────────────────────────────────────────────────

@dataclass
class AdvisorySignal:
    """Standardized payload produced by the Layer-1 parser."""
    timestamp: Optional[str] = None
    action: Optional[Action] = None
    tradable_instrument: Optional[str] = None   # raw textual name from the message
    segment: Segment = Segment.EQUITY
    expiry: Optional[str] = None
    strike: Optional[float] = None
    entry_min: Optional[float] = None
    entry_max: Optional[float] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    strategy_tag: StrategyTag = StrategyTag.UNKNOWN
    raw_text: str = ""
    parse_errors: List[str] = field(default_factory=list)

    # filled by Layer 2 token resolution
    resolved_symbol: Optional[str] = None       # e.g. "MOTHERSON"
    resolved_token: Optional[str] = None
    resolve_score: float = 0.0

    @property
    def is_derivative(self) -> bool:
        return self.segment in (Segment.DERIVATIVE_CALL, Segment.DERIVATIVE_PUT)

    @property
    def is_complete(self) -> bool:
        """Minimum fields needed to even attempt validation/execution."""
        base = (self.action is not None
                and self.tradable_instrument
                and self.entry_min is not None
                and self.entry_max is not None
                and self.stop_loss is not None
                and self.target is not None)
        if self.is_derivative:
            return bool(base and self.strike is not None)
        return bool(base)

    def to_json(self) -> str:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Enum):
                d[k] = v.value
        return json.dumps(d, indent=2, default=str)


# ── Layer 2 output ─────────────────────────────────────────────────────────────

@dataclass
class MarketSnapshot:
    """Aligned multi-timeframe view of the *underlying* with indicators attached."""
    symbol: str
    daily: Optional[pd.DataFrame] = None        # 1D bars + indicators
    intraday_1h: Optional[pd.DataFrame] = None  # 1H bars + indicators
    intraday_15m: Optional[pd.DataFrame] = None # 15M bars + indicators
    ltp: Optional[float] = None                 # live LTP of the *tradable* instrument
    underlying_ltp: Optional[float] = None
    oi_series: Optional[List[float]] = None     # recent OI per 15M bar (options only)
    fetched_at: datetime = field(default_factory=datetime.now)

    def has_frame(self, name: str) -> bool:
        df = getattr(self, name, None)
        return df is not None and not df.empty


# ── Layer 3 output ─────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    rule: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    strategy_tag: StrategyTag
    direction: str                                  # "LONG" / "SHORT"
    results: List[RuleResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def first_failure(self) -> Optional[RuleResult]:
        return next((r for r in self.results if not r.passed), None)

    def summary(self) -> str:
        lines = [f"{'PASS' if r.passed else 'FAIL'}  {r.rule}: {r.detail}"
                 for r in self.results]
        return "\n".join(lines)


# ── Layer 4 structures ─────────────────────────────────────────────────────────

@dataclass
class OrderPlan:
    """Fully risk-vetted order, ready for the execution state machine."""
    signal: AdvisorySignal
    quantity: int = 0
    lots: int = 0
    lot_size: int = 1
    order_type: str = "LIMIT"                   # "LIMIT" | "MARKET"
    limit_price: Optional[float] = None
    risk_per_unit: float = 0.0
    capital_at_risk: float = 0.0
    rejection_reason: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.rejection_reason is None and self.quantity > 0


@dataclass
class ManagedPosition:
    """State-machine record for one live (or paper) bracket position."""
    plan: OrderPlan
    state: OrderState = OrderState.PENDING
    entry_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    target_order_id: Optional[str] = None
    fill_price: Optional[float] = None
    exit_price: Optional[float] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    def transition(self, new_state: OrderState, note: str = "") -> None:
        self.history.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "from": self.state.value,
            "to": new_state.value,
            "note": note,
        })
        self.state = new_state
