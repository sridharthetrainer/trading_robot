"""Authenticated, provenance-preserving option-chain providers.

Every adapter returns the NSE-like schema consumed by the existing option
analytics. Provider metadata is carried with the payload so cached or failed
responses cannot be promoted to verified live evidence by a wrapper.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from typing import Any, Dict, Optional

import requests


UPSTOX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|Nifty Midcap Select",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX",
}

DHAN_UNDERLYINGS = {
    "NIFTY": (13, "IDX_I"),
    "BANKNIFTY": (25, "IDX_I"),
    "FINNIFTY": (27, "IDX_I"),
    "MIDCPNIFTY": (442, "IDX_I"),
    "SENSEX": (51, "IDX_I"),
    "BANKEX": (319, "IDX_I"),
}


def _request_id(response: requests.Response, provider: str) -> str:
    for key in ("x-request-id", "x-correlation-id", "request-id", "cf-ray"):
        value = response.headers.get(key)
        if value:
            return f"{provider}:{value}"
    return f"{provider}:local:{uuid.uuid4().hex}"


def mark_provider(
    payload: Dict[str, Any],
    source: str,
    *,
    is_live: bool,
    request_id: str = "",
) -> Dict[str, Any]:
    if is_live and not request_id:
        request_id = f"{source}:local:{uuid.uuid4().hex}"
    payload["_provider_source"] = str(source)
    payload["_provider_is_live"] = bool(is_live)
    payload["_provider_request_id"] = str(request_id)
    payload["_provider_fetched_at"] = datetime.now().astimezone().isoformat()
    return payload


def _nearest_expiry(values: list[str]) -> str:
    parsed = []
    for value in values:
        try:
            expiry = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if expiry >= date.today():
            parsed.append((expiry, str(value)[:10]))
    return min(parsed)[1] if parsed else ""


def _nse_side(market: Dict[str, Any], greeks: Dict[str, Any]) -> Dict[str, Any]:
    oi = float(market.get("oi", 0) or 0)
    previous_oi = float(market.get("prev_oi", market.get("previous_oi", 0)) or 0)
    return {
        "openInterest": oi,
        "changeinOpenInterest": oi - previous_oi,
        "totalTradedVolume": float(market.get("volume", 0) or 0),
        "lastPrice": float(market.get("ltp", market.get("last_price", 0)) or 0),
        "impliedVolatility": float(
            greeks.get("iv", market.get("implied_volatility", 0)) or 0
        ),
        "bidprice": float(market.get("bid_price", market.get("top_bid_price", 0)) or 0),
        "bidQty": float(market.get("bid_qty", market.get("top_bid_quantity", 0)) or 0),
        "askPrice": float(market.get("ask_price", market.get("top_ask_price", 0)) or 0),
        "askQty": float(market.get("ask_qty", market.get("top_ask_quantity", 0)) or 0),
        "delta": float(greeks.get("delta", 0) or 0),
        "gamma": float(greeks.get("gamma", 0) or 0),
        "theta": float(greeks.get("theta", 0) or 0),
        "vega": float(greeks.get("vega", 0) or 0),
    }


def fetch_upstox_option_chain(
    underlying: str,
    *,
    token: str = "",
    timeout: int = 12,
    session: Optional[requests.Session] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch Upstox option chain using a standard or read-only analytics token."""
    token = token or os.getenv("UPSTOX_ANALYTICS_TOKEN", "") or os.getenv(
        "UPSTOX_ACCESS_TOKEN", ""
    )
    key = UPSTOX_KEYS.get(str(underlying).upper())
    if not token or not key:
        return None
    http = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    contracts = http.get(
        "https://api.upstox.com/v2/option/contract",
        params={"instrument_key": key},
        headers=headers,
        timeout=timeout,
    )
    if contracts.status_code != 200:
        return None
    contract_rows = contracts.json().get("data", []) or []
    expiry = _nearest_expiry([row.get("expiry", "") for row in contract_rows])
    if not expiry:
        return None

    response = http.get(
        "https://api.upstox.com/v2/option/chain",
        params={"instrument_key": key, "expiry_date": expiry},
        headers=headers,
        timeout=timeout,
    )
    if response.status_code != 200:
        return None
    raw_rows = response.json().get("data", []) or []
    rows = []
    spot = 0.0
    for row in raw_rows:
        spot = float(row.get("underlying_spot_price", spot) or spot)
        ce = row.get("call_options", {}) or {}
        pe = row.get("put_options", {}) or {}
        rows.append(
            {
                "strikePrice": float(row.get("strike_price", 0) or 0),
                "expiryDate": datetime.strptime(expiry, "%Y-%m-%d").strftime("%d-%b-%Y"),
                "CE": _nse_side(ce.get("market_data", {}) or {}, ce.get("option_greeks", {}) or {}),
                "PE": _nse_side(pe.get("market_data", {}) or {}, pe.get("option_greeks", {}) or {}),
            }
        )
    rows = [row for row in rows if row["strikePrice"] > 0]
    if not rows or spot <= 0:
        return None
    expiry_nse = rows[0]["expiryDate"]
    payload = {
        "records": {"data": rows, "expiryDates": [expiry_nse], "underlyingValue": spot},
        "filtered": {"data": rows},
    }
    return mark_provider(
        payload,
        "upstox_live",
        is_live=True,
        request_id=_request_id(response, "upstox"),
    )


def fetch_dhan_option_chain(
    underlying: str,
    *,
    client_id: str = "",
    token: str = "",
    timeout: int = 12,
    session: Optional[requests.Session] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch Dhan option chain and normalize it to the shared schema."""
    client_id = client_id or os.getenv("DHAN_CLIENT_CODE", "")
    token = token or os.getenv("DHAN_TOKEN_ID", "")
    instrument = DHAN_UNDERLYINGS.get(str(underlying).upper())
    if not client_id or not token or not instrument:
        return None
    security_id, segment = instrument
    http = session or requests.Session()
    headers = {
        "access-token": token,
        "client-id": client_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"UnderlyingScrip": security_id, "UnderlyingSeg": segment}
    expiries = http.post(
        "https://api.dhan.co/v2/optionchain/expirylist",
        headers=headers,
        json=body,
        timeout=timeout,
    )
    if expiries.status_code != 200:
        return None
    expiry = _nearest_expiry(expiries.json().get("data", []) or [])
    if not expiry:
        return None
    response = http.post(
        "https://api.dhan.co/v2/optionchain",
        headers=headers,
        json={**body, "Expiry": expiry},
        timeout=timeout,
    )
    if response.status_code != 200:
        return None
    data = response.json().get("data", {}) or {}
    expiry_nse = datetime.strptime(expiry, "%Y-%m-%d").strftime("%d-%b-%Y")
    rows = []
    for strike, sides in (data.get("oc", {}) or {}).items():
        ce = sides.get("ce", {}) or {}
        pe = sides.get("pe", {}) or {}
        rows.append(
            {
                "strikePrice": float(strike),
                "expiryDate": expiry_nse,
                "CE": _nse_side(ce, ce.get("greeks", {}) or {}),
                "PE": _nse_side(pe, pe.get("greeks", {}) or {}),
            }
        )
    spot = float(data.get("last_price", 0) or 0)
    if not rows or spot <= 0:
        return None
    payload = {
        "records": {"data": rows, "expiryDates": [expiry_nse], "underlyingValue": spot},
        "filtered": {"data": rows},
    }
    return mark_provider(
        payload,
        "dhan_live",
        is_live=True,
        request_id=_request_id(response, "dhan"),
    )


def fetch_authenticated_option_chain(underlying: str) -> Optional[Dict[str, Any]]:
    """Try configured low-cost authenticated sources in explicit priority order."""
    order = os.getenv("OPTION_CHAIN_PROVIDER_ORDER", "upstox,dhan").split(",")
    for provider in (name.strip().lower() for name in order):
        try:
            if provider == "upstox":
                result = fetch_upstox_option_chain(underlying)
            elif provider == "dhan":
                result = fetch_dhan_option_chain(underlying)
            else:
                continue
            if result:
                return result
        except (requests.RequestException, ValueError, TypeError):
            continue
    return None
