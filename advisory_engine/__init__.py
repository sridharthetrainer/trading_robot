"""
advisory_engine — advisory-text -> validated, risk-sized signal pipeline.

ADDITIVE module: nothing in the existing trading system is imported-and-mutated;
this package only *consumes* the AngelOne client surface for data. Execution is
PAPER by default — see advisory_engine.gateway for the safety contract.

Layers:
  1. parser.AdvisoryParser        — regex/NLP text -> AdvisorySignal JSON payload
  2. data_core.MarketDataCore     — fuzzy token resolution + MTF data + indicators
  3. validators.ValidationMatrix  — BOLLINGER_MACD / VWAP_OI / TRENDLINE_EMA rules
  4. gateway.RiskGovernor + ExecutionGateway — sizing, IV filter, OCO bracket

Quick start (paper, with the project's AngelOne client):

    from angel import AngelOne
    from advisory_engine import AdvisoryEngine

    broker = AngelOne(api_key, client_id, password, totp_secret)
    broker.connect()
    engine = AdvisoryEngine(
        broker=broker,
        equity_provider=broker.get_balance,   # live rmsLimit equity, no guess
    )
    pos = engine.process("Buy MOTHERSON between 126-127 SL 122 target 135 — "
                         "price detaching cleanly from the VWAP with volume spike")
    print(pos.state, pos.history)
"""

from .models import (Action, Segment, StrategyTag, ExecutionMode, OrderState,
                     AdvisorySignal, MarketSnapshot, ValidationReport,
                     OrderPlan, ManagedPosition)
from .parser import AdvisoryParser
from .data_core import MarketDataCore, TokenResolver, compute_indicators
from .validators import ValidationMatrix, trade_direction
from .gateway import RiskGovernor, ExecutionGateway, implied_vol, historical_vol_30d
from .engine import AdvisoryEngine

__all__ = [
    "Action", "Segment", "StrategyTag", "ExecutionMode", "OrderState",
    "AdvisorySignal", "MarketSnapshot", "ValidationReport", "OrderPlan",
    "ManagedPosition", "AdvisoryParser", "MarketDataCore", "TokenResolver",
    "compute_indicators", "ValidationMatrix", "trade_direction",
    "RiskGovernor", "ExecutionGateway", "implied_vol", "historical_vol_30d",
    "AdvisoryEngine",
]
