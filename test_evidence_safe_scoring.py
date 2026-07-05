from signal_engine import _evidence_safe_context_modifier


def test_unvalidated_positive_context_cannot_boost():
    assert _evidence_safe_context_modifier(3.0, {}) == 0.0


def test_context_headwind_is_preserved():
    assert _evidence_safe_context_modifier(-1.25, {}) == -1.25


def test_forward_validated_switch_restores_boost():
    assert _evidence_safe_context_modifier(2.0, {"allow_unvalidated_context_boosts": True}) == 2.0
