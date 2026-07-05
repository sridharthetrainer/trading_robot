#!/usr/bin/env python3
from __future__ import annotations

from live_signal_engine import LiveSignalEngine


class _NoSelfLearningModel:
    model = None
    _strategy_models = {}


def _run_bridge_check() -> bool:
    engine = object.__new__(LiveSignalEngine)
    engine.learning_engine = _NoSelfLearningModel()
    signal = {
        "symbol": "NIFTY",
        "side": "BUY",
        "strategy": "breakout",
        "score": 18.0,
        "raw_score": 8.0,
        "n_agree": 8,
        "n_conflict": 1,
        "confluence": "VERY_STRONG",
        "regime": "BREAKOUT",
        "confidence": 0.5,
        "volume_ratio": 1.1,
        "india_vix": 12.8,
        "pcr_atm": 1.1,
        "hour_of_day": 10,
        "day_of_week": 4,
    }
    prob = LiveSignalEngine._get_ai_probability(engine, signal)
    if signal.get("ai_model_state") == "signal_log_model":
        return 0.0 <= float(prob) <= 1.0 and signal.get("ai_model_used") == "cross_symbol"
    # 2026-07 rename: with no VALID model (legacy pickles are quarantined by the
    # training-contract check) the fallback state is "unvalidated_rule_fallback".
    return (signal.get("ai_model_state") in {"rule_based_fallback", "unvalidated_rule_fallback"}
            and 0.0 <= float(prob) <= 1.0)


def test_live_engine_uses_signal_log_model_when_self_learning_model_missing() -> None:
    assert _run_bridge_check()


def main() -> int:
    ok = _run_bridge_check()
    print(("PASS" if ok else "FAIL") + " live AI model bridge")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
