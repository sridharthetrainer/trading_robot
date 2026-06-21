"""
dhan_client.py — Dhan API client (free, permanent token, no daily login)

Dhan provides:
  - NSE/BSE intraday candles (1m, 5m, 15m, 1h, 1d)
  - Live market data via WebSocket
  - Order placement
  - Permanent access token (never expires)

Setup (5 minutes):
  1. Login to dhan.co
  2. Go to: My Profile → API Access
  3. Copy Client Code and Access Token
  4. Add to .env:
       DHAN_CLIENT_CODE=your_client_code
       DHAN_TOKEN_ID=your_access_token

Cost: FREE (just needs a Dhan demat account)
"""
from __future__ import annotations
import logging, os
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

_CLIENT  = os.getenv("DHAN_CLIENT_CODE", "")
_TOKEN   = os.getenv("DHAN_TOKEN_ID", "")
_BASE    = "https://api.dhan.co"

# Dhan security IDs for key NSE indices
INDEX_SECURITY_IDS = {
    "NIFTY":      "13",
    "BANKNIFTY":  "25",
    "FINNIFTY":   "27",
    "MIDCPNIFTY": "442",
    "SENSEX":     "51",
    "BANKEX":     "319",
}

INTERVAL_MAP = {
    "1m":  "1",
    "5m":  "5",
    "15m": "15",
    "25m": "25",
    "1h":  "60",
    "1d":  "D",
}

EXCHANGE_MAP = {
    "NIFTY": "IDX_I", "BANKNIFTY": "IDX_I",
    "FINNIFTY": "IDX_I", "MIDCPNIFTY": "IDX_I",
    "SENSEX": "IDX_I", "BANKEX": "IDX_I",
}


def is_configured() -> bool:
    """True if Dhan credentials are in .env."""
    return bool(_CLIENT and _TOKEN)


def get_headers() -> dict:
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
    Fetch intraday/EOD historical candles from Dhan.
    Permanent token — never needs refresh.
    Works for indices out of the box.
    For stocks: needs security_id lookup from Dhan master.
    """
    if not is_configured():
        return None

    try:
        import requests
        sym_up    = symbol.upper()
        exch_seg  = EXCHANGE_MAP.get(sym_up, "NSE_EQ")
        sec_id    = INDEX_SECURITY_IDS.get(sym_up, "")
        dhan_iv   = INTERVAL_MAP.get(interval, "5")

        if not sec_id:
            # Try loading from dhan_master.json (built by download_dhan_master)
            sec_id = _lookup_dhan_security_id(sym_up)
            if not sec_id:
                logger.debug("Dhan: no security_id for %s", symbol)
                return None

        now   = datetime.now()
        start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end   = now.strftime("%Y-%m-%d")

        if interval == "1d" or dhan_iv == "D":
            # EOD endpoint
            r = requests.post(
                f"{_BASE}/charts/historical",
                headers=get_headers(),
                json={
                    "securityId":    sec_id,
                    "exchangeSegment": exch_seg,
                    "instrument":    "INDEX" if exch_seg == "IDX_I" else "EQUITY",
                    "expiryCode":    0,
                    "fromDate":      start,
                    "toDate":        end,
                },
                timeout=12,
            )
        else:
            # Intraday endpoint
            r = requests.post(
                f"{_BASE}/charts/intraday",
                headers=get_headers(),
                json={
                    "securityId":    sec_id,
                    "exchangeSegment": exch_seg,
                    "instrument":    "INDEX" if exch_seg == "IDX_I" else "EQUITY",
                    "interval":      dhan_iv,
                    "fromDate":      start,
                    "toDate":        end,
                },
                timeout=12,
            )

        if r.status_code != 200:
            logger.debug("Dhan HTTP %d for %s", r.status_code, symbol)
            return None

        d = r.json()
        opens   = d.get("open", [])
        if not opens:
            return None

        timestamps = d.get("timestamp", [])
        df = pd.DataFrame({
            "open":   opens,
            "high":   d.get("high",   opens),
            "low":    d.get("low",    opens),
            "close":  d.get("close",  opens),
            "volume": d.get("volume", [0]*len(opens)),
        })

        if timestamps:
            # Dhan returns epoch milliseconds
            df.index = pd.DatetimeIndex([
                pd.Timestamp(ts/1000, unit="s", tz="Asia/Kolkata").tz_localize(None)
                for ts in timestamps
            ])
        else:
            df.index = pd.date_range(end=now, periods=len(df), freq="5min")

        df.columns = [c.lower() for c in df.columns]
        logger.info("Dhan ✅ %s: %d bars", symbol, len(df))
        return df

    except Exception as e:
        logger.debug("Dhan fetch %s: %s", symbol, e)
        return None


def _lookup_dhan_security_id(symbol: str) -> str:
    """Look up Dhan security_id from cached master file."""
    try:
        import json
        from pathlib import Path
        master_file = Path("dhan_master.json")
        if master_file.exists():
            master = json.loads(master_file.read_text())
            return str(master.get(symbol.upper(), ""))
    except Exception:
        pass
    return ""


def download_dhan_master() -> bool:
    """Download and cache Dhan security master (symbol → security_id)."""
    if not is_configured():
        logger.warning("Dhan not configured — set DHAN_CLIENT_CODE + DHAN_TOKEN_ID")
        return False
    try:
        import requests, json
        from pathlib import Path
        r = requests.get(
            f"{_BASE}/instruments",
            headers=get_headers(),
            timeout=30,
        )
        if r.status_code == 200:
            instruments = r.json()
            master = {}
            for inst in instruments:
                sym = str(inst.get("tradingSymbol", "")).upper()
                sid = str(inst.get("securityId", ""))
                if sym and sid:
                    master[sym] = sid
            Path("dhan_master.json").write_text(json.dumps(master))
            logger.info("Dhan master: %d instruments saved", len(master))
            return True
    except Exception as e:
        logger.error("Dhan master download: %s", e)
    return False
