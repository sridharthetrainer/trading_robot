from datetime import date

import data_gap_filler


def test_weekend_does_not_mark_latest_exchange_session_stale(monkeypatch):
    monkeypatch.setattr(data_gap_filler, "date", type(
        "Sunday", (), {"today": staticmethod(lambda: date(2026, 10, 11))}
    ))
    monkeypatch.setattr(data_gap_filler, "SOURCES", [{
        "name": "example", "db": "x.db", "q": "unused", "fill": lambda *_: {},
    }])
    monkeypatch.setattr(data_gap_filler, "_last_date", lambda *_: "2026-10-09")
    assert data_gap_filler.freshness()[0]["stale_days"] == 0
