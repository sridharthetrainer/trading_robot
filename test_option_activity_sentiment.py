#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

from option_chain_intelligence import OptionChainIntelligence


def _base_rows():
    rows = []
    for strike in [19850, 19900, 19950, 20000, 20050, 20100, 20150]:
        rows.append({
            "strikePrice": strike,
            "CE_openInterest": 1000,
            "PE_openInterest": 1000,
            "CE_changeinOpenInterest": 100,
            "PE_changeinOpenInterest": 100,
            "CE_totalTradedVolume": 100,
            "PE_totalTradedVolume": 100,
            "CE_lastPrice": 50,
            "PE_lastPrice": 50,
        })
    return rows


def test_put_support_activity_is_bullish():
    rows = _base_rows()
    for row in rows:
        if row["strikePrice"] in {19900, 19950, 20000}:
            row["PE_openInterest"] = 8000
            row["PE_changeinOpenInterest"] = 1800
            row["PE_totalTradedVolume"] = 1200
    summary = OptionChainIntelligence("NIFTY", strike_window=4).analyze(
        pd.DataFrame(rows),
        spot_price=20000,
    )
    assert (
        summary.activity_sentiment["sentiment"] == "BULLISH"
        and summary.activity_sentiment["bullish_score"] > summary.activity_sentiment["bearish_score"]
        and summary.most_active_put_strikes[0]["strike"] in {19900.0, 19950.0, 20000.0}
    )


def test_call_resistance_activity_is_bearish():
    rows = _base_rows()
    for row in rows:
        if row["strikePrice"] in {20000, 20050, 20100}:
            row["CE_openInterest"] = 8000
            row["CE_changeinOpenInterest"] = 1800
            row["CE_totalTradedVolume"] = 1200
    summary = OptionChainIntelligence("NIFTY", strike_window=4).analyze(
        pd.DataFrame(rows),
        spot_price=20000,
    )
    assert (
        summary.activity_sentiment["sentiment"] == "BEARISH"
        and summary.activity_sentiment["bearish_score"] > summary.activity_sentiment["bullish_score"]
        and summary.most_active_call_strikes[0]["strike"] in {20000.0, 20050.0, 20100.0}
    )


def main() -> int:
    tests = [
        ("put support activity is bullish", test_put_support_activity_is_bullish),
        ("call resistance activity is bearish", test_call_resistance_activity_is_bearish),
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
