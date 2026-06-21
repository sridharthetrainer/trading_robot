#!/usr/bin/env python3
"""
test_universe_manager.py

Run:
    python test_universe_manager.py
"""

from __future__ import annotations

import config
from universe_manager import (
    build_learning_universe,
    is_live_symbol_allowed,
    is_probation_symbol_allowed,
)


def _set(name: str, value):
    old = getattr(config, name, None)
    setattr(config, name, value)
    return old


def _restore(values):
    for name, value in values.items():
        setattr(config, name, value)


def test_learning_universe_keeps_indices_first() -> bool:
    old = {
        "LEARNING_UNIVERSE_MODE": _set("LEARNING_UNIVERSE_MODE", "liquid_fno"),
        "LEARNING_UNIVERSE_MAX_SYMBOLS": _set("LEARNING_UNIVERSE_MAX_SYMBOLS", 20),
        "LEARNING_UNIVERSE_EXTRA_SYMBOLS": _set("LEARNING_UNIVERSE_EXTRA_SYMBOLS", []),
    }
    try:
        universe = build_learning_universe(["RELIANCE", "HDFCBANK", "ICICIBANK"])
        return universe[:3] == ["NIFTY", "BANKNIFTY", "FINNIFTY"] and "RELIANCE" in universe
    finally:
        _restore(old)


def test_probation_universe_allows_underlying_from_option_symbol() -> bool:
    old = {"PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"])}
    try:
        return (
            is_probation_symbol_allowed("NIFTY20000CE")
            and not is_probation_symbol_allowed("RELIANCE")
        )
    finally:
        _restore(old)


def test_live_universe_empty_means_no_extra_filter() -> bool:
    old = {"LIVE_UNIVERSE": _set("LIVE_UNIVERSE", [])}
    try:
        return is_live_symbol_allowed("ANYTHING")
    finally:
        _restore(old)


def main() -> int:
    tests = [
        ("learning universe keeps indices first", test_learning_universe_keeps_indices_first),
        ("probation universe allows option underlying", test_probation_universe_allows_underlying_from_option_symbol),
        ("live universe empty means no extra filter", test_live_universe_empty_means_no_extra_filter),
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
