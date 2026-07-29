from datetime import date

import bhavcopy_backfill as bb
import bhavcopy_cache as bc


def test_backfill_skips_weekends_and_counts_correctly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bb, "_is_trading_day", lambda d: d.weekday() < 5)
    calls = []

    def fake_download(d):
        calls.append(d)
        return 5

    monkeypatch.setattr(bc, "download_bhavcopy", fake_download)

    # Mon 2026-01-12 .. Sun 2026-01-18 -> 5 weekdays, 2 weekend days
    result = bb.backfill_years(
        start=date(2026, 1, 12), end=date(2026, 1, 18), delay_sec=0.0,
    )
    assert len(calls) == 5
    assert result["days_total"] == 7
    assert result["days_skipped_non_trading"] == 2
    assert result["days_attempted"] == 5
    assert result["days_succeeded"] == 5
    assert result["rows_stored"] == 25


def test_backfill_resumability_skips_already_cached_days(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bb, "_is_trading_day", lambda d: d.weekday() < 5)

    conn = bc._init_db()
    conn.execute(
        "INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)",
        (bb._ANCHOR_SYMBOL, date(2026, 1, 12).isoformat(), 100, 101, 99, 100, 1000),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(bc, "download_bhavcopy", lambda d: calls.append(d) or 3)

    result = bb.backfill_years(start=date(2026, 1, 12), end=date(2026, 1, 16), delay_sec=0.0)
    assert date(2026, 1, 12) not in calls  # already cached, skipped
    assert result["days_skipped_cached"] == 1
    assert result["days_attempted"] == 4  # Jan 13-16, weekdays


def test_backfill_summary_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bb, "_is_trading_day", lambda d: d.weekday() < 5)
    monkeypatch.setattr(bc, "download_bhavcopy", lambda d: 0)

    result = bb.backfill_years(start=date(2026, 1, 12), end=date(2026, 1, 20), delay_sec=0.0)
    for key in (
        "start", "end", "days_total", "days_skipped_cached",
        "days_skipped_non_trading", "days_attempted", "days_succeeded",
        "days_empty", "rows_stored",
    ):
        assert key in result
    assert result["days_empty"] == result["days_attempted"]
    assert result["days_succeeded"] == 0
