"""
iv_percentile.py

Sheldon Natenberg — Option Volatility and Pricing
IV Percentile (IVP) and Volatility Cone implementation.

THE CORE NATENBERG INSIGHT:
  Options are NOT always fairly priced.
  Sometimes IV is historically cheap → options are bargains → BUY.
  Sometimes IV is historically expensive → options are overpriced → SELL.

  IVP = What % of the past 252 days had IV lower than today?
  IVP = 80% means IV is higher than 80% of past year → expensive → SELL options
  IVP = 20% means IV is lower than 20% of past year → cheap → BUY options

RULES:
  IVP < 30  → Options cheap  → Buy options (CE or PE based on direction)
  IVP 30-70 → Options normal → Standard position sizing
  IVP > 70  → Options expensive → Prefer selling spreads over buying
  IVP > 85  → Options very expensive → Strong sell-options signal

IMPLEMENTATION:
  We approximate IVP using India VIX history.
  VIX is the market's IV — high VIX = expensive options, low VIX = cheap.
  Historical VIX data fetched from NSE website.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

IVP_CACHE_FILE = "ivp_cache.json"
IVP_CACHE_TTL  = 3600   # refresh every hour


class IVPercentile:
    """
    Calculates IV Percentile using India VIX as proxy for NIFTY options IV.
    Provides sizing multipliers and trading bias based on IVP.
    """

    LOOKBACK_DAYS = 252   # 1 trading year

    def __init__(self, cache_file: str = IVP_CACHE_FILE) -> None:
        self._cache_file = Path(cache_file)
        self._vix_history: list = []
        self._last_fetch: float = 0.0
        self._current_vix: float = 0.0
        self._ivp: float = 50.0   # default neutral

    # ── Public API ────────────────────────────────────────────────────────────

    def _fetch_live_vix(self) -> float:
        """Fetch India VIX from NSE allIndices API."""
        try:
            import requests as _rq
            s = _rq.Session()
            s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            s.get("https://www.nseindia.com/", timeout=5)
            r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
            if r.status_code == 200:
                for idx in r.json().get("data",[]):
                    if "INDIA VIX" in str(idx.get("index","")).upper():
                        v = float(idx.get("last",0) or 0)
                        if v: return v
        except Exception: pass
        # yfinance fallback
        try:
            import yf_compat as _yf, json as _j
            t = _yf.Ticker("^INDIAVIX")
            df = t.history(period="5d", interval="1d")
            if df is not None and len(df)>0:
                v = float(df["Close"].iloc[-1])
                if v: return v
        except Exception: pass
        return 15.0  # safe default — not 0

    def get_ivp(self, current_vix: float = 0) -> dict:
        """
        Returns IV Percentile and trading bias.

        Returns:
            {
              "ivp":          float (0-100),
              "current_vix":  float,
              "bias":         "CHEAP" | "NORMAL" | "EXPENSIVE" | "VERY_EXPENSIVE",
              "action":       "BUY_OPTIONS" | "NEUTRAL" | "SELL_OPTIONS",
              "size_mult":    float (0.5 - 1.3),
              "note":         str,
            }
        """
        if current_vix > 0:
            self._current_vix = current_vix

        # Refresh history if needed
        if (time.time() - self._last_fetch) > IVP_CACHE_TTL:
            self._fetch_vix_history()

        ivp = self._calculate_ivp(self._current_vix)
        self._ivp = ivp

        if ivp < 20:
            bias      = "VERY_CHEAP"
            action    = "BUY_OPTIONS"
            size_mult = 1.3
            note      = f"VIX={self._current_vix:.1f} IVP={ivp:.0f}% — options very cheap, buy more"
        elif ivp < 35:
            bias      = "CHEAP"
            action    = "BUY_OPTIONS"
            size_mult = 1.1
            note      = f"VIX={self._current_vix:.1f} IVP={ivp:.0f}% — options cheap, good time to buy"
        elif ivp < 65:
            bias      = "NORMAL"
            action    = "NEUTRAL"
            size_mult = 1.0
            note      = f"VIX={self._current_vix:.1f} IVP={ivp:.0f}% — options fairly priced"
        elif ivp < 80:
            bias      = "EXPENSIVE"
            action    = "PREFER_SELL"
            size_mult = 0.8
            note      = f"VIX={self._current_vix:.1f} IVP={ivp:.0f}% — options expensive, reduce size if buying"
        else:
            bias      = "VERY_EXPENSIVE"
            action    = "SELL_OPTIONS"
            size_mult = 0.6
            note      = f"VIX={self._current_vix:.1f} IVP={ivp:.0f}% — options very expensive, prefer selling spreads"

        return {
            "ivp":         round(ivp, 1),
            "current_vix": self._current_vix,
            "bias":        bias,
            "action":      action,
            "size_mult":   size_mult,
            "note":        note,
        }

    def should_buy_options(self, vix: float = 0) -> bool:
        """True when options are cheap enough to buy (IVP < 65)."""
        data = self.get_ivp(vix)
        return data["ivp"] < 65

    def should_sell_options(self, vix: float = 0) -> bool:
        """True when options are expensive enough to sell (IVP > 70)."""
        data = self.get_ivp(vix)
        return data["ivp"] > 70

    def get_size_multiplier(self, vix: float = 0) -> float:
        """Returns lot size multiplier based on IVP."""
        return self.get_ivp(vix)["size_mult"]

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _fetch_vix_history(self) -> None:
        """Fetch India VIX history from NSE."""
        # Try cache first
        if self._load_cache():
            return

        vix_data = []
        try:
            import requests
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.nseindia.com/",
            })
            # Prime cookie
            session.get("https://www.nseindia.com/", timeout=5)

            # NSE VIX history endpoint
            end_date   = date.today()
            start_date = end_date - timedelta(days=400)
            url = (
                f"https://www.nseindia.com/api/historical/vixhistory?"
                f"from={start_date.strftime('%d-%m-%Y')}"
                f"&to={end_date.strftime('%d-%m-%Y')}"
            )
            resp = session.get(url, timeout=10)
            data = resp.json()

            for row in data.get("data", []):
                try:
                    vix_close = float(row.get("EOD_CLOSE_PRICE") or
                                     row.get("Close") or
                                     row.get("close") or 0)
                    if vix_close > 0:
                        vix_data.append(vix_close)
                except Exception:
                    continue

            if vix_data:
                self._vix_history = vix_data[-self.LOOKBACK_DAYS:]
                self._save_cache()
                logger.info("VIX history loaded: %d days, range %.1f-%.1f",
                            len(self._vix_history),
                            min(self._vix_history), max(self._vix_history))
        except Exception as e:
            logger.debug("VIX history fetch: %s", e)

        # Fallback: use approximate VIX percentiles from memory
        if not vix_data:
            # NSE VIX typical range over past year (approximate)
            self._vix_history = self._get_fallback_history()

        self._last_fetch = time.time()

    def _get_fallback_history(self) -> list:
        """
        Approximate India VIX history if NSE API fails.
        Based on typical NSE VIX distribution (2020-2026).
        """
        import random
        # NSE VIX typically ranges 11-35, median ~14
        # Skewed distribution — more low values, rare high values
        history = []
        for _ in range(252):
            # Rough approximation: log-normal around 14
            v = max(10, min(40, random.gauss(14.5, 3.5)))
            history.append(round(v, 2))
        return sorted(history)   # sort for percentile calculation

    def _calculate_ivp(self, current_vix: float) -> float:
        """Calculate IV Percentile: % of historical days with lower VIX."""
        if not self._vix_history or current_vix <= 0:
            return 50.0
        below = sum(1 for v in self._vix_history if v < current_vix)
        return round(below / len(self._vix_history) * 100, 1)

    def _save_cache(self) -> None:
        try:
            data = {
                "history": self._vix_history,
                "date":    date.today().isoformat(),
            }
            self._cache_file.write_text(json.dumps(data))
        except Exception:
            pass

    def _load_cache(self) -> bool:
        try:
            if not self._cache_file.exists():
                return False
            data = json.loads(self._cache_file.read_text())
            if data.get("date") != date.today().isoformat():
                return False
            self._vix_history = data.get("history", [])
            self._last_fetch  = time.time()
            return bool(self._vix_history)
        except Exception:
            return False


# ── Volatility Cone ───────────────────────────────────────────────────────────

def calculate_volatility_cone(df, windows=(5, 10, 20, 30)) -> dict:
    """
    Natenberg's Volatility Cone:
    Compare current realised volatility to historical distribution.
    If current vol > 75th percentile → overvalued, consider selling.
    If current vol < 25th percentile → undervalued, consider buying.
    """
    try:
        import numpy as np
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        close = df_c["close"]
        returns = close.pct_change().dropna()

        result = {}
        for w in windows:
            if len(returns) < w * 4:
                continue
            rolling_vol = returns.rolling(w).std() * (252 ** 0.5) * 100
            rolling_vol = rolling_vol.dropna()
            current_rv  = float(rolling_vol.iloc[-1])
            p25 = float(np.percentile(rolling_vol, 25))
            p75 = float(np.percentile(rolling_vol, 75))
            p50 = float(np.percentile(rolling_vol, 50))

            if current_rv < p25:
                signal = "LOW_VOL_BUY"
            elif current_rv > p75:
                signal = "HIGH_VOL_SELL"
            else:
                signal = "NORMAL"

            result[f"{w}d"] = {
                "current": round(current_rv, 2),
                "p25":     round(p25, 2),
                "p50":     round(p50, 2),
                "p75":     round(p75, 2),
                "signal":  signal,
            }
        return result
    except Exception as e:
        logger.debug("Vol cone error: %s", e)
        return {}


# Singleton
_ivp: Optional[IVPercentile] = None
def get_ivp() -> IVPercentile:
    global _ivp
    if _ivp is None:
        _ivp = IVPercentile()
    return _ivp
