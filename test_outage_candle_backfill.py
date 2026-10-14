import pandas as pd

from outage_candle_backfill import BROKER_SYMBOLS, _derive, _valid_one_minute


def test_exact_range_validator_rejects_bad_ohlc():
    frame = pd.DataFrame(
        {"open": [100, 100], "high": [101, 99], "low": [99, 98],
         "close": [100.5, 100], "volume": [10, 20]},
        index=pd.to_datetime(["2026-09-24 09:15+05:30", "2026-09-24 09:16+05:30"]),
    )
    result = _valid_one_minute(frame)
    assert len(result) == 1


def test_derived_hourly_bars_are_anchored_at_exchange_open():
    index = pd.date_range("2026-09-24 09:15", periods=120, freq="min", tz="Asia/Kolkata")
    frame = pd.DataFrame(
        {"open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1},
        index=index,
    )
    result = _derive(frame, "60min")
    assert [value.strftime("%H:%M") for value in result.index] == ["09:15", "10:15"]
    assert list(result.volume) == [60, 60]


def test_cross_exchange_index_symbols_have_explicit_broker_mappings():
    assert BROKER_SYMBOLS["SENSEX"] == ("SENSEX", "BSE")
    assert BROKER_SYMBOLS["NIFTYNEXT50"] == ("NIFTY NEXT 50", "NSE")
