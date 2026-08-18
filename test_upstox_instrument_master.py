"""Regression tests for upstox_data.py's instrument-key resolution.

Two real bugs found and fixed via a system audit (2026-08-18):

1. _get_instrument_key() was a silent no-op for EVERY stock (not just the 5
   a data-quality watchdog happened to flag) -- it searched local files that
   either don't exist (scrip_master.json, angel_scrip_master.json) or exist
   but have no "isin" field (OpenAPIScripMaster.json is actually an NSE
   bond/debenture master, not an equity one). Root-caused via 5 real symbols
   (HPCL, HEROMOTOCO, ICICIPRULI, OIL, SHREECEM) stuck at their last-Angel-
   covered date for 10-37+ sessions with zero working fallback, because the
   fallback itself never worked. Fixed by using Upstox's own public,
   no-auth complete instrument master instead of deriving an ISIN.

2. The first version of that fix picked whichever of NSE_EQ/BSE_EQ appeared
   first in the raw file for a given symbol -- verified this does NOT
   reliably favor NSE_EQ (RELIANCE, OIL, SHREECEM all resolved to BSE_EQ
   under a naive single-pass "first one wins" loop), which conflicts with
   this system's documented NSE-primary architecture. Fixed with an explicit
   two-pass scan that always prefers NSE_EQ.

Uses synthetic gzip'd JSON (mocking requests.get) -- no real network call,
and does not touch the real upstox_instrument_master_cache.json used by the
live system.
"""
import gzip
import json

import upstox_data as ud


def _fake_response(items):
    class _Resp:
        def __init__(self, content):
            self.content = content
        def raise_for_status(self):
            pass
    return _Resp(gzip.compress(json.dumps(items).encode()))


def _reset_module_state(monkeypatch, tmp_path):
    cache_file = str(tmp_path / "upstox_master_test_cache.json")
    monkeypatch.setattr(ud, "_UPSTOX_MASTER_CACHE_FILE", cache_file)
    monkeypatch.setattr(ud, "_UPSTOX_MASTER_LOADED", False)
    monkeypatch.setattr(ud, "_ISIN_CACHE", {})
    return cache_file


def test_resolves_a_plain_nse_stock(tmp_path, monkeypatch):
    _reset_module_state(monkeypatch, tmp_path)
    items = [{"segment": "NSE_EQ", "trading_symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029"}]
    monkeypatch.setattr(ud.requests, "get", lambda *a, **k: _fake_response(items))

    assert ud._get_instrument_key("TCS") == "NSE_EQ|INE467B01029"


def test_hpcl_alias_resolves_via_hindpetro(tmp_path, monkeypatch):
    _reset_module_state(monkeypatch, tmp_path)
    items = [{"segment": "NSE_EQ", "trading_symbol": "HINDPETRO", "instrument_key": "NSE_EQ|INE094A01015"}]
    monkeypatch.setattr(ud.requests, "get", lambda *a, **k: _fake_response(items))

    assert ud._get_instrument_key("HPCL") == "NSE_EQ|INE094A01015"


def test_nse_eq_preferred_over_bse_eq_regardless_of_file_order(tmp_path, monkeypatch):
    """The exact bug found this session: BSE_EQ listed BEFORE NSE_EQ in the
    raw file must still resolve to NSE_EQ."""
    _reset_module_state(monkeypatch, tmp_path)
    items = [
        {"segment": "BSE_EQ", "trading_symbol": "RELIANCE", "instrument_key": "BSE_EQ|WRONG"},
        {"segment": "NSE_EQ", "trading_symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018"},
    ]
    monkeypatch.setattr(ud.requests, "get", lambda *a, **k: _fake_response(items))

    assert ud._get_instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"


def test_falls_back_to_bse_eq_when_no_nse_listing_exists(tmp_path, monkeypatch):
    _reset_module_state(monkeypatch, tmp_path)
    items = [{"segment": "BSE_EQ", "trading_symbol": "BSEONLY", "instrument_key": "BSE_EQ|SOMEKEY"}]
    monkeypatch.setattr(ud.requests, "get", lambda *a, **k: _fake_response(items))

    assert ud._get_instrument_key("BSEONLY") == "BSE_EQ|SOMEKEY"


def test_unknown_symbol_returns_none(tmp_path, monkeypatch):
    _reset_module_state(monkeypatch, tmp_path)
    items = [{"segment": "NSE_EQ", "trading_symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029"}]
    monkeypatch.setattr(ud.requests, "get", lambda *a, **k: _fake_response(items))

    assert ud._get_instrument_key("NOT_A_REAL_SYMBOL") is None


def test_index_map_bypasses_the_master_lookup_entirely(tmp_path, monkeypatch):
    _reset_module_state(monkeypatch, tmp_path)
    def _boom(*a, **k):
        raise AssertionError("should not fetch the master for an index symbol")
    monkeypatch.setattr(ud.requests, "get", _boom)

    assert ud._get_instrument_key("NIFTY") == "NSE_INDEX|Nifty 50"


def test_disk_cache_avoids_a_second_network_call(tmp_path, monkeypatch):
    cache_file = _reset_module_state(monkeypatch, tmp_path)
    items = [{"segment": "NSE_EQ", "trading_symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029"}]
    calls = []
    def _get(*a, **k):
        calls.append(1)
        return _fake_response(items)
    monkeypatch.setattr(ud.requests, "get", _get)

    ud._get_instrument_key("TCS")
    assert len(calls) == 1

    # Fresh module-level state (simulating a new process), same cache file on disk.
    monkeypatch.setattr(ud, "_UPSTOX_MASTER_LOADED", False)
    monkeypatch.setattr(ud, "_ISIN_CACHE", {})
    ud._get_instrument_key("TCS")
    assert len(calls) == 1, "should have used the disk cache, not re-fetched"
