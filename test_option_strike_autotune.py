#!/usr/bin/env python3
"""
test_option_strike_autotune.py

Run:
    python test_option_strike_autotune.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from option_decision_journal import record_option_decision
from option_strike_autotune import (
    build_strike_autotune,
    score_candidate_with_autotune,
)


def _record(path: str, premium: float, won: bool, pnl: float, spread: float = 0.05) -> None:
    record_option_decision(
        strategy="hero_zero",
        symbol="NIFTY",
        decision="selected",
        side="BUY",
        spot=20000,
        setup_score=7.5,
        quality={"ok": True, "implied_move_pct": 1.0, "move_used_ratio": 0.2},
        selected={
            "strike": 20300,
            "option_type": "CE",
            "premium": premium,
            "spread_pct": spread,
            "oi": 1200,
            "volume": 1500,
            "quality_score": 0.8,
            "otm_pct": 1.5,
        },
        outcome={"label": 1 if won else -1, "pnl": pnl},
        path=path,
    )


def test_autotune_neutral_until_min_samples():
    with tempfile.TemporaryDirectory() as tmp:
        journal = str(Path(tmp) / "journal.jsonl")
        out = str(Path(tmp) / "autotune.json")
        for _ in range(2):
            _record(journal, premium=20, won=True, pnl=100)
        model = build_strike_autotune(journal_file=journal, output_file=out, min_samples=5)
        assert (
            model["labelled_selected"] == 2
            and all(weight == 1.0 for weight in model["feature_weights"].values())
        )


def test_autotune_rewards_winning_feature():
    with tempfile.TemporaryDirectory() as tmp:
        journal = str(Path(tmp) / "journal.jsonl")
        out = str(Path(tmp) / "autotune.json")
        for _ in range(8):
            _record(journal, premium=20, won=True, pnl=120)
        for _ in range(2):
            _record(journal, premium=20, won=False, pnl=-50)
        model = build_strike_autotune(journal_file=journal, output_file=out, min_samples=5)
        weight = model["feature_weights"].get("premium:15-35", 1.0)
        scored = score_candidate_with_autotune(
            {
                "premium": 20,
                "spread_pct": 0.05,
                "oi": 1200,
                "volume": 1500,
                "quality_score": 0.8,
                "otm_pct": 1.5,
            },
            quality={"implied_move_pct": 1.0, "move_used_ratio": 0.2},
            side="BUY",
            autotune=model,
        )
        assert weight > 1.0 and scored["multiplier"] > 1.0


def test_autotune_learns_from_shadow_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        journal = str(Path(tmp) / "journal.jsonl")
        out = str(Path(tmp) / "autotune.json")
        for _ in range(10):
            record_option_decision(
                strategy="pivot_scalping",
                symbol="NIFTY",
                decision="selected",
                side="BUY",
                selected={
                    "strike": 20000,
                    "option_type": "CE",
                    "premium": 50,
                    "quality_score": 0.8,
                },
                strikes=[
                    {
                        "strike": 20050,
                        "option_type": "CE",
                        "premium": 20,
                        "quality_score": 0.8,
                        "shadow_outcome": {"label": 1, "pnl": 100},
                    }
                ],
                path=journal,
            )
        model = build_strike_autotune(journal_file=journal, output_file=out, min_samples=5)
        assert (
            model["labelled_selected"] == 0
            and model["labelled_shadow"] == 10
            and model["feature_weights"].get("premium:15-35", 1.0) > 1.0
        )


def main() -> int:
    tests = [
        ("neutral until min samples", test_autotune_neutral_until_min_samples),
        ("rewards winning feature", test_autotune_rewards_winning_feature),
        ("learns from shadow candidates", test_autotune_learns_from_shadow_candidates),
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
