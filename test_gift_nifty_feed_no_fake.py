"""The GIFT Nifty feed must never present NIFTY's own price as GIFT."""
import sys
import types

import pandas as pd


def test_gift_feed_returns_zero_without_genuine_gift(monkeypatch):
    calls = []

    def download(ticker, **kw):
        calls.append(ticker)
        if ticker == "GN=F":
            return pd.DataFrame()                                   # no genuine GIFT quote
        return pd.DataFrame({"Close": [22622.7]})                   # NIFTY-like data

    monkeypatch.setitem(sys.modules, "yf_compat", types.SimpleNamespace(download=download))
    import market_data_feeds as m
    assert m.GIFTNiftyFeed()._fetch_gift_nifty() == 0.0
    assert calls == ["GN=F"]                                        # no NIFTY fallbacks queried


def test_gift_feed_uses_genuine_quote(monkeypatch):
    monkeypatch.setitem(sys.modules, "yf_compat",
                        types.SimpleNamespace(download=lambda t, **kw: pd.DataFrame({"Close": [22710.5]})))
    import market_data_feeds as m
    assert m.GIFTNiftyFeed()._fetch_gift_nifty() == 22710.5
