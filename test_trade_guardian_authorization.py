"""Authorization regression tests for the manual-trade Guardian bot."""

import trade_guardian_bot as guardian_bot


def test_guardian_accepts_configured_chat(monkeypatch):
    monkeypatch.setattr(guardian_bot, "_chat", lambda: "12345")
    assert guardian_bot._is_authorized_message(
        {"chat": {"id": 12345}, "from": {"id": 12345}}
    )


def test_guardian_rejects_other_chat(monkeypatch):
    monkeypatch.setattr(guardian_bot, "_chat", lambda: "12345")
    assert not guardian_bot._is_authorized_message(
        {"chat": {"id": 99999}, "from": {"id": 99999}}
    )


def test_guardian_fails_closed_without_owner(monkeypatch):
    monkeypatch.setattr(guardian_bot, "_chat", lambda: "")
    assert not guardian_bot._is_authorized_message(
        {"chat": {"id": 12345}, "from": {"id": 12345}}
    )
