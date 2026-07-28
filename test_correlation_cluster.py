from live_signal_engine import LiveSignalEngine


def _engine(sector_map=None):
    engine = LiveSignalEngine.__new__(LiveSignalEngine)
    engine._sector_map = sector_map or {}
    return engine


def test_index_underlyings_share_index_beta_cluster():
    engine = _engine()
    assert engine._correlation_cluster("NIFTY") == "INDEX_BETA"
    assert engine._correlation_cluster("BANKNIFTY") == "INDEX_BETA"
    assert engine._correlation_cluster("FINNIFTY") == "INDEX_BETA"


def test_same_sector_stocks_share_a_cluster():
    engine = _engine({"HDFCBANK": "BANKING", "ICICIBANK": "BANKING", "TCS": "IT"})
    assert engine._correlation_cluster("HDFCBANK") == engine._correlation_cluster("ICICIBANK")
    assert engine._correlation_cluster("HDFCBANK") != engine._correlation_cluster("TCS")


def test_unmapped_stock_falls_back_to_its_own_symbol():
    engine = _engine({})
    assert engine._correlation_cluster("SOMEOBSCURESTOCK") == "SOMEOBSCURESTOCK"


def test_cash_execution_plan_uses_sector_cluster_not_raw_symbol(monkeypatch):
    """Regression for a 2026-07-28 audit finding: the cash-equity fallback in
    _build_execution_plan used the raw symbol as correlation_group, unlike the
    option path (_correlation_cluster), so the max-1-per-group cap never
    caught two same-sector cash stocks stacking correlated bets."""
    import config as cfg
    import live_signal_engine as lse

    engine = LiveSignalEngine.__new__(LiveSignalEngine)
    engine._sector_map = {"HDFCBANK": "BANKING"}
    engine._sl_guard = None
    engine.total_capital = 100000.0

    class _FakeSizer:
        def size_position(self, **kwargs):
            return type("S", (), {"quantity": 10})()

    engine.position_sizer = _FakeSizer()
    monkeypatch.setattr(cfg, "ENABLE_CASH_EQUITY_EXECUTION", True, raising=False)

    plan = lse.LiveSignalEngine._build_execution_plan(
        engine,
        symbol="HDFCBANK",
        signal={"side": "BUY", "price": 100.0, "confidence": 0.6, "score": 7.0,
                "regime": "TREND", "strategy": "AUTO"},
        option_signal=None,
        trade_capital=100000.0,
        style="INTRADAY",
        df=None,
        option_result=None,
    )
    assert plan is not None
    assert plan["correlation_group"] == "SECTOR:BANKING"
