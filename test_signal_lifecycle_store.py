import pytest

from signal_lifecycle import SignalLifecycleStore


def test_lifecycle_store_persists_ordered_transitions(tmp_path):
    store = SignalLifecycleStore(str(tmp_path / "lifecycle.db"))
    for stage in ("GENERATED", "PRICED", "LIQUID", "COST_POSITIVE"):
        store.transition("signal-1", stage, metadata={"symbol": "NIFTY"})
    assert store.current_stage("signal-1") == "COST_POSITIVE"
    assert store.actionable("signal-1") is True
    assert store.actionable("signal-1", live=True) is False


def test_lifecycle_store_rejects_skipped_or_duplicate_stage(tmp_path):
    store = SignalLifecycleStore(str(tmp_path / "lifecycle.db"))
    with pytest.raises(ValueError, match="illegal_lifecycle_transition"):
        store.transition("signal-1", "LIQUID")
    store.transition("signal-1", "GENERATED")
    with pytest.raises(ValueError, match="illegal_lifecycle_transition"):
        store.transition("signal-1", "GENERATED")
