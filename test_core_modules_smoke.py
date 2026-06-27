"""Smoke / contract tests for the three core, previously-untested modules:
signal_engine, live_signal_engine, trade_manager. These guard the bug CLASSES
that have bitten this system before (silently-inert strategies from signature
mismatches; cost/edge gate regressions) without needing live market data.
"""

import inspect

import pandas as pd

import signal_engine
import live_signal_engine
import trade_manager
from trade_manager import ManagedTrade


# ── signal_engine: the strategy registry ──────────────────────────────────────

def test_strategy_registry_is_populated_and_callable():
    assert len(signal_engine.STRATEGIES) >= 20, "strategy registry collapsed"
    assert all(callable(fn) for fn in signal_engine.STRATEGIES)
    # no duplicate function objects sneaking in
    names = [getattr(fn, "__name__", repr(fn)) for fn in signal_engine.STRATEGIES]
    assert len(names) == len(set(names)), f"duplicate strategies: {names}"


def test_every_strategy_signature_is_satisfiable_by_the_adapter():
    """The single most damaging past bug: ~16/75 strategies had positional
    params the engine never supplied → TypeError every call → silently never
    voted. _invoke_strategy maps a known set of param names to values; every
    REQUIRED positional param of every strategy must be in that set (or have a
    default), else it is inert. KNOWN mirrors _invoke_strategy's _vals — keep in
    sync if that mapping changes.
    """
    KNOWN = {
        "df", "data", "ohlc", "candles",
        "df_htf", "htf", "df_high", "higher_tf",
        "symbol", "sym",
        "option_data", "option", "opt_data", "options",
        "df_1min", "df_1m", "config", "cfg", "capital",
    }
    offenders = []
    for fn in signal_engine.STRATEGIES:
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            continue  # builtins / un-inspectable → adapter falls back to legacy call
        if any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()):
            continue  # *args present → legacy positional call is safe
        for name, p in sig.parameters.items():
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                if p.default is p.empty and name not in KNOWN:
                    offenders.append(f"{fn.__name__}({name})")
    assert not offenders, f"strategies the adapter can't satisfy (would be inert): {offenders}"


def test_invoke_strategy_does_not_raise_argument_errors():
    """Exercise the real adapter against the real registry on a synthetic frame.
    Internal logic errors on minimal data are fine; an argument-binding TypeError
    is the signature-bug class and must not occur."""
    n = 60
    df = pd.DataFrame({
        "Open":  [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
        "Close": [100.5] * n, "Volume": [10000] * n,
    })
    df_htf = df.copy()
    arg_bugs = []
    for fn in signal_engine.STRATEGIES:
        try:
            signal_engine._invoke_strategy(fn, df, df_htf, {}, "NIFTY")
        except TypeError as e:
            msg = str(e).lower()
            if "argument" in msg or "positional" in msg or "parameter" in msg:
                arg_bugs.append(f"{getattr(fn, '__name__', fn)}: {e}")
        except Exception:
            pass  # any non-arg error on synthetic data is acceptable here
    assert not arg_bugs, f"argument-binding failures (silently-inert risk): {arg_bugs}"


# ── live_signal_engine: cost / edge gate helpers ──────────────────────────────

def _bare_live_engine():
    """An instance without running the heavy __init__ (no broker/data)."""
    return live_signal_engine.LiveSignalEngine.__new__(live_signal_engine.LiveSignalEngine)

def test_expected_gross_profit_contract():
    eng = _bare_live_engine()
    plan = {"entry_price": 100.0, "target_price": 110.0}
    assert eng._expected_gross_profit(plan, qty=50) == 500.0   # |110-100| * 50
    assert eng._expected_gross_profit({"entry_price": 0}, qty=50) == 0.0  # guard

def test_estimated_round_trip_cost_is_positive_for_a_real_plan():
    eng = _bare_live_engine()
    plan = {"entry_price": 200.0, "asset_type": "OPTION"}
    cost = eng._estimated_round_trip_cost(plan, qty=50, symbol="NIFTY")
    assert cost > 0.0


# ── trade_manager: the managed-trade record ───────────────────────────────────

def test_managed_trade_constructs_with_core_fields():
    t = ManagedTrade(
        trade_id="T1", symbol="NIFTY", side="BUY", qty=50, strategy="TREND",
        broker_name="PAPER", order_id="PAPER-1", entry_price=100.0, entry_time=1.0,
    )
    assert t.trade_id == "T1" and t.qty == 50 and t.entry_price == 100.0
