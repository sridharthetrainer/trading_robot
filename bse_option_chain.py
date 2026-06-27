"""
bse_option_chain.py — BSE SENSEX & BANKEX option chain fetcher.

BSE publishes SENSEX and BANKEX option chains FREE at:
  https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Scrip_cd=

Unlike NSE:
  - Exchange is BSE (not NSE)
  - F&O exchange is BFO (not NFO)
  - Token for SENSEX: 99926000
  - Token for BANKEX: 99919000
  - Lot sizes: SENSEX=10, BANKEX=15

SIGNALS FROM SENSEX OPTIONS:
  If SENSEX and NIFTY option chain give conflicting signals
  (e.g. SENSEX PCR bullish but NIFTY PCR bearish) →
  market has sector divergence → use NIFTY for trade decision
  
  SENSEX IT-heavy (Infosys, TCS, Wipro, HCL, Wipro = 25%+)
  NIFTY more balanced across sectors
  Divergence = IT-specific event (US tech news, FAANG results)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_CACHE_FILE = Path("bse_option_chain_cache.json")
_TTL        = 180  # 3 minutes

BSE_UNDERLYINGS = {
    "SENSEX": {
        "symbol":   "SENSEX",
        "token":    "99926000",
        "lot_size": 10,
        "exchange": "BFO",
        "bse_code": "999901",
    },
    "BANKEX": {
        "symbol":   "BANKEX",
        "token":    "99919000",
        "lot_size": 15,
        "exchange": "BFO",
        "bse_code": "999907",
    },
}


def _fetch_bse_option_chain(symbol: str = "SENSEX") -> dict:
    """Fetch BSE option chain from BSE India API."""
    try:
        meta = BSE_UNDERLYINGS.get(symbol.upper(), BSE_UNDERLYINGS["SENSEX"])
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://www.bseindia.com/",
        })
        # BSE option chain endpoint
        url = (f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
               f"?Scrip_cd={meta['bse_code']}")
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            logger.debug("BSE OC HTTP %d for %s", r.status_code, symbol)
            return {}
        return r.json()
    except Exception as e:
        logger.debug("BSE option chain fetch: %s", e)
        return {}


def _fetch_bse_index_level(symbol: str = "SENSEX") -> float:
    """Get current BSE index level."""
    ticker_map = {"SENSEX": "^BSESN", "BANKEX": "BANKEX.BO"}
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        ticker = ticker_map.get(symbol.upper(), "^BSESN")
        data   = yf.download(ticker, period="1d", interval="5m",
                             progress=False, auto_adjust=True)
        if data is not None and len(data) > 0:
            return float(data["Close"].iloc[-1])
    except Exception:
        pass
    # Fallback: NSE API has SENSEX proxy
    return 0.0


def get_bse_pcr(symbol: str = "SENSEX") -> dict:
    """
    Get PCR and key levels for BSE SENSEX/BANKEX options.
    Returns same format as NSE option chain for drop-in compatibility.
    """
    empty = {
        "symbol":    symbol,
        "pcr":       1.0,
        "signal":    "NEUTRAL",
        "spot":      0.0,
        "max_pain":  0.0,
        "exchange":  "BFO",
        "lot_size":  BSE_UNDERLYINGS.get(symbol.upper(), {}).get("lot_size", 10),
    }
    try:
        # Try cache first
        if _CACHE_FILE.exists():
            try:
                cached = json.loads(_CACHE_FILE.read_text())
                entry  = cached.get(symbol.upper(), {})
                if time.time() - entry.get("ts", 0) < _TTL:
                    return entry.get("data", empty)
            except Exception:
                pass

        raw  = _fetch_bse_option_chain(symbol)
        spot = _fetch_bse_index_level(symbol)

        # Parse BSE response (structure differs from NSE)
        total_ce = 0.0
        total_pe = 0.0
        for row in raw.get("data", []):
            ce_oi = float(row.get("CALLOI", 0) or 0)
            pe_oi = float(row.get("PUTOI",  0) or 0)
            total_ce += ce_oi
            total_pe += pe_oi

        pcr    = round(total_pe / total_ce, 3) if total_ce > 0 else 1.0
        signal = "BULLISH" if pcr > 1.3 else "BEARISH" if pcr < 0.7 else "NEUTRAL"

        result = {**empty, "pcr": pcr, "signal": signal, "spot": spot}

        # Save cache
        try:
            cached = {}
            if _CACHE_FILE.exists():
                cached = json.loads(_CACHE_FILE.read_text())
            cached[symbol.upper()] = {"ts": time.time(), "data": result}
            _CACHE_FILE.write_text(json.dumps(cached))
        except Exception:
            pass

        return result

    except Exception as e:
        logger.debug("BSE PCR error: %s", e)
        return empty


def get_sensex_banknifty_divergence() -> dict:
    """
    Check if SENSEX and NIFTY are diverging — indicates sector-specific event.
    
    Returns:
        diverging:   bool
        sensex_pct:  float  (SENSEX % change)
        nifty_pct:   float  (NIFTY % change)
        divergence:  float  (difference in %)
        signal:      str    (IT_SPECIFIC, BANK_SPECIFIC, BROAD_MARKET, NEUTRAL)
    """
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken

        sensex = yf.download("^BSESN",  period="2d", interval="1d",
                             progress=False, auto_adjust=True)
        nifty  = yf.download("^NSEI",   period="2d", interval="1d",
                             progress=False, auto_adjust=True)
        bankn  = yf.download("^NSEBANK",period="2d", interval="1d",
                             progress=False, auto_adjust=True)

        def pct_chg(df):
            if df is not None and len(df) >= 2:
                return float((df["Close"].iloc[-1] - df["Close"].iloc[-2])
                              / df["Close"].iloc[-2] * 100)
            return 0.0

        s_pct = pct_chg(sensex)
        n_pct = pct_chg(nifty)
        b_pct = pct_chg(bankn)
        div   = round(s_pct - n_pct, 3)

        # SENSEX is IT-heavy; NIFTY is balanced
        # Large divergence = IT-specific or Bank-specific event
        if abs(div) > 0.5:
            signal = "IT_SPECIFIC" if s_pct > n_pct else "BROAD_MARKET"
        elif abs(b_pct - n_pct) > 0.7:
            signal = "BANK_SPECIFIC"
        else:
            signal = "NEUTRAL"

        return {
            "diverging":   abs(div) > 0.3,
            "sensex_pct":  round(s_pct, 3),
            "nifty_pct":   round(n_pct, 3),
            "banknifty_pct": round(b_pct, 3),
            "divergence":  div,
            "signal":      signal,
        }
    except Exception as e:
        logger.debug("Divergence check: %s", e)
        return {"diverging": False, "divergence": 0.0, "signal": "NEUTRAL",
                "sensex_pct": 0.0, "nifty_pct": 0.0, "banknifty_pct": 0.0}
