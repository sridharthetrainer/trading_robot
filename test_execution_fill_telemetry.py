import pytest

from execution_fill_telemetry import _enrich


def test_journal_entry_is_not_its_own_slippage_benchmark():
    row = _enrich({"side": "BUY", "entry_price": 100})
    assert row["entry_slippage_pct"] is None
    assert row["fill_avg_price"] == 0


def test_decision_price_without_actual_fill_is_not_execution_evidence():
    row = _enrich({"side": "BUY", "entry_price": 101,
                   "signal_metadata": {"expected_entry": 100}})
    assert row["entry_slippage_pct"] is None


@pytest.mark.parametrize("side,expected", [("BUY", 1), ("SELL", -1)])
def test_slippage_uses_distinct_decision_and_fill_prices(side, expected):
    row = _enrich({"side": side, "entry_price": 101,
                   "signal_metadata": {"expected_entry": 100},
                   "metadata": {"fill": {"fill_avg_price": 101, "fill_latency_sec": .125}}})
    assert row["entry_slippage_pct"] == expected
    assert row["fill_latency_sec"] == .125


@pytest.mark.parametrize("latency", ["bad", float("nan"), float("inf"), -1, None])
def test_invalid_latency_is_unknown_instead_of_zero(latency):
    row = _enrich({"metadata": {"fill": {"fill_latency_sec": latency}}})
    assert row["fill_latency_sec"] is None
