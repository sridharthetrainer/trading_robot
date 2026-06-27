from datetime import datetime

import pandas as pd


def test_param_trainer_normalises_cached_ohlcv():
    from autonomous_param_trainer import _normalise_backtest_data

    raw = pd.DataFrame(
        {
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000],
        }
    )
    out = _normalise_backtest_data(raw)

    assert {"Open", "High", "Low", "Close", "Volume"} <= set(out.columns)


def test_option_cache_isolated_by_underlying():
    from option_chain_fetcher import NSEOptionChainFetcher

    nifty = NSEOptionChainFetcher("NIFTY")
    banknifty = NSEOptionChainFetcher("BANKNIFTY")

    assert nifty.cache_file != banknifty.cache_file
    assert "nifty" in nifty.cache_file
    assert "banknifty" in banknifty.cache_file


def test_option_spot_sanity_rejects_cross_underlying_data():
    from option_chain_fetcher import NSEOptionChainFetcher

    assert NSEOptionChainFetcher("NIFTY")._is_plausible_spot(24056.0)
    assert not NSEOptionChainFetcher("SENSEX")._is_plausible_spot(24056.0)
    assert NSEOptionChainFetcher("SENSEX")._is_plausible_spot(82000.0)


def test_option_recorder_skips_known_nse_holiday():
    from option_chain_recorder import _in_market_hours

    assert not _in_market_hours(datetime(2026, 6, 26, 10, 0))
    assert _in_market_hours(datetime(2026, 6, 25, 10, 0))


def test_option_snapshot_live_source_allowlist_rejects_cache_aliases():
    from option_chain_recorder import _is_live_source

    assert _is_live_source("nse_live")
    assert _is_live_source("angel")
    assert not _is_live_source("stale_cache")
    assert not _is_live_source("resilience_unknown")
