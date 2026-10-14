"""
Direct analog of test_multileg_strategy_quarantine.py for the 42-strategy
option catalog: nothing in option_strategy_registry.py / option_core_strategies.py
may ever place an order, real or paper. This is enforced two ways --
runtime (monkeypatched Fakes that raise if called) and static (no
module-scope import of the execution-capable modules) -- because real-money
manual option trading runs alongside this bot.
"""
import ast
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

import option_strategy_registry as reg


_FORBIDDEN_MODULES = {"angel", "broker_interface", "option_execution_router"}


def _module_level_imports(path: str) -> set:
    tree = ast.parse(Path(path).read_text())
    names = set()
    for node in tree.body:  # top-level only -- a lazy import inside a
        if isinstance(node, ast.Import):  # function body is fine and expected
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_registry_has_no_module_scope_execution_imports():
    found = _module_level_imports("option_strategy_registry.py") & _FORBIDDEN_MODULES
    assert not found, f"option_strategy_registry.py imports execution modules at module scope: {found}"


def test_core_strategies_has_no_module_scope_execution_imports():
    found = _module_level_imports("option_core_strategies.py") & _FORBIDDEN_MODULES
    assert not found, f"option_core_strategies.py imports execution modules at module scope: {found}"


class _RaisingCallable:
    def __init__(self, label: str):
        self.label = label
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        raise AssertionError(f"{self.label} must never be called from the shadow-only option catalog")


def _mk_row(strike, ce_ltp, pe_ltp, iv=14.0):
    return {
        "strikePrice": strike,
        "CE": {"lastPrice": ce_ltp, "bidprice": ce_ltp * 0.99, "askPrice": ce_ltp * 1.01,
               "openInterest": 50000, "totalTradedVolume": 10000, "impliedVolatility": iv},
        "PE": {"lastPrice": pe_ltp, "bidprice": pe_ltp * 0.99, "askPrice": pe_ltp * 1.01,
               "openInterest": 50000, "totalTradedVolume": 10000, "impliedVolatility": iv},
    }


def _synthetic_chain(spot=22000.0, gap=50, steps=10):
    rows = []
    for i in range(-steps, steps + 1):
        strike = spot + i * gap
        ce = max(5.0, 150 - max(0, i) * 15 - abs(min(0, i)) * 2)
        pe = max(5.0, 150 - max(0, -i) * 15 - abs(max(0, i)) * 2)
        rows.append(_mk_row(strike, ce, pe))
    return {"chain": rows, "spot": spot, "ts": time.time()}


def test_evaluate_catalog_never_places_an_order(monkeypatch):
    import angel
    import option_execution_router

    place_order = _RaisingCallable("AngelOne.place_order")
    execute_entry = _RaisingCallable("execute_option_entry")
    monkeypatch.setattr(angel.AngelOne, "place_order", place_order)
    monkeypatch.setattr(option_execution_router, "execute_option_entry",
                         execute_entry)
    monkeypatch.setattr("option_decision_journal.record_option_decision", lambda **kw: dict(kw))
    monkeypatch.setattr("option_core_strategies._todays_high_impact_events", lambda: [])
    monkeypatch.setattr(reg, "_SELECTED_TODAY", set())

    conn = sqlite3.connect(":memory:")
    option_data = _synthetic_chain()
    regime = {"primary": "MEAN_REVERT", "vix": 11.0, "gap": False,
              "days_to_expiry": 5, "next_expiry": "2026-07-22"}

    results = reg.evaluate_catalog(
        symbol="NIFTY", df=None, df_htf=None, option_data=option_data, regime=regime, conn=conn,
        now=datetime(2026, 7, 20, 9, 21))
    results += reg.evaluate_catalog(
        symbol="NIFTY", df=None, df_htf=None, option_data=option_data, regime=regime, conn=conn,
        now=datetime(2026, 7, 20, 10),
        gap_risk_manager=SimpleNamespace(get_iv_percentile=lambda symbol: 60.0))

    ids = {r.get("id") for r in results}
    assert len(ids) == 42
    assert {r["id"] for r in results if r["status"] == "selected"} == {"C1", "C3"}
    assert place_order.calls == execute_entry.calls == 0
    # Both selection branches ran. Counters detect execution attempts even
    # if the catalog catches the fake's AssertionError as a strategy error.
