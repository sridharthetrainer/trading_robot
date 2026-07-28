import json
from pathlib import Path

from telegram_commands import TelegramCommandHandler


def test_failed_photo_is_spooled_with_local_copy(tmp_path, monkeypatch):
    """Regression for a 2026-07-28 audit finding: TelegramCommandHandler's
    send_photo (used by interactive chart/OI commands) previously just logged
    a warning and dropped the image on failure, unlike alerts.py's
    AlertManager.send_photo which persists a retry copy."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "chart.png"
    source.write_bytes(b"not-a-real-png-but-persistent")

    handler = TelegramCommandHandler(bot_token="token", chat_id="1")
    monkeypatch.setattr(handler, "_post_photo", lambda *a, **kw: False)

    assert handler.send_photo(str(source), "evidence") is False
    spool_file = handler._media_spool_dir / "spool.jsonl"
    row = json.loads(spool_file.read_text().strip())
    assert row["caption"] == "evidence"
    assert Path(row["photo_path"]).exists()


def test_successful_photo_send_does_not_spool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "chart.png"
    source.write_bytes(b"data")

    handler = TelegramCommandHandler(bot_token="token", chat_id="1")
    monkeypatch.setattr(handler, "_post_photo", lambda *a, **kw: True)

    assert handler.send_photo(str(source), "evidence") is True
    assert not (handler._media_spool_dir / "spool.jsonl").exists()


def test_spooled_photo_is_retried_and_removed_on_next_send(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "chart.png"
    source.write_bytes(b"data")

    handler = TelegramCommandHandler(bot_token="token", chat_id="1")
    monkeypatch.setattr(handler, "_post_photo", lambda *a, **kw: False)
    handler.send_photo(str(source), "first")
    spool_file = handler._media_spool_dir / "spool.jsonl"
    assert spool_file.exists()

    # Next send succeeds and, on its opportunistic flush, should clear the backlog.
    monkeypatch.setattr(handler, "_post_photo", lambda *a, **kw: True)
    handler._last_spool_flush = 0.0
    handler.send_photo(str(source), "second")

    assert spool_file.read_text().strip() == ""


def test_media_spool_is_isolated_per_bot_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = TelegramCommandHandler(bot_token="token-a", chat_id="1")
    second = TelegramCommandHandler(bot_token="token-b", chat_id="1")
    assert first._media_spool_dir != second._media_spool_dir
