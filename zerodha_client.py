"""
zerodha_client.py — Zerodha Kite Connect integration (₹2,000/month)

WHEN TO USE:
  - Capital > ₹2,00,000 (one missed trade costs more than monthly fee)
  - Need sub-100ms execution latency
  - Need full L2 order book (5 levels bid/ask) for all stocks
  - Need 2000+ days historical data for ML training

LIMITATIONS vs ANGEL ONE:
  - Requires MANUAL daily token refresh (no TOTP equivalent)
  - Token expires at 6 AM IST daily
  - Must open browser → login → copy access_token → update .env
  - OR automate via Playwright (see auto_refresh_kite_token.py)

ADVANTAGES vs ANGEL ONE:
  - Best-in-class data quality (Zerodha processes 15% of NSE volume)
  - Full tick data (every trade, not just candles)
  - KiteTicker WebSocket: bid/ask depth + LTP + volume on same stream
  - Lower latency (~50ms vs ~150ms Angel)
  - 2000+ days historical daily data (Angel = 500 days)
  - Cleaner API — fewer rate limit issues

SETUP:
  1. Buy Kite Connect subscription at kite.trade (₹2000/month)
  2. Create app at developers.kite.trade
  3. Daily: login → get access_token → put in .env:
       ZERODHA_API_KEY=your_api_key
       ZERODHA_API_SECRET=your_api_secret
       ZERODHA_ACCESS_TOKEN=today_token
  4. OR: Use auto_refresh_kite_token.py (Playwright-based automation)

RECOMMENDATION: Keep Angel One as primary. Add Zerodha when capital > ₹2L.
"""
from __future__ import annotations
import logging, os
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

_API_KEY   = os.getenv("ZERODHA_API_KEY", "")
_API_SEC   = os.getenv("ZERODHA_API_SECRET", "")
_TOKEN     = os.getenv("ZERODHA_ACCESS_TOKEN", "")


def is_configured() -> bool:
    return bool(_API_KEY and _TOKEN)


def is_token_valid() -> bool:
    """Kite tokens expire at 6 AM IST daily."""
    token_date_str = os.getenv("ZERODHA_TOKEN_DATE", "")
    if not token_date_str:
        return False
    try:
        from datetime import date
        token_date = date.fromisoformat(token_date_str)
        return token_date >= date.today()
    except Exception:
        return False


def get_kite():
    """Get authenticated Kite instance."""
    if not is_configured():
        return None
    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=_API_KEY)
        kite.set_access_token(_TOKEN)
        return kite
    except ImportError:
        logger.debug("kiteconnect not installed: pip install kiteconnect")
        return None
    except Exception as e:
        logger.debug("Kite init: %s", e)
        return None


def get_historical_data(
    symbol:   str,
    interval: str = "5minute",
    days:     int = 5,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical candles from Zerodha Kite.
    interval: minute, 3minute, 5minute, 10minute, 15minute,
              30minute, 60minute, day, week, month
    """
    if not is_configured() or not is_token_valid():
        return None

    kite = get_kite()
    if not kite:
        return None

    try:
        # NSE instrument tokens (permanent — hardcoded for indices)
        INDEX_TOKENS = {
            "NIFTY":     256265,
            "BANKNIFTY": 260105,
            "FINNIFTY":  257801,
            "MIDCPNIFTY":288009,
            "SENSEX":    265,
        }
        token = INDEX_TOKENS.get(symbol.upper())
        if not token:
            # Look up from instruments file
            token = _lookup_kite_token(symbol, kite)
        if not token:
            return None

        now   = datetime.now()
        start = now - timedelta(days=days)
        iv_map = {
            "1m": "minute", "5m": "5minute", "15m": "15minute",
            "1h": "60minute", "1d": "day",
        }
        kite_interval = iv_map.get(interval, "5minute")

        records = kite.historical_data(
            instrument_token=token,
            from_date=start,
            to_date=now,
            interval=kite_interval,
            continuous=False,
            oi=True,
        )
        if not records:
            return None

        df = pd.DataFrame(records)
        df.columns = [c.lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        logger.info("Zerodha Kite ✅ %s: %d bars", symbol, len(df))
        return df

    except Exception as e:
        logger.debug("Kite historical %s: %s", symbol, e)
        return None


def place_order(
    symbol:     str,
    qty:        int,
    side:       str,
    order_type: str = "MARKET",
    price:      float = 0.0,
    exchange:   str = "NSE",
    product:    str = "MIS",
) -> Optional[str]:
    """
    Place order via Kite. Returns order_id or None.
    product: MIS (intraday) / CNC (delivery) / NRML (F&O)
    """
    if not is_configured() or not is_token_valid():
        return None

    kite = get_kite()
    if not kite:
        return None

    try:
        order_id = kite.place_order(
            tradingsymbol=symbol.upper(),
            exchange=exchange,
            transaction_type=side.upper(),
            quantity=qty,
            order_type=order_type.upper(),
            price=price if order_type.upper() == "LIMIT" else None,
            product=product.upper(),
            validity="DAY",
        )
        logger.info("Kite order placed: %s %s %d qty → %s", side, symbol, qty, order_id)
        return str(order_id)
    except Exception as e:
        logger.error("Kite order failed %s: %s", symbol, e)
        return None


def _lookup_kite_token(symbol: str, kite=None) -> Optional[int]:
    """Look up Kite instrument token from cached file."""
    try:
        import json
        from pathlib import Path
        f = Path("kite_instruments.json")
        if f.exists():
            instruments = json.loads(f.read_text())
            for inst in instruments:
                if (inst.get("tradingsymbol","").upper() == symbol.upper()
                        and inst.get("exchange") == "NSE"):
                    return int(inst.get("instrument_token",0))
    except Exception:
        pass
    return None


# ══ Token auto-refresh using Playwright (optional) ════════════════
def auto_refresh_token_playwright() -> bool:
    """
    Automate Kite token refresh using browser automation.
    Requires: pip install playwright && playwright install chromium
    Set env vars: ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_PIN

    Schedule via cron: 0 6 * * 1-5 python3 zerodha_client.py --refresh
    """
    try:
        from playwright.sync_api import sync_playwright
        import requests

        user_id  = os.getenv("ZERODHA_USER_ID","")
        password = os.getenv("ZERODHA_PASSWORD","")
        pin      = os.getenv("ZERODHA_PIN","")
        api_key  = _API_KEY
        api_sec  = _API_SEC

        if not all([user_id, password, pin, api_key, api_sec]):
            logger.error("Zerodha auto-refresh: missing credentials")
            return False

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page    = browser.new_page()

            # Login to Kite
            page.goto(f"https://kite.trade/connect/login?api_key={api_key}&v=3")
            page.fill("#userid", user_id)
            page.fill("#password", password)
            page.click("button[type=submit]")
            page.wait_for_url("**/two-factor**", timeout=10000)

            # PIN
            page.fill("#pin", pin)
            page.click("button[type=submit]")
            page.wait_for_url("**/redirect**", timeout=10000)

            # Extract request_token from URL
            url = page.url
            browser.close()

        if "request_token=" not in url:
            return False

        req_token = url.split("request_token=")[1].split("&")[0]

        # Exchange request_token for access_token
        import hashlib
        checksum = hashlib.sha256(f"{api_key}{req_token}{api_sec}".encode()).hexdigest()
        r = requests.post(
            "https://api.kite.trade/session/token",
            data={
                "api_key":       api_key,
                "request_token": req_token,
                "checksum":      checksum,
            },
        )
        if r.status_code == 200:
            access_token = r.json()["data"]["access_token"]
            # Update .env
            _update_env("ZERODHA_ACCESS_TOKEN", access_token)
            _update_env("ZERODHA_TOKEN_DATE", datetime.now().date().isoformat())
            logger.info("Zerodha token refreshed ✅")
            return True

    except ImportError:
        logger.info("playwright not installed — manual token needed for Zerodha")
    except Exception as e:
        logger.error("Zerodha auto-refresh failed: %s", e)
    return False


def _update_env(key: str, value: str) -> None:
    """Update a key in .env file."""
    import re
    from pathlib import Path
    env_file = Path(".env")
    if not env_file.exists():
        return
    content = env_file.read_text()
    if f"{key}=" in content:
        content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
    else:
        content += f"\n{key}={value}"
    env_file.write_text(content)


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        ok = auto_refresh_token_playwright()
        print("✅ Token refreshed" if ok else "❌ Token refresh failed")
