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

import os
import tempfile

import pytest
import requests

# Must run before any test module imports runtime_telemetry (DB path is read at
# import): tests record telemetry in a throwaway DB, never the live bot's one.
os.environ["RUNTIME_TELEMETRY_DB"] = os.path.join(tempfile.mkdtemp(prefix="pytest_telemetry_"), "runtime_telemetry.db")


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


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Run a test in an empty working directory.

    2026-10-17 audit: several tests wrote relative-path runtime state into the
    LIVE bot's files (trades.db, signal_log.db, score_calibration.json,
    rl_qtable.json, option_decision_journal.jsonl, ...), nudging learned state
    on every test run. Test files opt in with
    ``pytestmark = pytest.mark.usefixtures("isolated_cwd")``.
    """
    # Small read-only reference inputs are COPIED (never linked), so a test cannot
    # write through to the real file.
    import shutil
    from pathlib import Path
    repo = Path(__file__).resolve().parent
    for name in ("trading_holidays.json",):
        if (repo / name).exists():
            shutil.copy2(repo / name, tmp_path / name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_request_governor(tmp_path, monkeypatch):
    """Tests must never share Angel request pacing with the running bot.

    request_governor keeps cross-process pacing in
    .request_governor_state/<provider>.last (absolute path). A test that reached
    angel.connect() waited ~2 minutes for the live bot's login slot and then
    stamped that file, delaying the bot's next login. Point it at a temp dir.
    """
    try:
        import request_governor
    except Exception:
        return
    monkeypatch.setattr(request_governor, "_STATE_DIR", tmp_path / ".request_governor_state")
