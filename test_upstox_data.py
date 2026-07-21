from datetime import datetime, timedelta

import upstox_data as ud


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _candle_payload(rows):
    """rows: list of (iso_timestamp, o, h, l, c, v)."""
    return {"data": {"candles": [list(r) for r in rows]}}


def test_stale_historical_response_is_merged_with_fresh_intraday(monkeypatch):
    """2026-07-21 regression: Upstox's historical-candle endpoint never
    includes today's in-progress session by design, but a multi-day
    request always has enough PAST days to satisfy the old len>=2 check,
    so the /intraday/ fallback was unreachable for reliable instruments
    (every NSE/BSE index) -- confirmed live: 6 major indices frozen at
    the prior day's close for 2+ consecutive trading days. Historical
    response ending yesterday must now trigger an intraday fetch and a
    merge that actually advances to today."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    yesterday2 = (datetime.now() - timedelta(days=1, hours=1)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    today_920 = datetime.now().replace(hour=9, minute=20, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S+05:30")
    today_925 = datetime.now().replace(hour=9, minute=25, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S+05:30")

    hist_rows = [(yesterday2, 99, 100, 98, 100, 900),
                 (yesterday, 100, 101, 99, 100.5, 1000)]
    intra_rows = [(today_920, 101, 102, 100, 101.5, 1200),
                  (today_925, 101.5, 103, 101, 102, 1300)]

    def _fake_get(url, headers=None, timeout=None):
        if "/intraday/" in url:
            return _FakeResp(200, _candle_payload(intra_rows))
        return _FakeResp(200, _candle_payload(hist_rows))

    monkeypatch.setattr(ud.requests, "get", _fake_get)
    monkeypatch.setattr(ud, "_rate_limit", lambda: None)

    df = ud.get_candles("NIFTY", interval="1m", days=5)
    assert df is not None
    assert df.index[-1].date() == datetime.now().date(), (
        "merged result must advance to today, not stay stuck on yesterday's historical response")
    assert len(df) == 4  # 2 historical rows + 2 fresh intraday rows


def test_historical_already_current_skips_intraday_call(monkeypatch):
    """When the historical response already includes today, no /intraday/
    call should even be attempted (avoid the extra request)."""
    today_row = datetime.now().replace(hour=9, minute=20, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S+05:30")
    today_row2 = datetime.now().replace(hour=9, minute=25, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S+05:30")
    hist_rows = [(today_row, 100, 101, 99, 100.5, 1000),
                 (today_row2, 100.5, 102, 100, 101, 1100)]

    calls = {"intraday": 0}

    def _fake_get(url, headers=None, timeout=None):
        if "/intraday/" in url:
            calls["intraday"] += 1
            return _FakeResp(200, _candle_payload([]))
        return _FakeResp(200, _candle_payload(hist_rows))

    monkeypatch.setattr(ud.requests, "get", _fake_get)
    monkeypatch.setattr(ud, "_rate_limit", lambda: None)

    df = ud.get_candles("NIFTY", interval="1m", days=5)
    assert df is not None
    assert len(df) == 2
    assert calls["intraday"] == 0


def test_no_historical_falls_back_to_intraday_only(monkeypatch):
    """Historical call fails/empty entirely -- must still return intraday
    data alone rather than None."""
    today_row = datetime.now().replace(hour=9, minute=20, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S+05:30")
    today_row2 = datetime.now().replace(hour=9, minute=25, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S+05:30")
    intra_rows = [(today_row, 100, 101, 99, 100.5, 1000),
                  (today_row2, 100.5, 102, 100, 101, 1100)]

    def _fake_get(url, headers=None, timeout=None):
        if "/intraday/" in url:
            return _FakeResp(200, _candle_payload(intra_rows))
        return _FakeResp(200, _candle_payload([]))

    monkeypatch.setattr(ud.requests, "get", _fake_get)
    monkeypatch.setattr(ud, "_rate_limit", lambda: None)

    df = ud.get_candles("NIFTY", interval="1m", days=5)
    assert df is not None
    assert len(df) == 2
