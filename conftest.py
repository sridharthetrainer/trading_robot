"""Repo-wide pytest guard: tests must never reach external HTTP services.

2026-10-14 incident: test_option_decision_journal.py records fake "selected"
option decisions (hero_zero / alert_only / NIFTY26JUN2623500CE at spot 20,000);
record_option_decision() alerts on every "selected" row, and with .env loaded
those fixtures were posted to the live option-bot channel as real signals.

The guard answers Telegram calls with a harmless {"ok": true} instead of
raising: alerts.AlertManager spools FAILED sends to disk and the live bot
retries them later, so a raised error would still leak the message.
"""
from __future__ import annotations

import pytest
import requests


class _FakeTelegramResponse:
    status_code = 200
    ok = True
    text = '{"ok":true,"result":{}}'
    content = text.encode()

    def json(self):
        return {"ok": True, "result": {}}

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    def guarded(self, method, url, *args, **kwargs):
        if "api.telegram.org" in str(url):
            return _FakeTelegramResponse()
        raise requests.ConnectionError(
            f"External HTTP disabled in tests; mock the service response: {method} {url}"
        )

    monkeypatch.setattr(requests.sessions.Session, "request", guarded)
