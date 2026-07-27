#!/usr/bin/env python3
"""
test_live_probation.py

Run:
    python test_live_probation.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import config
from live_probation import (
    evaluate_probation,
    load_state,
    probation_preflight,
    record_probation_entry,
    record_probation_exit,
)


PROMISING_OPTION_META = {
    "asset_type": "OPTION",
    "lot_size": 65,
    "edge_policy": "PAPER_PROMISING",
    "health": {"data": True, "feed": True, "angel": True},
}


def _set(name: str, value):
    old = getattr(config, name, None)
    setattr(config, name, value)
    return old


def _restore(values):
    for name, value in values.items():
        setattr(config, name, value)


def test_disabled_by_default_blocks():
    old = _set("LIVE_PROBATION_ENABLED", False)
    try:
        d = evaluate_probation(
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            requested_qty=65,
            entry_price=20,
            metadata=PROMISING_OPTION_META,
            state={"date": "x", "trades": [], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert not d.allowed and d.reason == "probation_disabled"
    finally:
        setattr(config, "LIVE_PROBATION_ENABLED", old)


def test_enabled_caps_option_to_one_lot():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "LIVE_PROBATION_MAX_CAPITAL": _set("LIVE_PROBATION_MAX_CAPITAL", 2000.0),
        "LIVE_PROBATION_MAX_LOTS": _set("LIVE_PROBATION_MAX_LOTS", 1),
        "LIVE_PROBATION_MAX_TRADES_PER_DAY": _set("LIVE_PROBATION_MAX_TRADES_PER_DAY", 1),
        "PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"]),
    }
    try:
        d = evaluate_probation(
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            requested_qty=650,
            entry_price=20,
            metadata=PROMISING_OPTION_META,
            state={"date": "x", "trades": [], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert d.allowed and d.live_qty == 65
    finally:
        _restore(old)


def test_daily_limit_blocks_second_trade():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "LIVE_PROBATION_MAX_TRADES_PER_DAY": _set("LIVE_PROBATION_MAX_TRADES_PER_DAY", 1),
        "PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"]),
    }
    try:
        d = evaluate_probation(
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            requested_qty=65,
            entry_price=20,
            metadata=PROMISING_OPTION_META,
            state={"date": "x", "trades": [{"trade_id": "T1"}], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert not d.allowed and d.reason == "probation_daily_trade_limit"
    finally:
        _restore(old)


def test_loss_locks_probation_state():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "probation.json")
        record_probation_entry(
            trade_id="T1",
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            live_qty=65,
            entry_price=20,
            path=path,
        )
        state = record_probation_exit("T1", pnl=-100.0, path=path)
        loaded = load_state(path)
        assert state["loss_locked"] is True and loaded["daily_pnl"] == -100.0


def test_hard_block_reason_blocks_probation():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"]),
    }
    try:
        d = evaluate_probation(
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            requested_qty=65,
            entry_price=20,
            metadata={
                **PROMISING_OPTION_META,
                "signal_data": {"live_block_reason": "fallback_ai_filter_block"},
            },
            state={"date": "x", "trades": [], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert not d.allowed and d.reason == "probation_hard_block_reason"
    finally:
        _restore(old)


def test_probation_blocks_symbol_outside_universe():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"]),
    }
    try:
        d = evaluate_probation(
            symbol="RELIANCE",
            strategy="PIVOT_SCALPING",
            requested_qty=1,
            entry_price=100,
            metadata={"edge_policy": "PAPER_PROMISING", "health": {"data": True, "feed": True, "angel": True}},
            state={"date": "x", "trades": [], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert not d.allowed and d.reason == "probation_symbol_not_allowed"
    finally:
        _restore(old)


def test_probation_requires_promising_or_live_evidence_status():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"]),
    }
    try:
        d = evaluate_probation(
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            requested_qty=65,
            entry_price=20,
            metadata={
                "asset_type": "OPTION",
                "lot_size": 65,
                "edge_policy": "VALIDATING",
                "health": {"data": True, "feed": True, "angel": True},
            },
            state={"date": "x", "trades": [], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert not d.allowed and d.reason == "probation_evidence_status_VALIDATING"
    finally:
        _restore(old)


def test_probation_requires_data_feed_and_angel_health():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "PROBATION_UNIVERSE": _set("PROBATION_UNIVERSE", ["NIFTY"]),
    }
    try:
        d = evaluate_probation(
            symbol="NIFTY20000CE",
            strategy="PIVOT_SCALPING",
            requested_qty=65,
            entry_price=20,
            metadata={
                "asset_type": "OPTION",
                "lot_size": 65,
                "edge_policy": "PAPER_PROMISING",
                "health": {"data": True, "feed": False, "angel": True},
            },
            state={"date": "x", "trades": [], "daily_pnl": 0.0, "loss_locked": False},
        )
        assert not d.allowed and d.reason == "probation_system_health_not_ok"
    finally:
        _restore(old)


def test_preflight_warns_when_capital_cannot_fit_lot():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "LIVE_PROBATION_MAX_CAPITAL": _set("LIVE_PROBATION_MAX_CAPITAL", 500.0),
        "OPTION_LOT_SIZE": _set("OPTION_LOT_SIZE", 65),
    }
    try:
        status = probation_preflight(sample_option_premium=20.0, sample_equity_price=100.0)
        assert (
            status["ok"] is False
            and "max_capital_cannot_fit_sample_option_lot" in status["warnings"]
        )
    finally:
        _restore(old)


def test_preflight_ok_for_valid_probation_config():
    old = {
        "LIVE_PROBATION_ENABLED": _set("LIVE_PROBATION_ENABLED", True),
        "ENABLE_REAL_TRADING": _set("ENABLE_REAL_TRADING", True),
        "PAPER_TRADING": _set("PAPER_TRADING", False),
        "LIVE_PROBATION_MAX_CAPITAL": _set("LIVE_PROBATION_MAX_CAPITAL", 2000.0),
        "LIVE_PROBATION_MAX_LOTS": _set("LIVE_PROBATION_MAX_LOTS", 1),
        "OPTION_LOT_SIZE": _set("OPTION_LOT_SIZE", 65),
    }
    try:
        status = probation_preflight(sample_option_premium=20.0, sample_equity_price=100.0)
        assert status["ok"] is True and status["warnings"] == []
    finally:
        _restore(old)


def main() -> int:
    tests = [
        ("disabled by default blocks", test_disabled_by_default_blocks),
        ("enabled caps option to one lot", test_enabled_caps_option_to_one_lot),
        ("daily limit blocks second trade", test_daily_limit_blocks_second_trade),
        ("loss locks probation state", test_loss_locks_probation_state),
        ("hard block reason blocks probation", test_hard_block_reason_blocks_probation),
        ("probation blocks symbol outside universe", test_probation_blocks_symbol_outside_universe),
        ("probation requires evidence status", test_probation_requires_promising_or_live_evidence_status),
        ("probation requires health", test_probation_requires_data_feed_and_angel_health),
        ("preflight warns when capital cannot fit lot", test_preflight_warns_when_capital_cannot_fit_lot),
        ("preflight ok for valid probation config", test_preflight_ok_for_valid_probation_config),
    ]
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok is None:
            ok = True
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
