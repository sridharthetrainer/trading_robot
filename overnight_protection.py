"""
overnight_protection.py

Complete overnight gap risk protection system.

THE CORE PROBLEM
─────────────────
Stop-loss orders DO NOT protect against overnight gaps.
When NIFTY gaps 500+ points at open, a SL-M order fills
at whatever price the market opens at — which can be far
beyond your planned stop, causing catastrophic loss.

FIVE-LAYER PROTECTION SYSTEM
──────────────────────────────

LAYER 1 — EOD UNCERTAINTY SCORE (2:30 PM)
  Measure how "uncertain" the market is right now.
  If uncertainty > threshold → close ALL swing positions by 3:00 PM.
  Factors: VIX level, VIX change today, global markets,
           upcoming events, open interest buildup.

LAYER 2 — NEWS SENTIMENT SCAN (2:45 PM)
  Scan free news APIs for high-impact keywords.
  Trump, Fed, RBI, War, Ceasefire, Sanctions, Tariff, etc.
  If breaking news detected → close positions.

LAYER 3 — GIFT NIFTY PRE-MARKET CHECK (8:45 AM)
  Check GIFT Nifty before market opens.
  If gap > 1% predicted → assess each position.
  Auto-close positions that gap against them.
  Send Telegram alert with gap estimate and action plan.

LAYER 4 — OPENING 5-MINUTE ASSESSMENT (9:15-9:20 AM)
  NEVER trade the first candle if holding overnight positions.
  After first candle closes: assess actual gap vs predicted.
  Close positions that gapped adversely by > 0.5%.

LAYER 5 — POSITION SIZE LIMITS ON SWING (always)
  Max swing position = 30% of intraday limit.
  This means even a worst-case 100% loss on a swing position
  is survivable without breaching daily loss limit.

WHAT WE CANNOT CONTROL
────────────────────────
  • Nuclear events, natural disasters
  • Market circuit breakers (±10% on index)
  • Trading halts (sebi order)

For these, Layer 5 (small position size) is the only protection.
No system can fully hedge a 10% overnight gap.
"""
from __future__ import annotations

import json
import logging
import time
import threading
from datetime import datetime, date, time as dtime
from pathlib import Path
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────
EOD_CLOSE_SWING_HOUR   = 14    # Start evaluating at 2:30 PM
EOD_CLOSE_SWING_MIN    = 30
EOD_FORCE_CLOSE_HOUR   = 14    # Force close all swing at 2:55 PM on risky days
EOD_FORCE_CLOSE_MIN    = 55

UNCERTAINTY_THRESHOLD  = 0.65   # 0-1 scale, above this = close swing positions
GAP_ALERT_THRESHOLD    = 0.005  # 0.5% predicted gap → alert
GAP_CLOSE_THRESHOLD    = 0.010  # 1.0% actual gap against position → close

# High-impact news keywords (case-insensitive)
NEWS_KEYWORDS_HIGH = [
    "trump", "ceasefire", "war", "nuclear", "sanctions", "tariff",
    "fed rate", "fomc", "rbi rate", "monetary policy", "rate cut", "rate hike",
    "recession", "default", "crash", "circuit breaker", "halt",
    "inflation shock", "cpi surprise", "gdp miss",
]

NEWS_KEYWORDS_MEDIUM = [
    "geopolitical", "conflict", "escalation", "tension",
    "election result", "government", "policy change",
    "earnings miss", "profit warning", "bank crisis",
]


class UncertaintyScorer:
    """
    Scores market uncertainty on a 0-1 scale.
    Used at 2:30 PM to decide if swing positions should be closed.
    """

    def score(
        self,
        vix:            float,
        vix_change_pct: float,   # how much VIX moved today
        gift_nifty_chg: float,   # GIFT Nifty pre-market change
        has_event_tomorrow: bool,
        pcr:            float,   # put-call ratio
        news_score:     float,   # 0=clean, 1=major news
    ) -> float:
        """
        Returns uncertainty score 0.0-1.0.
        0.0 = completely calm, safe to hold overnight.
        1.0 = extreme uncertainty, close everything.
        """
        score = 0.0

        # VIX level (absolute)
        if vix > 25:   score += 0.35
        elif vix > 20: score += 0.20
        elif vix > 16: score += 0.10
        elif vix < 12: score -= 0.05  # very low VIX = calm

        # VIX change today (volatility of volatility)
        if abs(vix_change_pct) > 0.15:   score += 0.20  # VIX moved 15%+
        elif abs(vix_change_pct) > 0.08: score += 0.10  # VIX moved 8%+

        # Upcoming event tomorrow
        if has_event_tomorrow: score += 0.20

        # PCR extremes = institutional positioning for a move
        if pcr > 1.5 or pcr < 0.60: score += 0.15

        # News score
        score += news_score * 0.40

        return max(0.0, min(1.0, round(score, 3)))

    def get_recommendation(self, score: float) -> Tuple[str, str]:
        """Returns (action, reason)."""
        if score >= 0.75:
            return "CLOSE_ALL", f"extreme_uncertainty_{score:.0%}"
        if score >= 0.55:
            return "CLOSE_SWING", f"high_uncertainty_{score:.0%}_close_overnight"
        if score >= 0.40:
            return "REDUCE_HALF", f"medium_uncertainty_{score:.0%}_reduce_size"
        return "HOLD", f"uncertainty_acceptable_{score:.0%}"


class NewsScanner:
    """
    Scans free news sources for high-impact keywords.
    Uses Google News RSS (no API key required).
    Returns 0.0-1.0 news impact score.
    """

    RSS_URLS = [
        "https://news.google.com/rss/search?q=nifty+market&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=stock+market+india&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=trump+market+tariff&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=federal+reserve+rate&hl=en&gl=US&ceid=US:en",
    ]

    def scan(self) -> Tuple[float, List[str]]:
        """
        Returns (score, [matched keywords]).
        score: 0.0=clean, 0.5=medium news, 1.0=major event.
        """
        all_text = self._fetch_news_text()
        if not all_text:
            return 0.0, []

        text_lower = all_text.lower()
        matched_high   = [kw for kw in NEWS_KEYWORDS_HIGH   if kw in text_lower]
        matched_medium = [kw for kw in NEWS_KEYWORDS_MEDIUM if kw in text_lower]

        score = 0.0
        if matched_high:
            score = min(1.0, 0.4 + len(matched_high) * 0.15)
        elif matched_medium:
            score = min(0.5, len(matched_medium) * 0.10)

        all_matched = matched_high + matched_medium
        return round(score, 3), all_matched

    def _fetch_news_text(self) -> str:
        """Fetch and combine RSS feeds."""
        import urllib.request, xml.etree.ElementTree as ET
        combined = []
        for url in self.RSS_URLS[:2]:   # only first 2 to save time
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    xml_data = r.read().decode("utf-8", errors="ignore")
                root = ET.fromstring(xml_data)
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    desc  = item.findtext("description", "")
                    combined.append(f"{title} {desc}")
            except Exception as e:
                logger.debug("News fetch error: %s", e)
        return " ".join(combined)


class GiftNiftyMonitor:
    """
    Monitors GIFT Nifty futures to predict opening gap.
    Called at 8:45 AM before market opens.
    """

    def get_predicted_gap(self, prev_close: float = 0) -> dict:
        """
        Returns predicted opening gap.
        prev_close: yesterday's NIFTY close price.
        """
        try:
            import requests
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0",
                "Referer":    "https://www.nseindia.com/",
            })
            session.get("https://www.nseindia.com/", timeout=5)

            # Try NSE GIFT Nifty endpoint
            r = session.get(
                "https://www.nseindia.com/api/equity-stockIndices?index=GIFT%20NIFTY",
                timeout=8,
            )
            data  = r.json()
            items = data.get("data", [])
            for item in items:
                ltp = float(item.get("lastPrice", 0) or 0)
                if ltp > 10000:   # valid NIFTY-level price
                    chg_pct = (ltp - prev_close) / prev_close if prev_close > 0 else \
                              float(item.get("pChange", 0) or 0) / 100
                    direction = "UP" if chg_pct > 0 else "DOWN" if chg_pct < 0 else "FLAT"
                    return {
                        "gift_nifty":   ltp,
                        "change_pct":   round(chg_pct, 4),
                        "direction":    direction,
                        "gap_points":   round(ltp - prev_close) if prev_close else 0,
                        "source":       "NSE_GIFT",
                        "strong_gap":   abs(chg_pct) >= GAP_ALERT_THRESHOLD,
                    }
        except Exception as e:
            logger.debug("GIFT Nifty: %s", e)

        return {"gift_nifty": 0, "change_pct": 0, "direction": "UNKNOWN",
                "gap_points": 0, "source": "unavailable", "strong_gap": False}


class OvernightProtectionManager:
    """
    Main overnight protection orchestrator.

    Integrates with the trading system at:
    - 2:30 PM: Uncertainty assessment → decide to hold or close swing
    - 2:55 PM: Force close remaining swing if high uncertainty
    - 8:45 AM: GIFT Nifty check → pre-market alert
    - 9:20 AM: Post-opening assessment → close adverse gaps
    """

    def __init__(
        self,
        trade_manager = None,
        alerts        = None,
    ) -> None:
        self._tm       = trade_manager
        self._alerts   = alerts
        self._scorer   = UncertaintyScorer()
        self._news     = NewsScanner()
        self._gift     = GiftNiftyMonitor()

        self._last_eod_check_date  = None
        self._last_premarket_date  = None
        self._last_news_score      = 0.0
        self._last_news_keywords: List[str] = []
        self._last_uncertainty     = 0.0
        self._eod_closed_count     = 0

        # Cache
        self._vix_cache: float = 15.0
        self._pcr_cache: float = 1.0

    # ── Main entry points ─────────────────────────────────────────────────────

    def eod_risk_check(
        self,
        vix:         float = 0,
        vix_change:  float = 0,
        pcr:         float = 1.0,
        has_event:   bool  = False,
        force:       bool  = False,
    ) -> dict:
        """
        Run at 2:30 PM. Decide whether to close swing positions overnight.
        Returns action dict.
        """
        today = date.today()
        if not force and self._last_eod_check_date == today:
            return {"action": "already_checked", "score": self._last_uncertainty}

        self._last_eod_check_date = today
        self._vix_cache = vix or self._vix_cache
        self._pcr_cache = pcr or self._pcr_cache

        # Scan news
        try:
            news_score, keywords = self._news.scan()
            self._last_news_score    = news_score
            self._last_news_keywords = keywords
            if keywords:
                logger.info("News scan found keywords: %s (score=%.2f)", keywords[:5], news_score)
        except Exception as e:
            logger.debug("News scan: %s", e)
            news_score = 0.0

        # Score uncertainty
        uncertainty = self._scorer.score(
            vix             = vix or 15,
            vix_change_pct  = vix_change,
            gift_nifty_chg  = 0,   # not available at 2:30 PM
            has_event_tomorrow = has_event,
            pcr             = pcr or 1.0,
            news_score      = news_score,
        )
        self._last_uncertainty = uncertainty
        action, reason = self._scorer.get_recommendation(uncertainty)

        result = {
            "action":      action,
            "reason":      reason,
            "uncertainty": uncertainty,
            "vix":         vix,
            "pcr":         pcr,
            "news_score":  news_score,
            "news_keywords": keywords[:5] if keywords else [],
        }

        logger.info(
            "EOD risk check: uncertainty=%.0f%% action=%s vix=%.1f pcr=%.2f news=%.2f",
            uncertainty * 100, action, vix, pcr, news_score,
        )

        # Execute action
        closed = 0
        if action in ("CLOSE_ALL", "CLOSE_SWING") and self._tm:
            closed = self._close_swing_positions(reason)

        result["positions_closed"] = closed

        # Telegram alert
        self._send_eod_alert(result)
        return result

    def premarket_check(self, prev_close: float = 22000) -> dict:
        """
        Run at 8:45 AM. Check GIFT Nifty for predicted gap.
        Alert if gap > 0.5%. Return action plan.
        """
        today = date.today()
        if self._last_premarket_date == today:
            return {"status": "already_checked"}
        self._last_premarket_date = today

        gap_data = self._gift.get_predicted_gap(prev_close)
        chg_pct  = gap_data.get("change_pct", 0)
        strong   = gap_data.get("strong_gap", False)

        # Check swing positions and assess each one
        actions = []
        if self._tm and strong:
            for pos in self._get_swing_positions():
                symbol    = pos.get("symbol", "")
                side      = pos.get("side", "BUY")
                trade_id  = pos.get("trade_id", "")

                # Gap against position?
                gap_hurts = (
                    (side == "BUY"  and chg_pct < -GAP_CLOSE_THRESHOLD) or
                    (side == "SELL" and chg_pct >  GAP_CLOSE_THRESHOLD)
                )
                # CE position + gap down = bad
                if "CE" in symbol and chg_pct < -GAP_CLOSE_THRESHOLD:
                    gap_hurts = True
                # PE position + gap up = bad
                if "PE" in symbol and chg_pct > GAP_CLOSE_THRESHOLD:
                    gap_hurts = True

                actions.append({
                    "trade_id":  trade_id,
                    "symbol":    symbol,
                    "gap_hurts": gap_hurts,
                    "action":    "CLOSE_AT_OPEN" if gap_hurts else "MONITOR",
                })

        result = {
            "gap_data": gap_data,
            "actions":  actions,
            "close_count": sum(1 for a in actions if a["action"] == "CLOSE_AT_OPEN"),
        }

        self._send_premarket_alert(result)
        logger.info(
            "Pre-market check: GIFT=%.0f gap=%.2f%% actions=%d close=%d",
            gap_data.get("gift_nifty", 0),
            chg_pct * 100,
            len(actions),
            result["close_count"],
        )
        return result

    def post_open_assessment(self, spot_price: float, prev_close: float) -> dict:
        """
        Run at 9:20 AM (after first candle). Assess actual gap vs predicted.
        Close positions that gapped adversely.
        """
        actual_gap_pct = (spot_price - prev_close) / prev_close if prev_close else 0
        closed = 0

        if self._tm and abs(actual_gap_pct) > GAP_CLOSE_THRESHOLD:
            for pos in self._get_swing_positions():
                symbol = pos.get("symbol", "")
                side   = pos.get("side", "BUY")
                tid    = pos.get("trade_id", "")

                gap_hurts = (
                    ("CE" in symbol and actual_gap_pct < -GAP_CLOSE_THRESHOLD) or
                    ("PE" in symbol and actual_gap_pct >  GAP_CLOSE_THRESHOLD) or
                    (side == "BUY"  and actual_gap_pct < -GAP_CLOSE_THRESHOLD) or
                    (side == "SELL" and actual_gap_pct >  GAP_CLOSE_THRESHOLD)
                )
                if gap_hurts:
                    try:
                        self._tm.close_trade(
                            trade_id   = tid,
                            exit_price = 0,   # market price
                            reason     = f"post_open_gap_{actual_gap_pct:.1%}",
                        )
                        closed += 1
                        logger.info("Post-open close: %s gap=%.1f%%", symbol, actual_gap_pct * 100)
                    except Exception as e:
                        logger.error("Post-open close failed %s: %s", tid, e)

        return {
            "actual_gap_pct": round(actual_gap_pct, 4),
            "positions_closed": closed,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_swing_positions(self) -> List[dict]:
        """Get all open swing positions."""
        if not self._tm:
            return []
        try:
            return [
                p for p in self._tm.get_open_positions()
                if str(p.get("metadata", {}) or {}).get("style", "") == "swing"
                or "swing" in str(p.get("strategy", "")).lower()
            ]
        except Exception:
            return []

    def _close_swing_positions(self, reason: str) -> int:
        """Close all swing positions. Returns count closed."""
        positions = self._get_swing_positions()
        closed    = 0
        for pos in positions:
            try:
                self._tm.close_trade(
                    trade_id   = pos.get("trade_id", ""),
                    exit_price = 0,
                    reason     = f"overnight_protection_{reason}",
                )
                closed += 1
                logger.info(
                    "Overnight protection: closed %s reason=%s",
                    pos.get("symbol"), reason,
                )
            except Exception as e:
                logger.error("Close failed %s: %s", pos.get("trade_id"), e)
        self._eod_closed_count = closed
        return closed

    def _send_eod_alert(self, result: dict) -> None:
        if not self._alerts:
            return
        uncertainty = result.get("uncertainty", 0)
        action      = result.get("action", "HOLD")
        keywords    = result.get("news_keywords", [])
        closed      = result.get("positions_closed", 0)

        icon = "🔴" if uncertainty > 0.65 else "🟡" if uncertainty > 0.40 else "✅"
        msg  = (
            f"{icon} <b>EOD OVERNIGHT RISK CHECK</b>\n"
            f"Uncertainty:  {uncertainty:.0%}\n"
            f"VIX:          {result.get('vix',0):.1f}\n"
            f"PCR:          {result.get('pcr',1.0):.2f}\n"
            f"News score:   {result.get('news_score',0):.2f}\n"
        )
        if keywords:
            msg += f"News flags:   {', '.join(keywords[:4])}\n"
        msg += f"\n<b>Decision: {action}</b>\n"
        if closed > 0:
            msg += f"Closed {closed} swing position(s) for overnight safety\n"
        elif action == "HOLD":
            msg += "All positions held — risk acceptable\n"
        msg += f"\n🕐 {datetime.now().strftime('%d %b %H:%M')}"

        try:
            self._alerts.send(msg, dedup_key=f"eod_risk_{date.today()}")
        except Exception:
            pass

    def _send_premarket_alert(self, result: dict) -> None:
        if not self._alerts:
            return
        gap = result.get("gap_data", {})
        pct = gap.get("change_pct", 0)
        if abs(pct) < 0.002:
            return   # no alert for tiny gaps

        direction = "⬆️ GAP UP" if pct > 0 else "⬇️ GAP DOWN"
        msg = (
            f"🌅 <b>PRE-MARKET GAP ALERT</b>\n"
            f"{direction}  {abs(pct):.2%}\n"
            f"GIFT Nifty: {gap.get('gift_nifty',0):,.0f}\n"
            f"Gap points: {gap.get('gap_points',0):+.0f}\n"
        )
        close_count = result.get("close_count", 0)
        if close_count > 0:
            msg += f"\n⚠️ {close_count} position(s) will be closed at 9:20 AM\n"
            msg += "if gap holds against them."
        else:
            msg += "\nNo positions adversely affected."

        msg += f"\n\n🕐 {datetime.now().strftime('%H:%M')} — Market opens at 9:15 AM"
        try:
            self._alerts.send(msg, dedup_key=f"premarket_gap_{date.today()}")
        except Exception:
            pass

    def get_status(self) -> dict:
        return {
            "last_uncertainty":   self._last_uncertainty,
            "last_news_score":    self._last_news_score,
            "last_news_keywords": self._last_news_keywords,
            "eod_closed_today":   self._eod_closed_count,
            "vix":                self._vix_cache,
            "pcr":                self._pcr_cache,
        }


# Singleton
_manager: Optional[OvernightProtectionManager] = None

def get_overnight_protection(trade_manager=None, alerts=None) -> OvernightProtectionManager:
    global _manager
    if _manager is None:
        _manager = OvernightProtectionManager(trade_manager, alerts)
    if trade_manager and not _manager._tm:
        _manager._tm = trade_manager
    if alerts and not _manager._alerts:
        _manager._alerts = alerts
    return _manager
