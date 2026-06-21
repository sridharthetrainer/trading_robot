#!/usr/bin/env python3
"""
test_option_quality.py

Run:
    python test_option_quality.py
"""

from __future__ import annotations

import sys
import time

from option_quality import evaluate_option_buy_quality


class _Series:
    def __init__(self, values):
        self._values = values

    def max(self):
        return max(self._values)

    def min(self):
        return min(self._values)


class _Frame:
    def __init__(self, **cols):
        self._cols = cols
        self.columns = set(cols)

    def __getitem__(self, key):
        return _Series(self._cols[key])


def _row(strike, ce=100, pe=100, oi=1000, vol=1000, bid=99, ask=101):
    return {
        "strikePrice": strike,
        "CE": {
            "lastPrice": ce,
            "openInterest": oi,
            "totalTradedVolume": vol,
            "bidprice": bid,
            "askPrice": ask,
        },
        "PE": {
            "lastPrice": pe,
            "openInterest": oi,
            "totalTradedVolume": vol,
            "bidprice": bid,
            "askPrice": ask,
        },
    }


def test_good_chain_passes() -> bool:
    data = {
        "timestamp": time.time(),
        "records": {"underlyingValue": 20000, "data": [_row(20000)]},
    }
    df = _Frame(high=[20020, 20040], low=[19990, 20000])
    q = evaluate_option_buy_quality(data, df=df)
    return q.ok and q.reason == "ok_to_buy_options" and q.straddle_price == 200


def test_stale_chain_blocks() -> bool:
    data = {
        "timestamp": time.time() - 600,
        "records": {"underlyingValue": 20000, "data": [_row(20000)]},
    }
    q = evaluate_option_buy_quality(data, max_chain_age_sec=120)
    return not q.ok and q.reason == "option_chain_stale"


def test_expected_move_consumed_blocks() -> bool:
    data = {
        "timestamp": time.time(),
        "records": {"underlyingValue": 20000, "data": [_row(20000, ce=80, pe=80)]},
    }
    df = _Frame(high=[20150, 20200], low=[19950, 19800])
    q = evaluate_option_buy_quality(data, df=df, expected_move_usage_limit=0.70)
    return not q.ok and q.reason == "expected_move_already_consumed"


def test_wide_atm_spread_blocks() -> bool:
    data = {
        "timestamp": time.time(),
        "records": {"underlyingValue": 20000, "data": [_row(20000, bid=50, ask=100)]},
    }
    q = evaluate_option_buy_quality(data, max_atm_spread_pct=0.20)
    return not q.ok and q.reason == "atm_spread_too_wide"


def main() -> int:
    tests = [
        ("good chain passes", test_good_chain_passes),
        ("stale chain blocks", test_stale_chain_blocks),
        ("expected move consumed blocks", test_expected_move_consumed_blocks),
        ("wide atm spread blocks", test_wide_atm_spread_blocks),
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
    sys.exit(main())
