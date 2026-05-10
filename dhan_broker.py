"""
dhan_broker.py — Dhan API broker implementing BrokerInterface

Dhan is FREE with a demat account. Token NEVER expires (permanent static token).
This makes it a perfect always-on fallback for Angel One.

Setup (one-time, 5 minutes):
  1. Login to dhan.co
  2. Go to My Profile → API Access  
  3. Copy Client Code and Access Token
  4. Add to .env:
       DHAN_CLIENT_CODE=your_code
       DHAN_TOKEN_ID=your_token

No daily login. No TOTP. No browser. Pure fire-and-forget.
"""
from __future__ import annotations
import logging, os, time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)

_CLIENT = os.getenv("DHAN_CLIENT_CODE", "")
_TOKEN  = os.getenv("DHAN_TOKEN_ID", "")

# Dhan security IDs for indices (permanent — never change)
DHAN_INDEX_IDS = {
    "NIFTY":      "13",
    "BANKNIFTY":  "25",
    "FINNIFTY":   "27",
    "MIDCPNIFTY": "41502",
    "SENSEX":     "51",
    "BANKEX":     "54",
}

# Dhan exchange segments
SEGMENT_MAP = {
    "NSE": "NSE_EQ",
    "NFO": "NSE_FNO",
    "BSE": "BSE_EQ",
    "BFO": "BSE_FNO",
    "IDX": "IDX_I",
}


def is_configured() -> bool:
    """Returns True if Dhan credentials are in .env."""
    return bool(_CLIENT and _TOKEN and _CLIENT != "your_code")


def get_headers() -> dict:
    """Standard Dhan API headers."""
    return {
        "access-token": _TOKEN,
        "client-id":    _CLIENT,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


def get_historical_data(
    symbol:   str,
    interval: str = "5m",
    days:     int = 5,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV from Dhan API.
    Works for: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY (indices)
    Interval: "1m", "5m", "15m", "1d"
    """
    if not is_configured():
        return None

    try:
        import requests
        from datetime import date

        interval_map = {
            "1m": "1",  "3m": "3",  "5m": "5",
            "15m": "15", "30m": "30", "1h": "60",
            "1d": "D",  "daily": "D",
        }
        dhan_interval = interval_map.get(interval, "5")

        sym_upper  = symbol.upper()
        security_id = DHAN_INDEX_IDS.get(sym_upper, "")
        exchange_seg = "IDX_I"

        # For stocks, try to look up security ID
        if not security_id:
            security_id = _lookup_dhan_security(sym_upper)
            exchange_seg = "NSE_EQ"

        if not security_id:
            logger.debug("Dhan: no security ID for %s", symbol)
            return None

        end_date   = date.today()
        start_date = end_date - timedelta(days=max(days, 5))

        if dhan_interval == "D":
            url  = "https://api.dhan.co/charts/historical"
            body = {
                "securityId":    security_id,
                "exchangeSegment": exchange_seg,
                "instrument":    "INDEX" if exchange_seg == "IDX_I" else "EQUITY",
                "expiryCode":    0,
                "fromDate":      str(start_date),
                "toDate":        str(end_date),
            }
        else:
            url  = "https://api.dhan.co/charts/intraday"
            body = {
                "securityId":    security_id,
                "exchangeSegment": exchange_seg,
                "instrument":    "INDEX" if exchange_seg == "IDX_I" else "EQUITY",
                "interval":      dhan_interval,
                "fromDate":      str(start_date),
                "toDate":        str(end_date),
            }

        r = requests.post(url, headers=get_headers(), json=body, timeout=12)

        if r.status_code != 200:
            logger.debug("Dhan %s HTTP %d", symbol, r.status_code)
            return None

        d = r.json()
        opens = d.get("open", [])
        if not opens:
            return None

        timestamps = d.get("timestamp", [])
        df = pd.DataFrame({
            "open":   [float(x) for x in d.get("open",   opens)],
            "high":   [float(x) for x in d.get("high",   opens)],
            "low":    [float(x) for x in d.get("low",    opens)],
            "close":  [float(x) for x in d.get("close",  opens)],
            "volume": [int(x)   for x in d.get("volume", [0]*len(opens))],
        })

        if timestamps:
            df.index = pd.to_datetime(
                [ts * 1000 if ts < 1e12 else ts for ts in timestamps],
                unit="ms", utc=True,
            ).tz_convert("Asia/Kolkata").tz_localize(None)
        else:
            df.index = pd.date_range(
                end=datetime.now(), periods=len(df), freq="5min"
            )

        df = df[df["close"] > 0]
        logger.info("Dhan OK: %s %d bars", symbol, len(df))
        return df if len(df) >= 5 else None

    except Exception as e:
        logger.debug("Dhan historical %s: %s", symbol, e)
        return None


def get_ltp(symbol: str) -> float:
    """Get last traded price from Dhan."""
    if not is_configured():
        return 0.0
    try:
        import requests
        security_id  = DHAN_INDEX_IDS.get(symbol.upper(), "")
        exchange_seg = "IDX_I"
        if not security_id:
            security_id  = _lookup_dhan_security(symbol.upper())
            exchange_seg = "NSE_EQ"
        if not security_id:
            return 0.0
        r = requests.post(
            "https://api.dhan.co/marketfeed/ltp",
            headers=get_headers(),
            json={exchange_seg: [security_id]},
            timeout=6,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            for seg, items in data.items():
                for item in (items if isinstance(items, list) else [items]):
                    ltp = float(item.get("lastTradedPrice", 0) or 0)
                    if ltp > 0:
                        return ltp
    except Exception as e:
        logger.debug("Dhan LTP %s: %s", symbol, e)
    return 0.0


def place_order(
    symbol:     str,
    qty:        int,
    side:       str,
    order_type: str = "MARKET",
    price:      float = 0.0,
    product:    str = "INTRADAY",
) -> Optional[str]:
    """Place order via Dhan API."""
    if not is_configured():
        return None
    try:
        import requests
        security_id  = _lookup_dhan_security(symbol.upper())
        if not security_id:
            return None

        body = {
            "dhanClientId":    _CLIENT,
            "transactionType": "BUY" if side.upper() == "BUY" else "SELL",
            "exchangeSegment": "NSE_EQ",
            "productType":     product,
            "orderType":       order_type,
            "validity":        "DAY",
            "tradingSymbol":   symbol.upper(),
            "securityId":      security_id,
            "quantity":        qty,
            "price":           price if order_type == "LIMIT" else 0,
        }
        r = requests.post(
            "https://api.dhan.co/orders",
            headers=get_headers(), json=body, timeout=10,
        )
        if r.status_code == 200:
            order_id = r.json().get("orderId", "")
            logger.info("Dhan order placed: %s %s %d", side, symbol, qty)
            return str(order_id)
    except Exception as e:
        logger.error("Dhan place_order %s: %s", symbol, e)
    return None


def get_balance() -> float:
    """Get available balance from Dhan."""
    if not is_configured():
        return 0.0
    try:
        import requests
        r = requests.get(
            "https://api.dhan.co/fundlimit",
            headers=get_headers(), timeout=8,
        )
        if r.status_code == 200:
            d = r.json()
            for key in ["availabelBalance", "availableBalance",
                        "net", "cashAvailable"]:
                v = d.get(key)
                if v is not None:
                    return float(v)
    except Exception as e:
        logger.debug("Dhan balance: %s", e)
    return 0.0


def _lookup_dhan_security(symbol: str) -> str:
    """Look up Dhan security ID from master contract."""
    try:
        from pathlib import Path
        mc = Path("dhan_master.csv")
        if not mc.exists():
            return ""
        df = pd.read_csv(str(mc), low_memory=False,
                         usecols=lambda c: c in ["SEM_TRADING_SYMBOL",
                                                   "SEM_SMST_SECURITY_ID",
                                                   "SEM_EXM_EXCH_ID"])
        rows = df[df["SEM_TRADING_SYMBOL"].str.upper() == symbol]
        if not rows.empty:
            return str(rows.iloc[0]["SEM_SMST_SECURITY_ID"])
    except Exception:
        pass
    return ""


def is_connected() -> bool:
    """Returns True if Dhan credentials are valid."""
    if not is_configured():
        return False
    return get_balance() > 0 or get_ltp("NIFTY") > 0
