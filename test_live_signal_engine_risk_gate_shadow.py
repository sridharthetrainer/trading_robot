"""
_execute_candidate is a 735-line method with no existing test fixture
anywhere in this codebase (confirmed: no test file constructs a
LiveSignalEngine instance or exercises _execute_candidate directly) --
building a full end-to-end harness for it would require stubbing out its
entire dependency surface (capital_allocator, broker_manager, trade_manager,
~15 sequential gates, kelly_sizer, ws_engine, ...), disproportionate to
verifying a single, already-isolated addition.

Instead, the new shadow-comparison logic was extracted into its own method,
LiveSignalEngine._risk_gate_shadow_check(), which only touches
self.daily_loss_manager / self.risk_manager / self.trade_manager.capital.
These tests call that unbound method against a minimal stand-in object
carrying just those three attributes -- verifying exactly what matters:
it never raises, and it never needs to touch trade_id or return a value
(the real safety property _execute_candidate depends on).
"""
import types

import risk_gate
from live_signal_engine import LiveSignalEngine
from portfolio_risk import RiskDecision


class _FakeDailyLoss:
    def can_trade(self):
        return True


class _FakePortfolioRisk:
    def evaluate_new_trade(self, **kwargs):
        return RiskDecision(
            allowed=True, approved_quantity=100, approved_lots=2,
            reason="Approved", estimated_trade_risk=500.0,
            resulting_total_exposure=1000.0, resulting_symbol_exposure=500.0,
            resulting_portfolio_risk_pct=0.01,
        )


def _make_engine_stub():
    return types.SimpleNamespace(
        daily_loss_manager=_FakeDailyLoss(),
        risk_manager=_FakePortfolioRisk(),
        trade_manager=types.SimpleNamespace(capital=100_000.0),
    )


_CANDIDATE = {"symbol": "NIFTY", "signal": {"price": 100.0}}
_EXEC_PLAN = {
    "execution_symbol": "NIFTY24800CE", "entry_price": 100.0, "stop_loss": 80.0,
    "requested_quantity": 100, "correlation_group": "NIFTY", "lot_size": 50,
    "asset_type": "OPTION",
}
_LIVE_DECISION = RiskDecision(
    allowed=True, approved_quantity=100, approved_lots=2, reason="Approved",
    estimated_trade_risk=500.0, resulting_total_exposure=1000.0,
    resulting_symbol_exposure=500.0, resulting_portfolio_risk_pct=0.01,
)


def test_shadow_check_returns_none_and_never_raises_on_agreement():
    engine = _make_engine_stub()
    result = LiveSignalEngine._risk_gate_shadow_check(
        engine, _CANDIDATE, _EXEC_PLAN, [], _LIVE_DECISION,
        100, "trade-123", "NIFTY",
    )
    assert result is None  # no return value the caller could depend on


def test_shadow_check_never_raises_even_if_risk_gate_itself_blows_up(monkeypatch):
    def _explode(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(risk_gate, "evaluate", _explode)

    engine = _make_engine_stub()
    # must not raise -- this is the exact property _execute_candidate relies on
    LiveSignalEngine._risk_gate_shadow_check(
        engine, _CANDIDATE, _EXEC_PLAN, [], _LIVE_DECISION,
        100, "trade-123", "NIFTY",
    )


def test_shadow_check_never_raises_if_logging_itself_blows_up(monkeypatch):
    def _explode(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(risk_gate, "log_shadow_disagreement", _explode)

    engine = _make_engine_stub()
    LiveSignalEngine._risk_gate_shadow_check(
        engine, _CANDIDATE, _EXEC_PLAN, [], _LIVE_DECISION,
        100, "trade-123", "NIFTY",
    )


def test_shadow_check_never_raises_when_daily_loss_manager_is_missing_attrs():
    # a stand-in with a broken/incompatible daily_loss_manager must still
    # not propagate -- exercised via the try/except inside the method itself
    engine = types.SimpleNamespace(
        daily_loss_manager=object(),  # has no .can_trade() at all
        risk_manager=_FakePortfolioRisk(),
        trade_manager=types.SimpleNamespace(capital=100_000.0),
    )
    LiveSignalEngine._risk_gate_shadow_check(
        engine, _CANDIDATE, _EXEC_PLAN, [], _LIVE_DECISION,
        100, "trade-123", "NIFTY",
    )
