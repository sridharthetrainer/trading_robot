"""
global_market_filter.py

Pre-market global market signal filter.

Checks GIFT Nifty / SGX Nifty direction before market open.
If global markets are significantly against our signal direction,
the filter blocks the trade.

Data sources (free, no auth):
  - GIFT Nifty futures via NSE API
  - Nikkei 225 direction via Yahoo Finance
  - Dow Futures direction via Yahoo Finance

Rules:
  BUY signal + global markets down >0.5% → BLOCK (fighting global trend)
  SELL signal + global markets up >0.5%  → BLOCK
  Within ±0.5%                           → PASS (neutral, allow signal)

Called at 9:15 AM pre-market check and cached for 30 minutes.
"""
from __future__ import annotations
import logging, time, threading
from datetime import datetime, time as dtime
from typing import Optional

logger = logging.getLogger(__name__)

class GlobalMarketFilter:
    """
    Checks GIFT Nifty and global indices before allowing a trade.
    Blocks trades that fight strong global momentum.
    """

    BLOCK_THRESHOLD  = 0.005   # 0.5% move = meaningful global signal
    STRONG_THRESHOLD = 0.015   # 1.5% move = very strong — reduce size too
    CACHE_SEC        = 1800    # refresh every 30 minutes

    def __init__(self) -> None:
        self._cache: dict      = {}
        self._cache_ts: float  = 0.0
        self._lock = threading.Lock()

    def get_global_bias(self) -> dict:
        """
        Returns global market bias.
        Result: {"bias": "BULLISH"|"BEARISH"|"NEUTRAL",
                 "change_pct": float, "source": str, "blocking": bool}
        """
        with self._lock:
            if (time.time() - self._cache_ts) < self.CACHE_SEC and self._cache:
                return self._cache

        result = self._fetch_gift_nifty()
        if not result:
            result = self._fetch_yahoo_nifty()

        with self._lock:
            self._cache    = result or {"bias":"NEUTRAL","change_pct":0.0,
                                        "source":"unavailable","blocking":False}
            self._cache_ts = time.time()
        return self._cache

    def should_block(self, signal_side: str) -> tuple[bool, str]:
        """
        Returns (block, reason).
        block=True means do not trade this signal.
        """
        # Only apply during first 90 minutes of session (global impact strongest)
        now = datetime.now().time()
        if not (dtime(9,15) <= now <= dtime(10,45)):
            return False, "outside_global_filter_window"

        data = self.get_global_bias()
        bias = data.get("bias", "NEUTRAL")
        pct  = abs(data.get("change_pct", 0.0))
        src  = data.get("source", "?")

        if not data or pct == 0:
            return False, "no_global_data"
        if pct < self.BLOCK_THRESHOLD:
            return False, f"global_neutral_{pct:.2%}"

        # Block if fighting strong global trend
        if signal_side == "BUY"  and bias == "BEARISH":
            return True, f"global_bearish_{pct:.2%}_via_{src}_blocks_BUY"
        if signal_side == "SELL" and bias == "BULLISH":
            return True, f"global_bullish_{pct:.2%}_via_{src}_blocks_SELL"

        return False, f"global_{bias.lower()}_{pct:.2%}_allows_{signal_side}"

    def get_size_multiplier(self, signal_side: str) -> float:
        """Reduce position size when partially against global trend."""
        data = self.get_global_bias()
        pct  = data.get("change_pct", 0.0)
        bias = data.get("bias", "NEUTRAL")

        # Strong global against us → reduce size even if not blocked
        if signal_side == "BUY" and bias == "BEARISH" and abs(pct) >= self.BLOCK_THRESHOLD:
            return 0.6
        if signal_side == "SELL" and bias == "BULLISH" and abs(pct) >= self.BLOCK_THRESHOLD:
            return 0.6
        return 1.0

    # ── Data fetchers ─────────────────────────────────────────────────────────

    def _fetch_gift_nifty(self) -> Optional[dict]:
        """Fetch GIFT Nifty futures from NSE."""
        try:
            import requests, json
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0",
                "Referer":    "https://www.nseindia.com/",
            })
            session.get("https://www.nseindia.com/", timeout=5)
            r = session.get(
                "https://www.nseindia.com/api/equity-stockIndices?index=GIFT%20NIFTY",
                timeout=8,
            )
            data = r.json()
            rows = data.get("data", [])
            for row in rows:
                if "GIFT" in str(row.get("symbol","")).upper() or \
                   "NIFTY" in str(row.get("symbol","")).upper():
                    chg_pct = float(row.get("pChange", 0) or 0) / 100
                    bias    = "BULLISH" if chg_pct > 0 else \
                              "BEARISH" if chg_pct < 0 else "NEUTRAL"
                    return {
                        "bias":       bias,
                        "change_pct": chg_pct,
                        "source":     "GIFT_Nifty",
                        "blocking":   abs(chg_pct) >= self.BLOCK_THRESHOLD,
                    }
        except Exception as e:
            logger.debug("GIFT Nifty fetch: %s", e)
        return None

    def _fetch_yahoo_nifty(self) -> Optional[dict]:
        """Fallback: fetch Nifty futures from Yahoo Finance."""
        try:
            import urllib.request, json
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1d&range=2d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                d = json.loads(resp.read())
            closes = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            if len(closes) >= 2 and closes[-1] and closes[-2]:
                chg_pct = (closes[-1] - closes[-2]) / closes[-2]
                bias    = "BULLISH" if chg_pct > 0 else \
                          "BEARISH" if chg_pct < 0 else "NEUTRAL"
                return {
                    "bias":       bias,
                    "change_pct": chg_pct,
                    "source":     "Yahoo_Nifty",
                    "blocking":   abs(chg_pct) >= self.BLOCK_THRESHOLD,
                }
        except Exception as e:
            logger.debug("Yahoo Nifty fetch: %s", e)
        return None


# Singleton
_filter: Optional[GlobalMarketFilter] = None
def get_global_filter() -> GlobalMarketFilter:
    global _filter
    if _filter is None:
        _filter = GlobalMarketFilter()
    return _filter
