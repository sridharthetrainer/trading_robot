"""Angel option chains must carry the quote's EXCHANGE time (never fetch time),
so option_quality's timestamp requirement can judge their freshness."""
import sys
import types
from datetime import datetime, timedelta

import option_quality
from angel_option_chain import AngelOptionChainEngine


def test_exchange_time_parsing():
    f = AngelOptionChainEngine._exchange_time
    assert f({"exchFeedTime": "14-Oct-2026 15:29:59"}) == "2026-10-14T15:29:59"
    assert f({"exchTradeTime": "2026-10-14 15:29:58"}) == "2026-10-14T15:29:58"
    assert f({"exchFeedTime": 1791975599000}) is not None          # epoch milliseconds
    assert f({"ltp": 100}) is None                                    # absent -> None
    assert f({"exchFeedTime": "not a time"}) is None
    assert f(None) is None


def _install_fake_angel(monkeypatch, chain):
    class FakeEngine:
        def __init__(self, **kw):
            pass

        def scan_option_chain(self):
            return 25000.0, 25000.0, "2026-10-20", chain

    monkeypatch.setitem(sys.modules, "angel", types.SimpleNamespace(AngelOne=lambda **kw: object()))
    monkeypatch.setattr("angel_option_chain.AngelOptionChainEngine", FakeEngine)


def _row(strike, ce_t, pe_t):
    return {"strike": strike, "CE_LTP": 100, "PE_LTP": 90, "CE_OI": 1, "PE_OI": 1,
            "CE_exch_time": ce_t, "PE_exch_time": pe_t}


def test_fetcher_stamps_oldest_atm_exchange_time(monkeypatch):
    from option_chain_fetcher import NSEOptionChainFetcher
    now = datetime.now().replace(microsecond=0)
    old, new = (now - timedelta(seconds=40)).isoformat(), (now - timedelta(seconds=5)).isoformat()
    _install_fake_angel(monkeypatch, [_row(24950, None, None), _row(25000, new, old), _row(25050, None, None)])
    payload = NSEOptionChainFetcher("NIFTY")._fetch_from_angel()
    assert payload["records"]["timestamp"] == (now - timedelta(seconds=40)).strftime("%d-%b-%Y %H:%M:%S")
    age = option_quality.chain_age_sec(payload)
    assert age is not None and 35 <= age <= 120


def test_no_exchange_time_means_no_timestamp(monkeypatch):
    from option_chain_fetcher import NSEOptionChainFetcher
    _install_fake_angel(monkeypatch, [_row(25000, None, None)])
    payload = NSEOptionChainFetcher("NIFTY")._fetch_from_angel()
    assert "timestamp" not in payload["records"]
    assert option_quality.chain_age_sec(payload) is None             # still rejected downstream
