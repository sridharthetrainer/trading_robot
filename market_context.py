"""
market_context.py

NSE market context module.
Provides daily market-wide context signals that improve all strategies.

Features implemented
───────────────────
1. India VIX Direction Tracker
   VIX falling → trend/breakout strategies boosted
   VIX rising  → MR strategy boosted
   VIX spike > 2 points → block ALL new entries

2. Previous Day Bias
   If NIFTY closed above 5-day EMA yesterday → bullish bias today (+1)
   If below → bearish bias today (-1)
   Applied as score modifier on directional signals

3. FII/DII Data Integration
   Fetches NSE provisional FII/DII data daily (after 3:30 PM)
   If FII net sellers > ₹2000 Cr → reduce BUY signal confidence by 0.15 next day
   Cached for next trading day

4. Sector Rotation Detector
   Tracks 5-day relative performance: NIFTY vs BANKNIFTY vs FINNIFTY
   Routes priority to the strongest-trending index
   Updated daily

5. Max Pain Score Modifier (on expiry days)
   If spot is within 100pts of max pain → boost signals toward max pain by +1.0
   If signals point away from max pain → penalty of -0.5

Usage:
    from market_context import MarketContext
    ctx = MarketContext()
    ctx.update_daily()              # call once per day after market
    bias = ctx.get_signal_bias("BUY", strategy="trend")   # → score modifier
    is_blocked = ctx.is_vix_spike_blocking()               # → bool
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONTEXT_FILE = "market_context.json"

# ── Thresholds ────────────────────────────────────────────────────────────────
VIX_SPIKE_THRESHOLD    = 2.0    # block entries if VIX rises this much in a session
FII_SELL_THRESHOLD     = 2000   # ₹ crore net sell to penalise BUY signals
MAX_PAIN_BOOST_RANGE   = 100    # within 100pts of max pain → boost
SECTOR_LOOKBACK_DAYS   = 5


class MarketContext:
    """
    Daily market-wide context that adjusts signal confidence for all strategies.
    Updated once per day after market close.
    """

    def __init__(self, context_file: str = CONTEXT_FILE) -> None:
        self._file  = Path(context_file)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            try:
                self._data = json.loads(self._file.read_text())
            except Exception:
                self._data = {}

    def _save(self) -> None:
        try:
            self._file.write_text(json.dumps(self._data, indent=2, default=str))
        except Exception:
            pass

    # ── VIX Direction ─────────────────────────────────────────────────────────

    def record_vix(self, vix_value: float) -> None:
        """Record today's VIX closing value."""
        today = date.today().isoformat()
        hist  = self._data.setdefault("vix_history", [])
        hist  = [h for h in hist if h["date"] != today]
        hist.append({"date": today, "vix": round(float(vix_value), 2)})
        hist  = sorted(hist, key=lambda x: x["date"])[-10:]
        self._data["vix_history"] = hist
        self._save()

    def get_vix_direction(self) -> str:
        """
        Returns "falling", "rising", or "neutral".
        Based on today's VIX vs yesterday's.
        """
        hist = self._data.get("vix_history", [])
        if len(hist) < 2:
            return "neutral"
        today_vix = hist[-1]["vix"]
        prev_vix  = hist[-2]["vix"]
        change    = today_vix - prev_vix
        if change <= -0.5:
            return "falling"
        if change >= VIX_SPIKE_THRESHOLD:
            return "spike"
        if change >= 0.5:
            return "rising"
        return "neutral"

    def is_vix_spike_blocking(self) -> bool:
        """True if VIX spike today — block all new entries."""
        return self.get_vix_direction() == "spike"

    def get_vix_strategy_bias(self, strategy: str) -> float:
        """Score modifier based on VIX direction for a given strategy."""
        direction = self.get_vix_direction()
        if direction == "spike":
            return -2.0   # blocks entry effectively

        trend_strategies  = {"trend", "breakout", "orb", "supertrend_mtf", "ma_cross"}
        mr_strategies     = {"mean_reversion", "vwap_reversion"}

        if direction == "falling":
            return 0.30 if strategy in trend_strategies else 0.0
        if direction == "rising":
            return 0.30 if strategy in mr_strategies else -0.10
        return 0.0

    # ── Previous Day Bias ─────────────────────────────────────────────────────

    def update_prev_day_bias(
        self, symbol: str, close: float, ema5: float
    ) -> None:
        """Record yesterday's close vs 5-EMA for tomorrow's bias."""
        biases = self._data.setdefault("prev_day_bias", {})
        biases[symbol.upper()] = {
            "date":      date.today().isoformat(),
            "close":     round(float(close), 2),
            "ema5":      round(float(ema5),  2),
            "bullish":   close > ema5,
        }
        self._save()

    def get_prev_day_bias(self, symbol: str) -> float:
        """
        Returns +0.25 (bullish bias), -0.25 (bearish bias), or 0.0 (no data).
        If yesterday's close > 5-EMA: bullish bias for today.
        """
        b = self._data.get("prev_day_bias", {}).get(symbol.upper(), {})
        if not b:
            return 0.0
        # Only valid if recorded today or yesterday
        try:
            rec_date = date.fromisoformat(b["date"])
            if (date.today() - rec_date).days > 1:
                return 0.0
        except Exception:
            return 0.0
        return 0.25 if b.get("bullish") else -0.25

    # ── FII/DII Data ──────────────────────────────────────────────────────────

    def update_fii_data(self, fii_net_crore: float, dii_net_crore: float) -> None:
        """Record FII/DII net activity for today."""
        self._data["fii_dii"] = {
            "date":     date.today().isoformat(),
            "fii_net":  round(float(fii_net_crore), 0),
            "dii_net":  round(float(dii_net_crore), 0),
        }
        self._save()

    def get_fii_buy_confidence_penalty(self) -> float:
        """
        Returns a confidence penalty (0.0-0.15) for BUY signals.
        If FII net sellers > ₹2000 Cr: -0.15 to all BUY confidence scores.
        """
        fd = self._data.get("fii_dii", {})
        if not fd:
            return 0.0
        try:
            rec_date = date.fromisoformat(fd["date"])
            if (date.today() - rec_date).days > 1:
                return 0.0
        except Exception:
            return 0.0
        fii_net = float(fd.get("fii_net", 0))
        if fii_net < -FII_SELL_THRESHOLD:
            return 0.15   # penalise BUY signals
        if fii_net > FII_SELL_THRESHOLD:
            return -0.05  # boost BUY signals (FII buyers)
        return 0.0

    def fetch_fii_data_from_nse(self) -> bool:
        """
        Attempt to fetch FII/DII provisional data from NSE.
        Returns True if successful.
        Note: NSE provisional data available ~4:00 PM each day.
        """
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept":     "application/json",
                "Referer":    "https://www.nseindia.com/",
            }
            session = requests.Session()
            try:
                from nse_proxy import apply as _apply_nse_proxy; _apply_nse_proxy(session)
            except Exception: pass
            session.get("https://www.nseindia.com/", headers=headers, timeout=5)
            resp = session.get(
                "https://www.nseindia.com/api/fiidiiTradeReact",
                headers=headers, timeout=10,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            # Parse FII/DII net figures from response
            for row in data:
                if "FII" in str(row.get("category", "")).upper():
                    fii_net = float(str(row.get("netPurchasesSales", "0")).replace(",", ""))
                elif "DII" in str(row.get("category", "")).upper():
                    dii_net = float(str(row.get("netPurchasesSales", "0")).replace(",", ""))
            self.update_fii_data(fii_net, dii_net)
            logger.info("FII/DII fetched: FII=₹%.0f Cr DII=₹%.0f Cr", fii_net, dii_net)
            return True
        except Exception as exc:
            logger.debug("FII fetch failed: %s", exc)
            return False

    # ── Sector Rotation ───────────────────────────────────────────────────────

    def update_relative_strength(
        self,
        performances: Dict[str, float],  # {"NIFTY": 0.012, "BANKNIFTY": 0.025, ...}
    ) -> None:
        """Record 5-day relative performance of indices."""
        self._data["relative_strength"] = {
            "date": date.today().isoformat(),
            "perf": performances,
        }
        self._save()

    def get_strongest_index(self) -> str:
        """Returns the best-performing index symbol over 5 days."""
        rs  = self._data.get("relative_strength", {})
        if not rs:
            return "NIFTY"
        perfs = rs.get("perf", {})
        if not perfs:
            return "NIFTY"
        return max(perfs, key=perfs.get)

    def get_index_score_boost(self, symbol: str) -> float:
        """
        Returns +0.30 if this symbol is the strongest index, 0.0 otherwise.
        Encourages routing to the currently trending index.
        """
        strongest = self.get_strongest_index()
        return 0.30 if symbol.upper() == strongest.upper() else 0.0

    # ── Max Pain Integration ──────────────────────────────────────────────────

    def get_max_pain_signal_modifier(
        self,
        spot:      float,
        max_pain:  float,
        direction: str,     # "BUY" or "SELL"
    ) -> float:
        """
        On expiry day: adjust signal score based on max pain gravity.
        Near max pain → signals that point toward max pain are boosted.
        """
        distance = spot - max_pain
        abs_dist = abs(distance)

        if abs_dist > MAX_PAIN_BOOST_RANGE:
            return 0.0   # too far from max pain, no effect

        # Toward max pain
        toward_pain = (distance > 0 and direction == "SELL") or \
                      (distance < 0 and direction == "BUY")

        if toward_pain:
            # Closer to max pain = higher boost
            closeness = 1.0 - abs_dist / MAX_PAIN_BOOST_RANGE
            return round(1.0 * closeness, 2)
        else:
            # Signal pushing away from max pain = penalty
            return -0.50

    # ── Combined Signal Bias ──────────────────────────────────────────────────

    def get_signal_bias(
        self,
        direction:  str,
        strategy:   str,
        symbol:     str = "NIFTY",
        spot:       Optional[float] = None,
        max_pain:   Optional[float] = None,
        is_expiry:  bool = False,
    ) -> float:
        """
        Combined score modifier from all context signals.
        Positive = boost, Negative = penalty.
        Used in signal_engine to adjust final score.
        """
        total = 0.0

        # VIX direction
        total += self.get_vix_strategy_bias(strategy)

        # Previous day bias
        prev_bias = self.get_prev_day_bias(symbol)
        if direction == "BUY":
            total += prev_bias
        else:
            total -= prev_bias   # bearish bias boosts SELL

        # FII penalty for BUY signals
        if direction == "BUY":
            total -= self.get_fii_buy_confidence_penalty()

        # Sector rotation boost
        total += self.get_index_score_boost(symbol)

        # Max pain (expiry day only)
        if is_expiry and spot and max_pain:
            total += self.get_max_pain_signal_modifier(spot, max_pain, direction)

        return round(total, 3)

    def status_summary(self) -> Dict[str, Any]:
        return {
            "vix_direction":     self.get_vix_direction(),
            "vix_spike_blocking": self.is_vix_spike_blocking(),
            "strongest_index":   self.get_strongest_index(),
            "fii_penalty":       self.get_fii_buy_confidence_penalty(),
            "prev_day_bias_nifty": self.get_prev_day_bias("NIFTY"),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
_context: Optional[MarketContext] = None


def get_market_context() -> MarketContext:
    global _context
    if _context is None:
        _context = MarketContext()
    return _context
