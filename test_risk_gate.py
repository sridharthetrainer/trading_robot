import json

import risk_gate
from portfolio_risk import RiskDecision


class _FakeDailyLoss:
    def __init__(self, can_trade=True):
        self._can_trade = can_trade

    def can_trade(self):
        return self._can_trade


class _FakePortfolioRisk:
    """Records the exact kwargs it was called with, for asserting the
    daily-loss-skip contract, and returns a pre-set RiskDecision."""

    def __init__(self, decision):
        self._decision = decision
        self.calls = []

    def evaluate_new_trade(self, **kwargs):
        self.calls.append(kwargs)
        return self._decision


def _allowed_decision(qty=100):
    return RiskDecision(
        allowed=True, approved_quantity=qty, approved_lots=qty // 50,
        reason="Approved", estimated_trade_risk=500.0,
        resulting_total_exposure=1000.0, resulting_symbol_exposure=500.0,
        resulting_portfolio_risk_pct=0.01,
    )


def _blocked_decision(reason="Trade blocked by portfolio risk or exposure limits"):
    return RiskDecision(
        allowed=False, approved_quantity=0, approved_lots=0, reason=reason,
        estimated_trade_risk=0.0, resulting_total_exposure=0.0,
        resulting_symbol_exposure=0.0, resulting_portfolio_risk_pct=0.0,
    )


_CANDIDATE = {"symbol": "NIFTY", "signal": {"price": 100.0}}
_EXEC_PLAN = {
    "execution_symbol": "NIFTY24800CE", "entry_price": 100.0, "stop_loss": 80.0,
    "requested_quantity": 100, "correlation_group": "NIFTY", "lot_size": 50,
    "asset_type": "OPTION",
}


def test_daily_loss_lock_blocks_before_portfolio_risk_is_even_called():
    portfolio_risk = _FakePortfolioRisk(_allowed_decision())
    ctx = risk_gate.RiskGateContext(
        daily_loss_manager=_FakeDailyLoss(can_trade=False),
        portfolio_risk_manager=portfolio_risk, capital=100_000.0,
    )
    decision = risk_gate.evaluate(_CANDIDATE, _EXEC_PLAN, [], ctx)
    assert decision.allowed is False
    assert decision.reason == "daily_loss_lock_active"
    assert portfolio_risk.calls == []  # never reached -- daily-loss short-circuits first


def test_portfolio_risk_hard_block_passes_through_unchanged():
    blocked = _blocked_decision()
    portfolio_risk = _FakePortfolioRisk(blocked)
    ctx = risk_gate.RiskGateContext(
        daily_loss_manager=_FakeDailyLoss(can_trade=True),
        portfolio_risk_manager=portfolio_risk, capital=100_000.0,
    )
    decision = risk_gate.evaluate(_CANDIDATE, _EXEC_PLAN, [], ctx)
    assert decision is blocked
    assert decision.allowed is False


def test_portfolio_risk_call_skips_its_own_daily_loss_recheck():
    portfolio_risk = _FakePortfolioRisk(_allowed_decision())
    ctx = risk_gate.RiskGateContext(
        daily_loss_manager=_FakeDailyLoss(can_trade=True),
        portfolio_risk_manager=portfolio_risk, capital=100_000.0,
    )
    risk_gate.evaluate(_CANDIDATE, _EXEC_PLAN, [], ctx)
    assert len(portfolio_risk.calls) == 1
    assert portfolio_risk.calls[0]["daily_loss_limit"] is None


def test_all_clear_passes_through_when_var_is_low(monkeypatch):
    class _FakeReport:
        var_pct = 1.0  # 1% -- well under the 3% resize threshold

    monkeypatch.setattr(
        "value_at_risk.get_var_engine", lambda capital: type(
            "E", (), {"compute": lambda self, positions: _FakeReport()}
        )(),
    )
    portfolio_risk = _FakePortfolioRisk(_allowed_decision(qty=100))
    ctx = risk_gate.RiskGateContext(
        daily_loss_manager=_FakeDailyLoss(can_trade=True),
        portfolio_risk_manager=portfolio_risk, capital=100_000.0,
    )
    decision = risk_gate.evaluate(_CANDIDATE, _EXEC_PLAN, [], ctx)
    assert decision.allowed is True
    assert decision.approved_quantity == 100


def test_high_var_resizes_the_approved_quantity(monkeypatch):
    class _FakeReport:
        var_pct = 9.0  # 9% -- over the 3% throttle

    monkeypatch.setattr(
        "value_at_risk.get_var_engine", lambda capital: type(
            "E", (), {"compute": lambda self, positions: _FakeReport()}
        )(),
    )
    portfolio_risk = _FakePortfolioRisk(_allowed_decision(qty=300))
    ctx = risk_gate.RiskGateContext(
        daily_loss_manager=_FakeDailyLoss(can_trade=True),
        portfolio_risk_manager=portfolio_risk, capital=100_000.0,
    )
    decision = risk_gate.evaluate(_CANDIDATE, _EXEC_PLAN, [], ctx)
    assert decision.allowed is True
    assert decision.approved_quantity < 300
    assert "VaR-resized" in decision.reason


def test_var_check_failure_does_not_block_the_trade(monkeypatch):
    def _raise(capital):
        raise RuntimeError("var engine unavailable")
    monkeypatch.setattr("value_at_risk.get_var_engine", _raise)
    portfolio_risk = _FakePortfolioRisk(_allowed_decision(qty=100))
    ctx = risk_gate.RiskGateContext(
        daily_loss_manager=_FakeDailyLoss(can_trade=True),
        portfolio_risk_manager=portfolio_risk, capital=100_000.0,
    )
    decision = risk_gate.evaluate(_CANDIDATE, _EXEC_PLAN, [], ctx)
    assert decision.allowed is True
    assert decision.approved_quantity == 100  # unresized -- VaR check failed open


# ── log_shadow_disagreement ─────────────────────────────────────────────────

def test_shadow_log_writes_only_on_allowed_disagreement(tmp_path, monkeypatch):
    log_file = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(risk_gate, "SHADOW_LOG_FILE", log_file)

    live = _allowed_decision(qty=100)
    shadow = _blocked_decision()
    risk_gate.log_shadow_disagreement(live, shadow, "NIFTY", {"note": "test"})

    assert log_file.exists()
    entry = json.loads(log_file.read_text().splitlines()[0])
    assert entry["live"]["allowed"] is True
    assert entry["shadow"]["allowed"] is False


def test_shadow_log_silent_when_decisions_agree(tmp_path, monkeypatch):
    log_file = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(risk_gate, "SHADOW_LOG_FILE", log_file)

    live = _allowed_decision(qty=100)
    shadow = _allowed_decision(qty=100)
    risk_gate.log_shadow_disagreement(live, shadow, "NIFTY", {})

    assert not log_file.exists()


def test_shadow_log_ignores_small_qty_differences_within_tolerance(tmp_path, monkeypatch):
    log_file = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(risk_gate, "SHADOW_LOG_FILE", log_file)

    live = _allowed_decision(qty=1000)
    shadow = _allowed_decision(qty=1005)  # 0.5% difference -- within 1% tolerance
    risk_gate.log_shadow_disagreement(live, shadow, "NIFTY", {})

    assert not log_file.exists()


def test_shadow_log_flags_large_qty_differences(tmp_path, monkeypatch):
    log_file = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(risk_gate, "SHADOW_LOG_FILE", log_file)

    live = _allowed_decision(qty=1000)
    shadow = _allowed_decision(qty=500)  # 50% difference
    risk_gate.log_shadow_disagreement(live, shadow, "NIFTY", {})

    assert log_file.exists()


def test_shadow_log_never_raises_even_with_a_malformed_decision(tmp_path, monkeypatch):
    log_file = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(risk_gate, "SHADOW_LOG_FILE", log_file)

    class _Broken:
        allowed = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    # must not raise -- shadow logging can never affect the caller
    risk_gate.log_shadow_disagreement(_Broken(), _allowed_decision(), "NIFTY", {})
