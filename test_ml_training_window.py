"""All ML training must run inside the 07:00–21:00 window (config-driven)."""
from datetime import datetime

import pytest

from trading_calendar import in_ml_training_window, ml_training_window


def test_window_default_and_boundaries():
    assert ml_training_window() == ("07:00", "21:00")
    assert in_ml_training_window(datetime(2026, 6, 28, 10, 0))[0] is True   # inside
    assert in_ml_training_window(datetime(2026, 6, 28, 7, 0))[0] is True    # start incl
    assert in_ml_training_window(datetime(2026, 6, 28, 21, 0))[0] is True   # end incl
    assert in_ml_training_window(datetime(2026, 6, 28, 6, 59))[0] is False  # before
    assert in_ml_training_window(datetime(2026, 6, 28, 21, 1))[0] is False  # after


def test_window_is_env_configurable(monkeypatch):
    monkeypatch.setenv("ML_TRAINING_WINDOW_START", "09:30")
    monkeypatch.setenv("ML_TRAINING_WINDOW_END", "18:00")
    assert ml_training_window() == ("09:30", "18:00")
    assert in_ml_training_window(datetime(2026, 6, 28, 8, 0))[0] is False
    assert in_ml_training_window(datetime(2026, 6, 28, 12, 0))[0] is True


def _force_window_closed(monkeypatch):
    """Deterministically force the ML window CLOSED — stub the shared helper so the
    test is independent of wall-clock time, weekday, and env pollution from other
    tests (the entry points re-import in_ml_training_window from trading_calendar
    at call time, so patching the module attribute takes effect)."""
    import trading_calendar
    monkeypatch.setattr(trading_calendar, "in_ml_training_window",
                        lambda *a, **k: (False, "00:00-00:01"))


def test_post_market_ml_skips_outside_window(monkeypatch):
    _force_window_closed(monkeypatch)
    import post_market_ml
    # The post-market (after-15:30) guard runs before the window guard; bypass it
    # so this test isolates the WINDOW behaviour regardless of wall-clock time.
    monkeypatch.setattr(post_market_ml, "_is_post_market", lambda: True)
    out = post_market_ml.run_pipeline(force=False)
    assert out.get("error") == "outside_training_window"


def test_param_trainer_skips_outside_window(monkeypatch):
    _force_window_closed(monkeypatch)
    import autonomous_param_trainer as apt
    out = apt.run_autonomous_param_training(force=False)
    assert out.get("error") == "outside_training_window"


def test_self_learning_skips_outside_window(monkeypatch):
    _force_window_closed(monkeypatch)
    import self_learning_engine
    eng = self_learning_engine.SelfLearningEngine.__new__(self_learning_engine.SelfLearningEngine)
    out = eng.run(force=False)
    assert out.get("status") == "skipped_outside_training_window"
