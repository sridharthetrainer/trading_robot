"""Queued Telegram alerts older than the max age must be dropped, not delivered."""
import json
import time

import alerts


def _manager(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    am = alerts.AlertManager(bot_token="T", chat_id="1", name="t")
    monkeypatch.setattr(am, "_post_telegram", lambda data, timeout=10: sent.append(data["text"]) or True)
    monkeypatch.setattr(am, "_post_photo", lambda path, caption: sent.append(caption) or True)
    return am


def _spool(am, items):
    am._spool_file.write_text("\n".join(json.dumps(i) for i in items) + "\n")


def test_stale_items_dropped_fresh_items_sent(tmp_path, monkeypatch):
    sent = []
    am = _manager(tmp_path, monkeypatch, sent)
    old_photo = tmp_path / "old.png"
    old_photo.write_bytes(b"x")
    _spool(am, [
        {"ts": time.time() - 90 * 86400, "data": {"text": "CRITICAL chaos test (June)"}},
        {"ts": time.time() - 7 * 3600, "data": {"text": "OPTION SELECTED stale"}},
        {"ts": time.time() - 30 * 86400, "kind": "photo", "photo_path": str(old_photo), "caption": "old card"},
        {"ts": time.time() - 60, "data": {"text": "fresh alert"}},
    ])
    am._last_spool_flush = 0
    assert am.flush_spool(limit=5) == 1
    assert sent == ["fresh alert"]
    assert not old_photo.exists()                       # stale image removed, never posted
    assert am._spool_file.read_text().strip() == ""


def test_max_age_is_configurable(tmp_path, monkeypatch):
    sent = []
    am = _manager(tmp_path, monkeypatch, sent)
    monkeypatch.setenv("TELEGRAM_SPOOL_MAX_AGE_SEC", str(10 * 86400))
    _spool(am, [{"ts": time.time() - 2 * 86400, "data": {"text": "two days old"}}])
    am._last_spool_flush = 0
    assert am.flush_spool(limit=5) == 1 and sent == ["two days old"]
