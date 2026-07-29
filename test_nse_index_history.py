from datetime import date

import pandas as pd

import nse_index_history as nih


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_backfill_index_unknown_symbol_returns_error():
    result = nih.backfill_index("NOTANINDEX")
    assert "error" in result


def test_fetch_chunk_parses_real_shaped_response():
    payload = {
        "data": {
            "indexCloseOnlineRecords": [
                {
                    "EOD_TIMESTAMP": "01-Jan-2020", "EOD_OPEN_INDEX_VAL": "12200.5",
                    "EOD_HIGH_INDEX_VAL": "12250.0", "EOD_LOW_INDEX_VAL": "12150.0",
                    "EOD_CLOSE_INDEX_VAL": "12220.1", "TRADED_QTY": "0",
                },
                {
                    "EOD_TIMESTAMP": "02-Jan-2020", "EOD_OPEN_INDEX_VAL": "12220.0",
                    "EOD_HIGH_INDEX_VAL": "12300.0", "EOD_LOW_INDEX_VAL": "12200.0",
                    "EOD_CLOSE_INDEX_VAL": "12280.0", "TRADED_QTY": "0",
                },
            ]
        }
    }

    class _FakeSession:
        def get(self, url, timeout=15):
            return _FakeResponse(200, payload)

    df = nih._fetch_chunk(_FakeSession(), "NIFTY 50", date(2020, 1, 1), date(2020, 1, 2))
    assert len(df) == 2
    assert df.iloc[0]["close"] == 12220.1
    assert df.index[0] < df.index[1]


def test_fetch_chunk_returns_empty_df_on_no_data():
    class _FakeSession:
        def get(self, url, timeout=15):
            return _FakeResponse(200, {"data": {"indexCloseOnlineRecords": []}})

    df = nih._fetch_chunk(_FakeSession(), "NIFTY 50", date(2020, 1, 1), date(2020, 1, 2))
    assert df.empty


def test_fetch_chunk_retries_then_gives_up(monkeypatch):
    monkeypatch.setattr(nih.time, "sleep", lambda s: None)
    calls = []

    class _FakeSession:
        def get(self, url, timeout=15):
            calls.append(url)
            return _FakeResponse(503)

    df = nih._fetch_chunk(_FakeSession(), "NIFTY 50", date(2020, 1, 1), date(2020, 1, 2), retries=3)
    assert df.empty
    assert len(calls) == 3


def test_backfill_index_chunks_wide_range_and_stores(monkeypatch):
    saved = []
    monkeypatch.setattr(nih, "_session", lambda: object())
    monkeypatch.setattr(nih.time, "sleep", lambda s: None)

    def fake_fetch_chunk(session, index_name, start, end, **kw):
        idx = pd.date_range(start, end, freq="7D")
        if len(idx) == 0:
            return pd.DataFrame()
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 0},
            index=idx,
        )

    monkeypatch.setattr(nih, "_fetch_chunk", fake_fetch_chunk)

    def fake_save_candles(symbol, interval, df):
        saved.append((symbol, interval, len(df)))
        return len(df)

    import candle_cache
    monkeypatch.setattr(candle_cache, "save_candles", fake_save_candles)

    result = nih.backfill_index("NIFTY", start=date(2016, 1, 1), end=date(2026, 1, 1), delay_sec=0.0)

    assert result["symbol"] == "NIFTY"
    assert result["chunks_ok"] > 10  # ~10 years / 300-day chunks
    assert result["rows_stored"] == sum(n for _, _, n in saved)
    assert all(s == "NIFTY" and i == "1d" for s, i, _ in saved)
