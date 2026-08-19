"""Regression test for the other half of the 2026-08-18 incident:
_fetch_candles() (manual_trade_tracker.py) called Angel's getCandleData
directly and, when Angel alone came back empty, gave up -- logging
"Struct-stop NOT armed ... no underlying candles" and leaving the position
protected only by a wide catastrophe-floor GTT. Verified against real logs
that data_fetcher/upstox_data successfully returned 1147 bars of NIFTY 1m
data for the rest of the system in the SAME minute -- Angel was a single
point of failure, not a real data gap.

Fix: fall back to the system's multi-source DataFetcher when Angel's direct
call is empty/short. This test verifies that fallback path in isolation,
without any network access.
"""
import threading

import pandas as pd

import manual_trade_tracker as mtt


class _EmptyAngel:
    """Angel client whose direct historical-data call always comes back empty,
    matching the 2026-08-18 log ("Token not found for NIFTY18AUG2624200CE")."""
    def get_historical_data(self, symbol, interval=None, from_date=None,
                             to_date=None, exchange=None):
        return None


def _make_df(rows=30):
    idx = pd.date_range("2026-08-18 09:00", periods=rows, freq="1min")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                          "volume": 1}, index=idx)


class _FakeDataFetcher:
    def __init__(self, df):
        self._df = df
        self.calls = []

    def get_market_data(self, symbol, interval="5m", days=5):
        self.calls.append((symbol, interval, days))
        return self._df


def _bare_tracker():
    inst = object.__new__(mtt.ManualTradeTracker)
    inst._angel = _EmptyAngel()
    inst._lock = threading.Lock()
    return inst


def test_falls_back_to_datafetcher_when_angel_returns_nothing(monkeypatch):
    tracker = _bare_tracker()
    fake_fetcher = _FakeDataFetcher(_make_df(30))
    monkeypatch.setattr(tracker, "_data_fetcher", lambda: fake_fetcher)

    df = tracker._fetch_candles("NIFTY", "NSE", days=5, interval="FIVE_MINUTE")

    assert df is not None and len(df) == 30
    assert fake_fetcher.calls == [("NIFTY", "5m", 5)], (
        "FIVE_MINUTE must map to DataFetcher's '5m' interval string"
    )


def test_does_not_fall_back_when_angel_already_has_enough_bars(monkeypatch):
    class _GoodAngel:
        def get_historical_data(self, *a, **k):
            return _make_df(25)

    tracker = _bare_tracker()
    tracker._angel = _GoodAngel()
    fake_fetcher = _FakeDataFetcher(_make_df(30))
    monkeypatch.setattr(tracker, "_data_fetcher", lambda: fake_fetcher)

    df = tracker._fetch_candles("NIFTY", "NSE")

    assert len(df) == 25
    assert fake_fetcher.calls == [], "should not touch the fallback when Angel already succeeded"


def test_returns_none_when_both_sources_fail(monkeypatch):
    tracker = _bare_tracker()
    monkeypatch.setattr(tracker, "_data_fetcher", lambda: _FakeDataFetcher(None))

    assert tracker._fetch_candles("NIFTY", "NSE") is None
