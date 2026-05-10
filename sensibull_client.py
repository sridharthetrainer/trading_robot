"""
sensibull_client.py — Sensibull option chain (NSE fallback)

Sensibull aggregates NSE data and is almost never blocked.
No auth needed. Used as fallback when NSE option chain returns 401/429.
"""
from __future__ import annotations
import logging, time
from typing import Optional, Dict

logger = logging.getLogger(__name__)
_CACHE: Dict[str, dict] = {}


def fetch_option_chain(symbol: str = "NIFTY") -> Optional[dict]:
    """
    Fetch option chain from Sensibull (unofficial but reliable).
    Returns NSE-compatible format with records.data structure.
    Skip silently if unavailable.
    """
    cache_key = f"sensibull_{symbol}"
    cached = _CACHE.get(cache_key, {})
    if cached and time.time() - cached.get("ts", 0) < 180:
        return cached["v"]

    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer":    "https://sensibull.com/",
            "Accept":     "application/json",
        }
        # Sensibull public option chain endpoint
        sym_map = {
            "NIFTY":     "NIFTY",
            "BANKNIFTY": "BANKNIFTY",
            "FINNIFTY":  "FINNIFTY",
            "MIDCPNIFTY":"MIDCPNIFTY",
        }
        sym = sym_map.get(symbol.upper(), symbol.upper())
        r = requests.get(
            f"https://oxide.sensibull.com/v1/compute/cache/"
            f"option_chain_with_greeks/{sym}",
            headers=headers, timeout=10,
        )
        if r.status_code == 200:
            raw = r.json()
            # Convert Sensibull format to NSE-compatible format
            data = _convert_to_nse_format(raw, symbol)
            if data:
                _CACHE[cache_key] = {"v": data, "ts": time.time()}
                logger.info("Sensibull OC OK: %s", symbol)
                return data
    except Exception as e:
        logger.debug("Sensibull %s: %s", symbol, e)

    # Fallback: opstra.definedge.com (also reliable)
    try:
        import requests
        r = requests.get(
            f"https://opstra.definedge.com/api/openoi/optionchaindata"
            f"?symbol={symbol.upper()}&expiry=current",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://opstra.definedge.com"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                _CACHE[cache_key] = {"v": data, "ts": time.time()}
                logger.info("Opstra OC OK: %s", symbol)
                return data
    except Exception as e:
        logger.debug("Opstra %s: %s", symbol, e)

    return None


def _convert_to_nse_format(raw: dict, symbol: str) -> Optional[dict]:
    """Convert Sensibull response to NSE option chain format."""
    try:
        strikes = raw.get("data", {}).get("strikePrices", [])
        if not strikes:
            return None

        records_data = []
        for strike_data in strikes:
            strike = strike_data.get("strikePrice", 0)
            ce = strike_data.get("CE", {})
            pe = strike_data.get("PE", {})

            row: dict = {"strikePrice": strike}
            if ce:
                row["CE"] = {
                    "openInterest":        int(ce.get("openInterest", 0)),
                    "changeinOpenInterest": int(ce.get("changeInOI", 0)),
                    "lastPrice":           float(ce.get("lastPrice", 0)),
                    "impliedVolatility":   float(ce.get("iv", 0)),
                    "delta":               float(ce.get("delta", 0)),
                    "theta":               float(ce.get("theta", 0)),
                }
            if pe:
                row["PE"] = {
                    "openInterest":        int(pe.get("openInterest", 0)),
                    "changeinOpenInterest": int(pe.get("changeInOI", 0)),
                    "lastPrice":           float(pe.get("lastPrice", 0)),
                    "impliedVolatility":   float(pe.get("iv", 0)),
                    "delta":               float(pe.get("delta", 0)),
                    "theta":               float(pe.get("theta", 0)),
                }
            records_data.append(row)

        ul = raw.get("data", {}).get("underlyingValue", 0)
        return {
            "records": {
                "data":            records_data,
                "underlyingValue": ul,
                "timestamp":       raw.get("data", {}).get("timestamp", ""),
            }
        }
    except Exception as e:
        logger.debug("Sensibull convert: %s", e)
    return None


def get_pcr(symbol: str = "NIFTY") -> float:
    """PCR from Sensibull option chain."""
    try:
        oc = fetch_option_chain(symbol)
        if not oc:
            return 0.0
        records = oc.get("records", {}).get("data", [])
        pe_oi = sum(r.get("PE", {}).get("openInterest", 0) for r in records)
        ce_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in records)
        return round(pe_oi / ce_oi, 3) if ce_oi > 0 else 0.0
    except Exception:
        return 0.0
