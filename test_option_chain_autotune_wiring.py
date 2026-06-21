#!/usr/bin/env python3
"""
test_option_chain_autotune_wiring.py

Run:
    python test_option_chain_autotune_wiring.py
"""

from __future__ import annotations

import option_strike_autotune
from option_chain_engine import OptionChainEngine


class FakeBroker:
    def get_ltp(self, symbol: str, exchange: str = "NFO") -> float:
        if symbol == "NIFTY":
            return 20000.0
        if "19950CE" in symbol:
            return 82.0
        if "20000CE" in symbol:
            return 50.0
        if "20050CE" in symbol:
            return 20.0
        if "20100CE" in symbol:
            return 8.0
        if "20150CE" in symbol:
            return 3.5
        return 0.0


def test_autotune_can_move_option_chain_strike() -> bool:
    original_loader = option_strike_autotune.load_autotune
    option_strike_autotune.load_autotune = lambda path=option_strike_autotune.AUTOTUNE_FILE: {
        "labelled_selected": 40,
        "feature_weights": {
            "premium:15-35": 1.35,
            "premium:>=35": 0.65,
        },
    }
    try:
        contract = OptionChainEngine(broker=FakeBroker()).select_option(
            underlying="NIFTY",
            signal_side="BUY",
            style="scalping",
            confidence=0.80,
            trade_capital=100000,
            max_lots=1,
        )
    finally:
        option_strike_autotune.load_autotune = original_loader

    return (
        contract is not None
        and contract.strike == 20050
        and contract.strike_type == "1OTM"
        and contract.autotune.get("reason") == "autotune_applied"
        and float(contract.autotune.get("multiplier", 1.0)) > 1.0
        and len(contract.shadow_candidates) >= 5
        and any(c.get("strike") == 19950 and c.get("strike_type") == "1ITM" for c in contract.shadow_candidates)
        and any(c.get("strike") == 20000 for c in contract.shadow_candidates)
        and any(c.get("strike") == 20100 for c in contract.shadow_candidates)
        and any(c.get("strike") == 20150 and c.get("strike_type") == "3OTM" for c in contract.shadow_candidates)
    )


def main() -> int:
    tests = [
        ("autotune can move option-chain strike", test_autotune_can_move_option_chain_strike),
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
