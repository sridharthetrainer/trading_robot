#!/usr/bin/env python3
"""
test_option_decision_journal.py

Run:
    python test_option_decision_journal.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from option_decision_journal import (
    label_option_decision,
    label_option_shadow_decisions,
    load_recent_option_decisions,
    record_option_decision,
    repair_missing_shadow_candidates,
)


def test_record_and_load_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "journal.jsonl")
        record_option_decision(
            strategy="hero_zero",
            symbol="NIFTY",
            decision="selected",
            reason="alert_only",
            side="BUY",
            spot=20000,
            setup_score=7.5,
            quality={"ok": True, "reason": "ok_to_buy_options"},
            selected={"strike": 20300, "option_type": "CE"},
            strikes=[{"strike": 20300}],
            path=path,
        )
        rows = load_recent_option_decisions(path=path, limit=10)
        assert (
            len(rows) == 1
            and rows[0]["strategy"] == "hero_zero"
            and rows[0]["decision"] == "selected"
            and rows[0]["selected"]["strike"] == 20300
        )


def test_limit_recent_rows():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "journal.jsonl")
        for idx in range(5):
            record_option_decision(
                strategy="hero_zero",
                symbol="NIFTY",
                decision=f"d{idx}",
                path=path,
            )
        rows = load_recent_option_decisions(path=path, limit=2)
        assert len(rows) == 2 and rows[0]["decision"] == "d3" and rows[1]["decision"] == "d4"


def test_label_option_decision_by_trade_id():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "journal.jsonl")
        record_option_decision(
            strategy="hero_zero",
            symbol="NIFTY",
            decision="selected",
            trade_id="T000001",
            selected={"strike": 20300},
            path=path,
        )
        record_option_decision(
            strategy="hero_zero",
            symbol="NIFTY",
            decision="blocked_quality",
            trade_id="T000001",
            path=path,
        )
        updated = label_option_decision(
            "T000001",
            outcome_label=1,
            pnl=125.5,
            exit_reason="target_hit",
            path=path,
        )
        rows = load_recent_option_decisions(path=path, limit=10)
        selected = [r for r in rows if r["decision"] == "selected"][0]
        blocked = [r for r in rows if r["decision"] == "blocked_quality"][0]
        assert (
            updated == 1
            and selected["outcome_label"] == 1
            and selected["pnl"] == 125.5
            and selected["outcome"]["exit_reason"] == "target_hit"
            and "outcome" not in blocked
        )


def test_label_shadow_decisions_by_trade_id():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "journal.jsonl")
        record_option_decision(
            strategy="pivot_scalping",
            symbol="NIFTY",
            decision="selected",
            trade_id="T000002",
            selected={"symbol": "NIFTY20000CE", "strike": 20000, "option_type": "CE"},
            strikes=[
                {"symbol": "NIFTY20000CE", "strike": 20000, "option_type": "CE", "premium": 50},
                {"symbol": "NIFTY20050CE", "strike": 20050, "option_type": "CE", "premium": 20},
            ],
            path=path,
        )
        updated = label_option_shadow_decisions(
            "T000002",
            [{"symbol": "NIFTY20050CE", "label": 1, "pnl": 80, "exit_price": 24}],
            path=path,
        )
        rows = load_recent_option_decisions(path=path, limit=10)
        shadow = rows[0]["strikes"][1]
        selected = rows[0]["strikes"][0]
        assert (
            updated == 1
            and shadow["shadow_outcome"]["label"] == 1
            and shadow["shadow_outcome"]["pnl"] == 80.0
            and "shadow_outcome" not in selected
        )


def test_auto_shadow_candidates_for_selected():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "journal.jsonl")
        record_option_decision(
            strategy="pivot_scalping",
            symbol="NIFTY",
            decision="selected",
            side="BUY",
            selected={
                "symbol": "NIFTY26JUN2623500CE",
                "strike": 23500,
                "option_type": "CE",
                "premium": 45.5,
            },
            path=path,
        )
        rows = load_recent_option_decisions(path=path, limit=10)
        strikes = rows[0].get("strikes", [])
        assert (
            len(strikes) >= 5
            and any(s.get("strike") == 23400 for s in strikes)
            and any(s.get("strike") == 23500 for s in strikes)
            and any(s.get("strike") == 23600 for s in strikes)
            and all(s.get("synthetic_shadow") for s in strikes)
        )


def test_repair_missing_shadow_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.jsonl"
        path.write_text(
            '{"decision":"selected","side":"BUY","spot":23520,'
            '"selected":{"symbol":"NIFTY26JUN2623500CE","strike":23500,'
            '"option_type":"CE","premium":45.5},"strikes":[]}\n',
            encoding="utf-8",
        )
        result = repair_missing_shadow_candidates(path=str(path))
        rows = load_recent_option_decisions(path=str(path), limit=10)
        assert result.get("updated") == 1 and len(rows[0].get("strikes", [])) >= 5


def main() -> int:
    tests = [
        ("record and load jsonl", test_record_and_load_jsonl),
        ("limit recent rows", test_limit_recent_rows),
        ("label option decision by trade id", test_label_option_decision_by_trade_id),
        ("label shadow decisions by trade id", test_label_shadow_decisions_by_trade_id),
        ("auto shadow candidates", test_auto_shadow_candidates_for_selected),
        ("repair missing shadow candidates", test_repair_missing_shadow_candidates),
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
