#!/usr/bin/env python3
"""
test_hero_zero_strategy.py

Focused tests for hero_zero_strategy option-candidate quality.

Run:
    python test_hero_zero_strategy.py
"""

from __future__ import annotations

import sys

from hero_zero_strategy import build_hero_zero_risk_plan, get_hero_zero_strikes, select_hero_zero_candidate


def _chain_row(strike, ce_ltp=0, pe_ltp=0, ce_oi=0, pe_oi=0, ce_vol=0, pe_vol=0,
               ce_bid=0, ce_ask=0, pe_bid=0, pe_ask=0):
    return {
        "strikePrice": strike,
        "CE": {
            "lastPrice": ce_ltp,
            "openInterest": ce_oi,
            "totalTradedVolume": ce_vol,
            "bidprice": ce_bid,
            "askPrice": ce_ask,
        },
        "PE": {
            "lastPrice": pe_ltp,
            "openInterest": pe_oi,
            "totalTradedVolume": pe_vol,
            "bidprice": pe_bid,
            "askPrice": pe_ask,
        },
    }


def test_illiquid_cheap_contract_rejected():
    option_data = {
        "chain": [
            _chain_row(20300, ce_ltp=5, ce_oi=10, ce_vol=5, ce_bid=4.5, ce_ask=5.5),
            _chain_row(20600, ce_ltp=18, ce_oi=500, ce_vol=600, ce_bid=17, ce_ask=19),
            _chain_row(20900, ce_ltp=40, ce_oi=500, ce_vol=600, ce_bid=38, ce_ask=42),
        ]
    }
    strikes = get_hero_zero_strikes(
        spot=20000,
        symbol="NIFTY",
        direction="BUY",
        otm_pct=1.5,
        n_strikes=3,
        option_data=option_data,
    )
    first = strikes[0]
    assert (
        first["strike"] == 20300
        and first["tradeable"] is False
        and first["quality_reason"] == "oi_too_low"
    )


def test_best_candidate_prefers_quality_not_cheapest():
    option_data = {
        "chain": [
            _chain_row(20300, ce_ltp=5, ce_oi=500, ce_vol=600, ce_bid=4.5, ce_ask=5.5),
            _chain_row(20600, ce_ltp=20, ce_oi=700, ce_vol=800, ce_bid=19, ce_ask=21),
            _chain_row(20900, ce_ltp=55, ce_oi=700, ce_vol=800, ce_bid=52, ce_ask=58),
        ]
    }
    strikes = get_hero_zero_strikes(
        spot=20000,
        symbol="NIFTY",
        direction="BUY",
        otm_pct=1.5,
        n_strikes=3,
        option_data=option_data,
    )
    selected = select_hero_zero_candidate(strikes)
    assert selected is not None and selected["strike"] == 20600


def test_wide_spread_rejected():
    option_data = {
        "chain": [
            _chain_row(20300, ce_ltp=20, ce_oi=800, ce_vol=900, ce_bid=10, ce_ask=25),
        ]
    }
    strikes = get_hero_zero_strikes(
        spot=20000,
        symbol="NIFTY",
        direction="BUY",
        otm_pct=1.5,
        n_strikes=1,
        option_data=option_data,
    )
    assert (
        strikes[0]["tradeable"] is False
        and strikes[0]["quality_reason"] == "spread_too_wide"
    )


def test_risk_plan_uses_premium_defined_levels():
    plan = build_hero_zero_risk_plan({"premium": 20, "lot_size": 65})
    assert (
        plan["risk_known"] is True
        and plan["max_loss"] == 1300
        and plan["stop_price"] == 10
        and plan["target_1"] == 40
        and plan["target_2"] == 100
    )


def main() -> int:
    tests = [
        ("illiquid cheap contract rejected", test_illiquid_cheap_contract_rejected),
        ("best candidate prefers quality", test_best_candidate_prefers_quality_not_cheapest),
        ("wide spread rejected", test_wide_spread_rejected),
        ("risk plan uses premium levels", test_risk_plan_uses_premium_defined_levels),
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
    sys.exit(main())
