#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from market_profile_context import build_market_profile_context, get_latest_market_profile


def _sample_df() -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(80):
        drift = (i - 40) * 0.03
        close = price + drift
        rows.append({
            "open": close - 0.10,
            "high": close + 0.40,
            "low": close - 0.35,
            "close": close,
            "volume": 1000 + (i % 10) * 120,
        })
    return pd.DataFrame(rows)


def test_market_profile_context_builds_and_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "profiles.db")
        ctx = build_market_profile_context(
            _sample_df(),
            symbol="NIFTY",
            side="BUY",
            persist=True,
            db_path=db_path,
        )
        assert ctx["available"] is True
        assert ctx["poc"] > 0
        assert ctx["vah"] >= ctx["val"] > 0
        assert ctx["profile_position"] in {
            "BELOW_VALUE", "LOWER_VALUE", "UPPER_VALUE", "ABOVE_VALUE"
        }
        assert -0.75 <= float(ctx["score_modifier"]) <= 0.75

        latest = get_latest_market_profile("NIFTY", db_path=db_path)
        assert latest
        assert latest["symbol"] == "NIFTY"
        assert float(latest["poc"]) > 0


def main() -> int:
    test_market_profile_context_builds_and_persists()
    print("PASS market profile context")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

