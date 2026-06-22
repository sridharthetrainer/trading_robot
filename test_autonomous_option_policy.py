#!/usr/bin/env python3
"""
test_autonomous_option_policy.py

Run:
    python test_autonomous_option_policy.py
"""

from __future__ import annotations

import config as cfg
from capital_allocator import CapitalAllocator
from option_chain_engine import MIN_DTE_BY_STYLE


def test_policy_is_option_first_with_stock_last_resort() -> bool:
    return (
        bool(cfg.AUTONOMOUS_OPTION_FIRST)
        and bool(cfg.ENABLE_CASH_STOCK_LAST_RESORT)
        and float(cfg.CASH_LAST_RESORT_MIN_SCORE) >= 7.0
    )


def test_all_required_styles_enabled() -> bool:
    required = {"scalping", "intraday", "swing", "position", "hero_zero"}
    return required.issubset(set(cfg.AUTONOMOUS_ALLOWED_STYLES))


def test_position_style_uses_swing_bucket_and_longer_dte() -> bool:
    alloc = CapitalAllocator(total_capital=100000)
    return (
        alloc._normalise_style("position") == "swing"
        and alloc._normalise_style("positional") == "swing"
        and MIN_DTE_BY_STYLE.get("position", 0) >= MIN_DTE_BY_STYLE.get("swing", 0)
    )


def test_qty_multipliers_are_conservative() -> bool:
    mults = cfg.OPTION_STYLE_QTY_MULTIPLIERS
    return (
        0 < float(mults.get("hero_zero", 0)) <= float(mults.get("scalping", 1))
        and 0 < float(mults.get("position", 0)) <= 1.0
        and 0 < float(cfg.CASH_LAST_RESORT_QTY_MULTIPLIER) <= 1.0
    )


def test_parallel_style_policy_enabled() -> bool:
    required = {"scalping", "intraday", "swing", "position", "hero_zero"}
    return (
        bool(cfg.ENABLE_PARALLEL_OPTION_STYLES)
        and int(cfg.MAX_SIGNALS_PER_CYCLE) >= 3
        and int(cfg.MAX_NEW_TRADES_PER_STYLE_PER_CYCLE) >= 1
        and int(cfg.MAX_NEW_TRADES_PER_UNDERLYING_PER_CYCLE) >= 1
        and required.issubset(set(cfg.OPTION_PARALLEL_STYLE_ORDER))
    )


def main() -> int:
    tests = [
        ("option-first stock-last policy", test_policy_is_option_first_with_stock_last_resort),
        ("required styles enabled", test_all_required_styles_enabled),
        ("position style bucket and dte", test_position_style_uses_swing_bucket_and_longer_dte),
        ("qty multipliers conservative", test_qty_multipliers_are_conservative),
        ("parallel style policy enabled", test_parallel_style_policy_enabled),
    ]
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
