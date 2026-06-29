from __future__ import annotations

from typing import Any

import pytest

from option_chain_providers import (
    fetch_dhan_option_chain,
    fetch_upstox_option_chain,
)


class _Response:
    def __init__(self, payload: dict, status: int = 200, request_id: str = "req-1"):
        self._payload = payload
        self.status_code = status
        self.headers = {"x-request-id": request_id}

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])

    def get(self, *args: Any, **kwargs: Any) -> _Response:
        return self.get_responses.pop(0)

    def post(self, *args: Any, **kwargs: Any) -> _Response:
        return self.post_responses.pop(0)


def test_upstox_chain_is_normalized_with_provenance():
    contracts = _Response({"data": [{"expiry": "2099-06-30"}]})
    chain = _Response(
        {
            "data": [
                {
                    "expiry": "2099-06-30",
                    "strike_price": 25000,
                    "underlying_spot_price": 25010,
                    "call_options": {
                        "market_data": {
                            "ltp": 100,
                            "oi": 1200,
                            "prev_oi": 1000,
                            "volume": 500,
                            "bid_price": 99.5,
                            "ask_price": 100.5,
                        },
                        "option_greeks": {"iv": 12, "delta": 0.5},
                    },
                    "put_options": {
                        "market_data": {"ltp": 90, "oi": 900, "prev_oi": 800},
                        "option_greeks": {"iv": 13, "delta": -0.5},
                    },
                }
            ]
        },
        request_id="up-42",
    )
    out = fetch_upstox_option_chain(
        "NIFTY", token="token", session=_Session(get_responses=[contracts, chain])
    )
    assert out is not None
    assert out["_provider_source"] == "upstox_live"
    assert out["_provider_request_id"] == "upstox:up-42"
    row = out["records"]["data"][0]
    assert row["CE"]["changeinOpenInterest"] == 200
    assert row["CE"]["bidprice"] == 99.5


def test_dhan_chain_is_normalized_with_provenance():
    expiries = _Response({"data": ["2099-06-30"]})
    chain = _Response(
        {
            "data": {
                "last_price": 25010,
                "oc": {
                    "25000.000000": {
                        "ce": {
                            "last_price": 100,
                            "oi": 1200,
                            "previous_oi": 1000,
                            "volume": 500,
                            "top_bid_price": 99.5,
                            "greeks": {"delta": 0.5},
                        },
                        "pe": {"last_price": 90, "oi": 900, "previous_oi": 800},
                    }
                },
            }
        },
        request_id="dh-42",
    )
    out = fetch_dhan_option_chain(
        "NIFTY",
        client_id="client",
        token="token",
        session=_Session(post_responses=[expiries, chain]),
    )
    assert out is not None
    assert out["_provider_source"] == "dhan_live"
    assert out["_provider_request_id"] == "dhan:dh-42"
    assert out["records"]["data"][0]["CE"]["changeinOpenInterest"] == 200


def test_smartapi_tick_price_is_scaled_and_callback_receives_tick():
    from websocket_engine import WebSocketEngine

    engine = WebSocketEngine()
    engine._token_symbol_map["123"] = "NIFTY"
    received = []
    engine.register_tick_callback(lambda symbol, ltp, tick: received.append((symbol, ltp, tick)))
    engine._process_json_tick(
        {"token": "123", "last_traded_price": 2501234, "last_traded_quantity": 5}
    )
    assert engine.get_ltp("NIFTY") == pytest.approx(25012.34)
    assert received[0][1] == pytest.approx(25012.34)


def test_websocket_connected_only_after_open():
    from websocket_engine import WebSocketEngine

    engine = WebSocketEngine()
    engine._running = True
    engine._ws = object()
    assert engine.is_connected() is False
    engine._on_open(None)
    assert engine.is_connected() is True
    engine._on_close(None)
    assert engine.is_connected() is False


def test_websocket_reconnect_preserves_subscription_exchange(monkeypatch):
    from websocket_engine import WebSocketEngine

    engine = WebSocketEngine()
    engine._subscribed_tokens.update({"nse-token", "nfo-token"})
    engine._token_exchange_map.update({"nse-token": "NSE", "nfo-token": "NFO"})
    calls = []
    monkeypatch.setattr(
        engine, "_do_subscribe", lambda tokens, exchange="NFO": calls.append((tokens, exchange))
    )
    engine._on_open(None)
    assert sorted((tuple(tokens), exchange) for tokens, exchange in calls) == [
        (("nfo-token",), "NFO"),
        (("nse-token",), "NSE"),
    ]


def test_stale_resilience_payload_is_not_promoted(monkeypatch):
    import data_source_resilience
    import option_chain_fetcher

    stale = {
        "records": {
            "data": [{"strikePrice": 25000, "expiryDate": "30-Jun-2099"}],
            "expiryDates": ["30-Jun-2099"],
            "underlyingValue": 25000,
        },
        "_provider_source": "resilience_cache",
        "_provider_is_live": False,
    }
    monkeypatch.setattr(
        option_chain_fetcher.NSEOptionChainFetcher, "_market_open", staticmethod(lambda: True)
    )
    monkeypatch.setattr(data_source_resilience, "fetch_option_chain", lambda *a, **k: stale)
    monkeypatch.setattr(
        option_chain_fetcher.NSEOptionChainFetcher, "_fetch_live", lambda self: None
    )
    monkeypatch.setattr(
        option_chain_fetcher.NSEOptionChainFetcher, "_fetch_from_angel", lambda self: None
    )
    monkeypatch.setattr(
        option_chain_fetcher.NSEOptionChainFetcher, "_load_cache", lambda self, **k: None
    )
    monkeypatch.setattr("sensibull_client.fetch_option_chain", lambda *a, **k: None)
    fetcher = option_chain_fetcher.NSEOptionChainFetcher("NIFTY")
    assert fetcher.fetch() is None
    assert fetcher.last_source is None


def test_tick_flow_features_persist_for_forward_learning(tmp_path, monkeypatch):
    import sqlite3

    import trading_calendar
    from signal_log import SignalLogger

    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda *_a, **_k: True)
    db = tmp_path / "signals.db"
    logger = SignalLogger(str(db))
    row_id = logger.log_candidate(
        {
            "symbol": "TEST",
            "side": "BUY",
            "entry_price": 100,
            "stop_loss": 99,
            "target": 102,
            "strategy": "tick_test",
            "metadata": {
                "tick_order_flow": {
                    "oim": 0.4,
                    "velocity": 2.5,
                    "momentum": 0.1,
                    "total": 20,
                }
            },
        }
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT tick_oim,tick_velocity,tick_momentum,tick_sample_count,"
            "tick_flow_available FROM signal_log WHERE id=?",
            (row_id,),
        ).fetchone()
    assert row == (0.4, 2.5, 0.1, 20, 1)
