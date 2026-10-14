from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from PIL import Image
import pytest

import option_decision_journal as journal
import option_bot_views as views
from option_multistrike_signals import ensure_multistrike_schema
from option_visual_cards import (
    signal_view, signal_caption, option_navigation, premium_history,
    render_option_signal_card, render_option_board,
)


@pytest.fixture(autouse=True)
def no_external_messages(monkeypatch):
    monkeypatch.setattr(journal, "_option_alerts", lambda: None)


def setup(**extra):
    return dict(underlying="NIFTY", strike=24250, option_type="CE", side="BUY",
                direction="BULLISH", entry_price=100, stop_loss=85,
                target_1=120, target_2=135, score=73, tradable=False,
                expiry="2026-10-20", snapshot_time="2026-10-14T10:30:00+05:30", **extra)


def test_watch_is_not_promoted_by_high_score():
    row = setup()
    row["score"] = 99
    assert signal_view(row)["status"] == "WATCH / BLOCKED"
    assert "WATCH / BLOCKED" in signal_caption(row)


def test_finished_signal_is_not_presented_as_new_setup():
    row = setup(lifecycle_status="TARGET2_HIT")
    row["tradable"] = True
    assert signal_view(row)["status"] == "SIGNAL CLOSED"
    assert signal_view(row)["lifecycle"] == "TARGET 2 HIT"


def test_put_purchase_is_bearish_and_execution_side_is_buy():
    row = {"symbol": "NIFTY", "side": "SELL", "decision": "selected_pre_trade",
           "selected": {"strike": 24250, "option_type": "PE", "trade_side": "BUY",
                        "premium": 100, "stop_loss": 85, "target_price": 120}}
    v = signal_view(row)
    assert v["direction"] == "BEARISH" and v["side"] == "BUY"
    assert v["valid_plan"] and v["rr"] == pytest.approx(20 / 15)
    assert "BUY" in journal._format_option_card(row)


def test_missing_levels_are_unknown_not_zero_or_invented():
    row = {"symbol": "NIFTY", "selected": {"premium": 100}, "setup_score": 7.5}
    v = signal_view(row)
    assert v["stop"] is None and v["target"] is None and v["rr"] is None
    assert v["score"] is None and v["setup_score"] == 7.5
    assert "₹0.00" not in signal_caption(row)


def test_research_multileg_is_not_a_directional_trade():
    v = signal_view({"symbol": "NIFTY", "side": "SELL", "strategy": "C3",
                     "metadata": {"unvalidated": True},
                     "selected": {"legs": [{"strike": 25000, "option_type": "CE", "side": "SELL"}]}})
    assert v["status"] == "SHADOW RESEARCH" and v["direction"] == "MULTI-LEG"


def test_caption_is_escaped_and_fits_telegram_photo_limit():
    row = setup()
    row["underlying"] = "NIFTY <bad> & test"
    caption = signal_caption(row)
    assert "<bad>" not in caption and "&lt;bad&gt;" in caption
    assert "&amp;" in caption and len(caption) < 1024


def test_navigation_contains_only_existing_read_commands():
    actions = [button["callback_data"] for line in option_navigation()["inline_keyboard"] for button in line]
    assert set(actions) == {"cmd:report", "cmd:signals", "cmd:oichart", "cmd:positions"}


def test_signal_and_empty_board_render_valid_portrait_pngs(tmp_path):
    paths = [render_option_signal_card(setup(), output_dir=str(tmp_path)),
             render_option_board([], day="2026-10-14", output_dir=str(tmp_path))]
    for path in paths:
        with Image.open(path) as image:
            assert image.format == "PNG" and image.height > image.width >= 1000
            image.verify()


def _database(path):
    with sqlite3.connect(path) as conn:
        ensure_multistrike_schema(conn)
        conn.executemany("""INSERT INTO option_strike_signals
            (ts,snapshot_time,underlying,expiry,strike,option_type,signal,price,source,tradable,score,flow,direction)
            VALUES (?,?,?,?,?,?,'BUY_CE',?,'angel',0,73,'LONG_BUILDUP','BULLISH')""", [
            (1,"2026-10-14T10:00:00+05:30","NIFTY","2026-10-20",24250,"CE",100),
            (2,"2026-10-14T10:10:00+05:30","NIFTY","2026-10-27",24250,"CE",200),
            (3,"2026-10-14T10:20:00+05:30","NIFTY","2026-10-20",24250,"CE",110),
            (4,"2026-10-14T10:40:00+05:30","NIFTY","2026-10-20",24250,"CE",120),
        ])
        conn.execute("UPDATE option_strike_signals SET source='historical_replay' WHERE ts=3")


def test_quote_history_excludes_wrong_expiry_future_and_research(tmp_path):
    db = tmp_path / "signals.db"
    _database(db)
    before = db.read_bytes()
    result = premium_history(setup(), str(db))
    assert [price for _, price in result] == [100]
    assert result[0][0].hour == 10
    assert db.read_bytes() == before


def test_signal_board_keeps_expiries_distinct_and_does_not_write_database(tmp_path, monkeypatch):
    db = tmp_path / "signals.db"
    _database(db)
    monkeypatch.setattr(views, "DB_PATH", str(db))
    before = db.read_bytes()
    rows = views.latest_signal_rows("2026-10-14")
    assert len(rows) == 2
    assert {row["expiry"] for row in rows} == {"2026-10-20", "2026-10-27"}
    assert db.read_bytes() == before


class FakeAlerts:
    def __init__(self, text_sent=True):
        self.text_sent = text_sent
        self.texts = []
        self.photos = []
        self.dedup = set()

    def send(self, text, **kw):
        self.texts.append((text, kw))
        return self.text_sent

    def send_photo(self, path, caption):
        self.photos.append((path, caption))
        return True

    def _is_dedup_blocked(self, key, cooldown):
        return key in self.dedup

    def _mark_dedup_sent(self, key):
        self.dedup.add(key)


def test_generated_alert_attaches_visual_and_read_only_buttons(monkeypatch):
    import option_visual_cards as cards
    fake = FakeAlerts()
    monkeypatch.setattr(journal, "_option_alerts", lambda: fake)
    monkeypatch.setenv("OPTION_VISUAL_CARDS", "true")
    monkeypatch.setattr(cards, "premium_history", lambda payload: [])
    monkeypatch.setattr(cards, "render_option_signal_card", lambda *a, **kw: "preview.png")
    row = setup(signal="BUY_CE", flow="LONG_BUILDUP")
    count = journal.alert_generated_option_signals(underlying="NIFTY", snapshot_time=row["snapshot_time"],
                                                   expiry=row["expiry"], signals=[row])
    assert count == 1 and len(fake.photos) == 1
    assert fake.texts[0][1]["reply_markup"] == option_navigation()
    journal.alert_generated_option_signals(underlying="NIFTY", snapshot_time=row["snapshot_time"],
                                          expiry=row["expiry"], signals=[row])
    assert len(fake.photos) == 1  # watch photo cooldown


def test_failed_or_deduplicated_text_does_not_duplicate_photo(monkeypatch):
    fake = FakeAlerts(text_sent=False)
    monkeypatch.setattr(journal, "_option_alerts", lambda: fake)
    assert not journal._alert_option_selection({"decision": "selected", "symbol": "NIFTY", "selected": {}})
    assert not fake.photos


def test_renderer_failure_preserves_successful_text_notification(monkeypatch):
    import option_visual_cards as cards
    fake = FakeAlerts()
    monkeypatch.setattr(journal, "_option_alerts", lambda: fake)
    monkeypatch.setenv("OPTION_VISUAL_CARDS", "true")
    monkeypatch.setattr(cards, "premium_history", lambda payload: [])
    monkeypatch.setattr(cards, "render_option_signal_card", lambda *a, **kw: (_ for _ in ()).throw(ValueError("render error")))
    assert journal._alert_option_selection({"decision": "selected", "symbol": "NIFTY", "selected": {}})
    assert fake.texts and not fake.photos


def test_report_switch_uses_indian_time():
    from option_telegram_report import is_post_market
    assert is_post_market(datetime(2026, 10, 14, 10, 10, tzinfo=ZoneInfo("UTC")))
    assert not is_post_market(datetime(2026, 10, 14, 9, 0, tzinfo=ZoneInfo("UTC")))
