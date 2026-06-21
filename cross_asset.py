from __future__ import annotations
import os

def _safe_close(df) -> float:
    """Safe yfinance last close — handles both old Series and new MultiIndex."""
    try:
        if df is None or len(df) == 0: return 0.0
        c = df["Close"]
        if hasattr(c, "columns"): c = c.iloc[:, 0]
        v = c.iloc[-1]
        if hasattr(v, "iloc"): v = v.iloc[0]
        return float(v)
    except Exception: return 0.0
"""
cross_asset.py  —  Cross-asset momentum signals for NSE.

RELATIONSHIPS MONITORED:
  USD/INR ↑  → FII outflows → NIFTY bearish
  Brent ↑    → OMC stocks (ONGC/HPCL) react predictably
  US 10Y ↑   → IT sector (TCS/INFY) underperforms
  Gold ↑     → Safe haven → equity bearish
  SGX/GIFT ↑ → NSE gap up expected (already have this)
  VIX (US)   → Global risk-off when VIX > 25

USAGE: score_mod = get_cross_asset_score(direction, sector)
"""
import json, logging, time
from pathlib import Path

logger = logging.getLogger(__name__)
_CACHE = Path("cross_asset_cache.json")
_TTL   = 900   # 15 min refresh


def _fetch_via_tiingo(ticker_sym: str) -> float:
    """Fetch latest price from Tiingo (free, 1000/hr). Backup for yfinance."""
    _key = os.getenv("TIINGO_KEY","43f3cb0bc2a1ea5afd7d8b33c084d584e44ba65b")
    if not _key: return 0.0
    try:
        import requests
        r = requests.get(
            f"https://api.tiingo.com/tiingo/daily/{ticker_sym}/prices",
            params={"token":_key,"resampleFreq":"daily"},
            headers={"Content-Type":"application/json","Authorization":f"Token {_key}"},
            timeout=8)
        if r.status_code==200:
            d = r.json()
            if d: return float(d[-1].get("close",0))
    except Exception: pass
    return 0.0


def _fetch_usdinr_rbi() -> float:
    """Fetch USD/INR from multiple free sources."""
    import requests
    # Source 1: RBI FBIL reference rate (official)
    try:
        r = requests.get(
            "https://www.fbil.org.in/rate/referenceRate",
            timeout=6, headers={"User-Agent":"Mozilla/5.0",
                                  "Accept":"application/json"})
        if r.status_code == 200:
            data = r.json()
            usd = data.get("USD") or data.get("usd") or data.get("USDINR")
            if usd: return float(usd)
    except Exception: pass
    # Source 2: ExchangeRate-API (free, no key needed)
    try:
        r = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=6)
        if r.status_code == 200:
            inr = r.json().get("rates", {}).get("INR")
            if inr: return float(inr)
    except Exception: pass
    # Source 3: Frankfurter (free ECB rates, USD/INR via EUR)
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=INR",
            timeout=6)
        if r.status_code == 200:
            inr = r.json().get("rates", {}).get("INR")
            if inr: return float(inr)
    except Exception: pass
    return 0.0


def _fetch_india_vix() -> float:
    """Fetch India VIX from NSE allIndices API."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                if "INDIA VIX" in str(idx.get("index", "")).upper():
                    v = float(idx.get("last", 0) or 0)
                    if v: return v
    except Exception: pass
    # yfinance fallback
    try:
        import yf_compat as yf
        df = yf.download("^INDIAVIX", period="5d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            c = df["Close"]
            if hasattr(c,"columns"): c = c.iloc[:,0]
            v = float(c.iloc[-1])
            if v: return v
    except Exception: pass
    return 0.0


def _fetch_yahoo_price(ticker: str) -> tuple:
    """
    Fetch price via Stooq (primary, no rate limit) → Yahoo → Alpha Vantage.
    Stooq is free, works globally, no API key required.
    """
    STOOQ_MAP = {
        "^GSPC":    "^spx",
        "^DJI":     "^dji",
        "^IXIC":    "^ndq",
        "^VIX":     "^vix",
        "^TNX":     "10us.b",
        "^N225":    "^nkx",
        "^HSI":     "^hsi",
        "BZ=F":     "lco.f",
        "CL=F":     "cl.f",
        "GC=F":     "gc.f",
        "SI=F":     "si.f",
        "DX-Y.NYB": "usdx.fso",
    }
    stooq_sym = STOOQ_MAP.get(ticker)

    # Source 1: Stooq (free, reliable, no rate limit)
    if stooq_sym:
        try:
            import requests, pandas as _pd, io
            r = requests.get(
                f"https://stooq.com/q/d/l/?s={stooq_sym}&i=d",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
            )
            if r.status_code == 200 and "," in r.text and "No data" not in r.text:
                df = _pd.read_csv(io.StringIO(r.text))
                if not df.empty and "Close" in df.columns and len(df) >= 2:
                    curr = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
                    if curr > 0:
                        logger.debug("Stooq OK %s: %.4f", ticker, curr)
                        return curr, prev
        except Exception as e:
            logger.debug("Stooq %s: %s", ticker, e)

    # Source 2: Yahoo Finance v8 (often rate-limited — kept as fallback)
    try:
        import requests as _rq
        url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
               f"?interval=1d&range=5d")
        r = _rq.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        }, timeout=8)
        if r.status_code == 200:
            meta = r.json()["chart"]["result"][0]["meta"]
            curr = float(meta.get("regularMarketPrice") or meta.get("previousClose") or 0)
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or curr)
            if curr > 0:
                logger.debug("Yahoo OK %s: %.4f", ticker, curr)
                return curr, prev
    except Exception as e:
        logger.debug("Yahoo %s: %s", ticker, e)

    # Source 3: Alpha Vantage (25 calls/day — for key tickers only)
    alpha_map = {
        "GC=F":  ("FOREX_DAILY", "XAUUSD"),
        "BZ=F":  ("BRENT", ""),
    }
    if ticker in alpha_map and os.getenv('ALPHA_VANTAGE_KEY'):
        try:
            import requests
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "TIME_SERIES_DAILY",
                    "symbol": alpha_map[ticker][1] or ticker,
                    "apikey": os.getenv("ALPHA_VANTAGE_KEY"),
                },
                timeout=10,
            )
            if r.status_code == 200:
                ts = r.json().get("Time Series (Daily)", {})
                if ts:
                    dates = sorted(ts.keys(), reverse=True)
                    curr = float(ts[dates[0]]["4. close"]) if dates else 0
                    prev = float(ts[dates[1]]["4. close"]) if len(dates) > 1 else curr
                    if curr > 0:
                        return curr, prev
        except Exception as e:
            logger.debug("AlphaV %s: %s", ticker, e)

    # Source 4: TwelveData (FREE 800/day — works from India!)
    td_map = {
        "^GSPC": "SPX",    "^DJI": "DJI",     "^IXIC": "IXIC",
        "^VIX": "VIX",     "^TNX": "TNX",      "BZ=F": "BRENT",
        "CL=F": "WTI",     "GC=F": "XAU/USD",  "SI=F": "XAG/USD",
        "DX-Y.NYB": "DXY",
    }
    td_sym = td_map.get(ticker)
    td_key = os.getenv("TWELVE_DATA_KEY", "")
    if td_sym and td_key:
        try:
            import requests
            r = requests.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": td_sym, "interval": "1day",
                        "outputsize": "3", "apikey": td_key},
                timeout=10,
            )
            if r.status_code == 200:
                vals = r.json().get("values", [])
                if vals and len(vals) >= 2:
                    curr = float(vals[0].get("close", 0))
                    prev = float(vals[1].get("close", 0))
                    if curr > 0:
                        logger.debug("TwelveData OK %s: %.4f", ticker, curr)
                        return curr, prev
        except Exception as e:
            logger.debug("TwelveData %s: %s", ticker, e)

    # Source 5: Tiingo (FREE 1000/hr — works from India)
    tiingo_map = {
        "^GSPC": "SPY",    "^VIX": "VIXY",     "GC=F": "GLD",
        "BZ=F": "USO",     "DX-Y.NYB": "UUP",
    }
    tiingo_sym = tiingo_map.get(ticker)
    tiingo_key = os.getenv("TIINGO_KEY", "")
    if tiingo_sym and tiingo_key:
        try:
            import requests
            r = requests.get(
                f"https://api.tiingo.com/tiingo/daily/{tiingo_sym}/prices",
                headers={"Authorization": f"Token {tiingo_key}"},
                params={"startDate": (__import__("datetime").date.today() -
                         __import__("datetime").timedelta(days=5)).isoformat()},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                if data and len(data) >= 2:
                    curr = float(data[-1].get("close", 0))
                    prev = float(data[-2].get("close", 0))
                    if curr > 0:
                        logger.debug("Tiingo OK %s: %.4f", ticker, curr)
                        return curr, prev
        except Exception as e:
            logger.debug("Tiingo %s: %s", ticker, e)

    return 0.0, 0.0


def _fetch_prices() -> dict:
    """
    Fetch real global cross-asset prices.
    Sources:
      - Yahoo Finance JSON API (S&P, DXY, Crude, Gold, VIX, US10Y) — free, no auth
      - NSE allIndices (India VIX, GIFT Nifty)
      - ExchangeRate-API (USD/INR)
    """
    result = {}

    TICKERS = {
        "SP500":   ("^GSPC",     "S&P 500"),
        "DXY":     ("DX-Y.NYB",  "Dollar Index"),
        "BRENT":   ("BZ=F",      "Brent Crude"),
        "GOLD":    ("GC=F",      "Gold"),
        "USVIX":   ("^VIX",      "US VIX"),
        "US10Y":   ("^TNX",      "US 10Y Yield"),
        "USDINR":  ("USDINR=X",  "USD/INR"),
        "NIKKEI":  ("^N225",     "Nikkei 225"),
        "SGXNIFTY":("^NSEBANK",  "SGX/GIFT Nifty"),
    }

    for key, (ticker, label) in TICKERS.items():
        curr, prev = _fetch_yahoo_price(ticker)
        if curr > 0:
            chg = (curr - prev) / prev * 100 if prev > 0 else 0.0
            result[key] = {
                "price":      round(curr, 4),
                "prev":       round(prev, 4),
                "change_pct": round(chg, 3),
                "label":      label,
            }
            logger.debug("✅ %s: %.4f (%.3f%%)", key, curr, chg)

    # India VIX from NSE (more reliable)
    vix = _fetch_india_vix()
    if vix > 0:
        result["INDIAVIX"] = {"price": vix, "prev": vix, "change_pct": 0.0, "label": "India VIX"}

    # USD/INR from RBI if Yahoo failed
    if not result.get("USDINR") or result["USDINR"]["price"] <= 0:
        usdinr = _fetch_usdinr_rbi()
        if usdinr > 0:
            result["USDINR"] = {"price": usdinr, "prev": usdinr, "change_pct": 0.0, "label": "USD/INR"}

    logger.info("Cross-asset fetch: %d/%d tickers", len(result), len(TICKERS))
    return result


def get_cross_asset_data(force: bool = False) -> dict:
    try:
        if not force and _CACHE.exists():
            d = json.loads(_CACHE.read_text())
            if time.time() - d.get("ts", 0) < _TTL:
                return d.get("data", {})
    except Exception:
        pass
    data = _fetch_prices()
    if data:
        try:
            _CACHE.write_text(json.dumps({"ts": time.time(), "data": data}))
        except Exception:
            pass
    # Fallbacks for key signals if yfinance failed
    if not data.get("USDINR"):
        v = _fetch_usdinr_rbi()
        if v: data["USDINR"] = {"price": v, "change_pct": 0, "prev": v}
    if not data.get("INDIAVIX"):
        v = _fetch_india_vix()
        if v: data["INDIAVIX"] = {"price": v, "change_pct": 0, "prev": v}
    if not data.get("GOLD"):
        v = _fetch_via_tiingo("gld")  # GLD ETF proxy
        if v: data["GOLD"] = {"price": v, "change_pct": 0, "prev": v}
    return data


# Sector → affected cross-asset signals
SECTOR_SIGNALS = {
    "IT":       ["US10Y"],           # US rates hurt IT valuations
    "OMC":      ["BRENT"],           # Oil price drives OMC
    "METAL":    ["GOLD", "BRENT"],   # Commodity complex
    "BANKING":  ["USDINR"],          # FII flows affect banks
    "INDICES":  ["USDINR", "USVIX"], # Macro affects indices
    "DEFAULT":  ["USDINR", "USVIX"],
}

# IT stocks
IT_STOCKS    = {"TCS","INFOSYS","WIPRO","HCLTECH","TECHM","LTTS","PERSISTENT"}
OMC_STOCKS   = {"ONGC","HINDPETRO","BPCL","IOC","RELIANCE"}
METAL_STOCKS = {"TATASTEEL","HINDALCO","JSWSTEEL","VEDL","COALINDIA","NMDC"}
BANK_STOCKS  = {"HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK"}


def _get_sector(symbol: str) -> str:
    s = symbol.upper()
    if s in IT_STOCKS:      return "IT"
    if s in OMC_STOCKS:     return "OMC"
    if s in METAL_STOCKS:   return "METAL"
    if s in BANK_STOCKS:    return "BANKING"
    if s in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}: return "INDICES"
    return "DEFAULT"


def get_cross_asset_score(symbol: str, direction: str) -> float:
    """
    Returns score modifier based on cross-asset momentum.
    Positive = aligned with cross-asset signal.
    Negative = fighting cross-asset signal.
    """
    try:
        data   = get_cross_asset_data()
        sector = _get_sector(symbol)
        signals = SECTOR_SIGNALS.get(sector, SECTOR_SIGNALS["DEFAULT"])
        modifier = 0.0

        for signal in signals:
            row = data.get(signal, {})
            chg = float(row.get("change_pct", 0))

            if signal == "USDINR":
                # INR weakening = FII outflows = bearish equities
                if chg > 0.3 and direction == "BUY":   modifier -= 0.8
                if chg > 0.3 and direction == "SELL":  modifier += 0.5
                if chg < -0.3 and direction == "BUY":  modifier += 0.5
                if chg < -0.3 and direction == "SELL": modifier -= 0.5

            elif signal == "USVIX":
                price = float(row.get("price", 15))
                if price > 25 and direction == "BUY":   modifier -= 1.0  # risk-off
                if price > 30 and direction == "BUY":   modifier -= 1.5  # extreme fear
                if price < 15 and direction == "SELL":  modifier -= 0.5  # complacency

            elif signal == "BRENT":
                # Crude up → OMC cost up (bearish for OMC)
                if sector == "OMC":
                    if chg > 1.5 and direction == "BUY":   modifier -= 0.8
                    if chg > 1.5 and direction == "SELL":  modifier += 0.8
                    if chg < -1.5 and direction == "BUY":  modifier += 0.8

            elif signal == "US10Y":
                # Rising US rates → IT bearish
                if sector == "IT":
                    if chg > 2.0 and direction == "BUY":   modifier -= 0.8
                    if chg < -2.0 and direction == "SELL": modifier -= 0.5

        return round(max(-2.0, min(2.0, modifier)), 2)
    except Exception as e:
        logger.debug("Cross-asset score: %s", e)
        return 0.0


def get_market_bias(data: dict = None) -> float:
    """
    Overall NIFTY bias score from global cross-asset signals.
    Returns float: positive = bullish, negative = bearish.
    Uses: S&P500, DXY, Brent crude, US VIX, USD/INR, US 10Y yield.
    """
    if data is None:
        data = get_cross_asset_data()

    score = 0.0

    # S&P 500 direction (strong leading indicator for NIFTY)
    sp_chg = float((data.get("SP500",  {}) or {}).get("change_pct", 0))
    if   sp_chg >  1.0: score += 0.4   # strong rally
    elif sp_chg >  0.3: score += 0.2
    elif sp_chg < -1.0: score -= 0.4
    elif sp_chg < -0.3: score -= 0.2

    # USD/INR (stronger INR = FII inflows = bullish)
    usd_chg = float((data.get("USDINR", {}) or {}).get("change_pct", 0))
    if   usd_chg >  0.5: score -= 0.3  # INR weakening = bearish
    elif usd_chg < -0.5: score += 0.3  # INR strengthening = bullish

    # US VIX (fear gauge — high VIX = risk-off)
    vix_val = float((data.get("USVIX",  {}) or {}).get("price", 15))
    if   vix_val > 30: score -= 0.4
    elif vix_val > 22: score -= 0.2
    elif vix_val < 15: score += 0.1

    # Brent Crude (high oil = inflationary, bad for India)
    brent_chg = float((data.get("BRENT", {}) or {}).get("change_pct", 0))
    if   brent_chg >  3.0: score -= 0.3
    elif brent_chg >  1.5: score -= 0.1
    elif brent_chg < -3.0: score += 0.2

    # DXY (strong dollar = EM outflows)
    dxy_chg = float((data.get("DXY",    {}) or {}).get("change_pct", 0))
    if   dxy_chg >  0.5: score -= 0.2
    elif dxy_chg < -0.5: score += 0.2

    # US 10Y yield (rising yield = FII outflows from EM)
    u10y_chg = float((data.get("US10Y", {}) or {}).get("change_pct", 0))
    if   u10y_chg >  2.0: score -= 0.2
    elif u10y_chg < -2.0: score += 0.1

    # Gold (safe haven — high gold = risk-off)
    gold_chg = float((data.get("GOLD",  {}) or {}).get("change_pct", 0))
    if   gold_chg >  1.5: score -= 0.1  # flight to safety
    elif gold_chg < -1.5: score += 0.1

    return round(max(-1.0, min(1.0, score)), 3)

