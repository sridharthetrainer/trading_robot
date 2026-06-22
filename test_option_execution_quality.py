#!/usr/bin/env python3
from __future__ import annotations

from option_execution_quality import evaluate_selected_option_execution
from trade_autopsy import classify_trade_autopsy


class _Trade:
    symbol = "NIFTY26062523000CE"
    side = "BUY"
    entry_price = 100.0
    exit_price = 90.0
    stop_loss = 92.0
    target_price = 120.0
    qty = 65
    realized_pnl = -650.0
    exit_reason = "trailing_stop"
    metadata = {
        "style": "scalping",
        "option_execution_quality": {"score": 72, "warnings": ["spread_unknown"]},
        "costs": {"total": 120.0},
        "gross_pnl": -530.0,
    }


def test_good_selected_option_quality() -> None:
    result = evaluate_selected_option_execution(
        {
            "premium": 100,
            "oi": 5000,
            "volume": 2000,
            "bid": 99,
            "ask": 101,
            "dte": 2,
            "strike_type": "ATM",
        },
        min_oi=100,
        min_volume=100,
        max_spread_pct=0.05,
    )
    assert result.ok
    assert result.score >= 90


def test_bad_selected_option_quality_blocks() -> None:
    result = evaluate_selected_option_execution(
        {"premium": 0.5, "oi": 10, "volume": 5, "bid": 90, "ask": 120, "dte": 0},
        min_oi=100,
        min_volume=100,
        max_spread_pct=0.05,
    )
    assert not result.ok
    assert {"premium_too_low", "oi_too_low", "volume_too_low", "spread_too_wide"}.issubset(
        set(result.hard_blocks)
    )


def test_trade_autopsy_tags_loss() -> None:
    out = classify_trade_autopsy(_Trade())
    tags = set(out.get("tags", []))
    assert out.get("label") == -1
    assert "direction_failed" in tags
    assert "option_execution_quality_low" in tags
    assert "style:scalping" in tags


def main() -> int:
    tests = [
        ("good selected option quality", test_good_selected_option_quality),
        ("bad selected option quality blocks", test_bad_selected_option_quality_blocks),
        ("trade autopsy tags loss", test_trade_autopsy_tags_loss),
    ]
    failed = 0
    for name, fn in tests:
        try:
            result = fn()
            ok = True if result is None else bool(result)
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        print(("PASS" if ok else "FAIL"), name)
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
