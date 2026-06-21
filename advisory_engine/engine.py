"""
advisory_engine.engine
======================
Top-level orchestrator: one call walks an advisory text through all 4 layers.

    engine = AdvisoryEngine(broker=angel_client)          # PAPER by default
    position = engine.process("Buy MOTHERSON between 126-127 SL 122 TGT 135 ...")

Any layer can abort; the return is always a ManagedPosition whose state and
history record exactly where and why the pipeline stopped.
"""

from __future__ import annotations

import logging
from typing import Optional, Callable, List

from .models import (AdvisorySignal, ExecutionMode, ManagedPosition, OrderPlan,
                     OrderState, StrategyTag)
from .parser import AdvisoryParser
from .data_core import MarketDataCore, TokenResolver
from .validators import ValidationMatrix
from .gateway import RiskGovernor, ExecutionGateway

logger = logging.getLogger("advisory.engine")


def _rejected(sig: AdvisorySignal, reason: str) -> ManagedPosition:
    plan = OrderPlan(signal=sig, rejection_reason=reason)
    pos = ManagedPosition(plan=plan)
    pos.transition(OrderState.REJECTED, reason)
    return pos


class AdvisoryEngine:
    """Wires Layers 1-4 together. Defaults are deliberately conservative."""

    def __init__(self,
                 broker,
                 mode: ExecutionMode = ExecutionMode.PAPER,
                 equity_provider: Optional[Callable[[], Optional[float]]] = None,
                 lot_size_provider: Optional[Callable[[str], int]] = None,
                 days_to_expiry_provider=None,
                 oi_provider=None,
                 token_resolver: Optional[TokenResolver] = None,
                 min_resolve_score: float = 0.80):
        self.parser = AdvisoryParser()
        self.data_core = MarketDataCore(broker, token_resolver, oi_provider)
        self.matrix = ValidationMatrix()
        self.governor = RiskGovernor(
            equity_provider=equity_provider or (lambda: None),
            lot_size_provider=lot_size_provider,
            days_to_expiry_provider=days_to_expiry_provider)
        self.gateway = ExecutionGateway(mode=mode, broker=broker)
        self.min_resolve_score = min_resolve_score

    def process(self, advisory_text: str) -> ManagedPosition:
        # LAYER 1 — parse
        sig = self.parser.parse(advisory_text)
        if not sig.is_complete:
            return _rejected(sig, f"L1 incomplete signal: {sig.parse_errors}")

        # LAYER 2 — token resolution + MTF snapshot
        need_1h = sig.strategy_tag is StrategyTag.BOLLINGER_MACD
        need_15m = sig.strategy_tag is StrategyTag.VWAP_OI
        snap = self.data_core.build_snapshot(sig, need_1h=need_1h, need_15m=need_15m)
        if snap is None:
            return _rejected(sig, "L2 could not resolve instrument / build snapshot")
        if sig.resolve_score < self.min_resolve_score:
            return _rejected(sig, f"L2 low-confidence symbol match "
                                  f"{sig.resolved_symbol!r} ({sig.resolve_score:.2f} "
                                  f"< {self.min_resolve_score}) — refusing to trade "
                                  "a possibly-wrong instrument")

        # LAYER 3 — technical validation matrix
        report = self.matrix.validate(sig, snap)
        if not report.passed:
            fail = report.first_failure
            return _rejected(sig, f"Trade Aborted: Rule "
                                  f"[{fail.rule if fail else 'NONE'}] failed "
                                  f"validation ({fail.detail if fail else ''})")

        # LAYER 4 — risk governance + execution
        plan = self.governor.build_plan(sig, snap)
        pos = self.gateway.submit(plan)
        logger.info("PIPELINE ◀ %s %s -> %s",
                    sig.action.value if sig.action else "?",
                    sig.resolved_symbol, pos.state.value)
        return pos

    def process_batch(self, texts: List[str]) -> List[ManagedPosition]:
        return [self.process(t) for t in texts]
