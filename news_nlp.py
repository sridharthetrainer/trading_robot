"""
news_nlp.py  —  News NLP Scoring Engine

Uses your existing NewsAPI key (NEWS_API_KEY in .env).
Scores news headlines for: bullish/bearish sentiment per sector/symbol.

SIGNALS:
  RBI dovish language (+score for banking/indices)
  Earnings beat keywords (+score for that stock)
  FII selling headline (-score)
  Budget sector allocation (+/- by sector)
  Geopolitical risk (-score market-wide)

NLP METHOD: Simple but effective — weighted keyword scoring.
No external NLP library needed. Works offline with cached news.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
_CACHE = Path("news_nlp_cache.json")
_TTL   = 1800   # 30 min refresh


# ── Keyword dictionaries ──────────────────────────────────────────────────────

BULLISH_WORDS = {
    # Macro bullish
    "rate cut": 2.0, "dovish": 1.8, "stimulus": 1.5, "gdp growth": 1.5,
    "record high": 1.2, "beat estimates": 1.8, "profit surge": 1.8,
    "strong demand": 1.3, "upgrade": 1.5, "outperform": 1.5,
    "buyback": 1.6, "dividend": 1.2, "bonus": 1.2,
    "fii buying": 2.0, "inflow": 1.5, "dii buying": 1.5,
    "recovery": 1.0, "expansion": 1.2, "rally": 1.0,
    "order win": 1.8, "contract win": 1.8, "new order": 1.5,
    "rbi holds": 0.8, "inflation eases": 1.5, "rupee strengthens": 1.2,
}

BEARISH_WORDS = {
    # Macro bearish
    "rate hike": -2.0, "hawkish": -1.8, "recession": -2.0,
    "miss estimates": -1.8, "profit fall": -1.8, "loss widens": -1.8,
    "downgrade": -1.5, "underperform": -1.5, "sell rating": -1.5,
    "fii selling": -2.0, "outflow": -1.5, "sell-off": -1.5,
    "inflation spike": -1.8, "rupee weakens": -1.2, "rupee falls": -1.2,
    "rbi hikes": -2.0, "sebi probe": -2.0, "fraud": -2.5,
    "ban": -1.5, "fine": -1.2, "penalty": -1.2,
    "geopolitical": -1.0, "war": -1.5, "sanctions": -1.5,
    "default": -2.5, "debt": -1.0, "npa": -1.8,
    "promoter pledge": -1.5, "promoter sell": -1.8,
}

# Sector-specific keywords
SECTOR_KEYWORDS = {
    "IT":      ["it sector", "tech", "software", "digital", "ai contract", "us recession"],
    "BANKING": ["rbi", "credit growth", "npa", "net interest margin", "nim"],
    "OMC":     ["crude", "petrol", "diesel", "brent", "opec", "oil"],
    "PHARMA":  ["fda", "drug approval", "usfda", "embargo", "api"],
    "AUTO":    ["ev", "electric vehicle", "sales data", "vahan", "auto sales"],
    "INFRA":   ["capex", "government spending", "roads", "pli scheme"],
}


# ── News Fetcher ──────────────────────────────────────────────────────────────

def _fetch_news(query: str, api_key: str) -> List[str]:
    """Fetch recent headlines for a query term."""
    try:
        import requests
        url = (f"https://newsapi.org/v2/everything"
               f"?q={query}&language=en&sortBy=publishedAt"
               f"&from={(datetime.now()-timedelta(days=1)).strftime('%Y-%m-%d')}"
               f"&pageSize=20&apiKey={api_key}")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            articles = r.json().get("articles", [])
            return [a.get("title","") + " " + (a.get("description") or "")
                    for a in articles]
    except Exception as e:
        logger.debug("News fetch: %s", e)
    return []


def _score_text(text: str) -> float:
    """Score a text block using keyword dictionaries."""
    text_l = text.lower()
    score  = 0.0
    for word, weight in BULLISH_WORDS.items():
        if word in text_l:
            score += weight
    for word, weight in BEARISH_WORDS.items():
        if word in text_l:
            score += weight   # weight is already negative
    return round(score, 2)


def _cached_market_news_score() -> Optional[float]:
    for path in (Path("news_sentiment_cache.json"), _CACHE):
        try:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            if "avg_score" in data:
                return float(data.get("avg_score", 0.0) or 0.0)
            if "weighted_score" in data:
                return float(data.get("weighted_score", 0.0) or 0.0)
            scores = []
            for key in ("top_bullish", "top_bearish"):
                vals = data.get(key, [])
                if isinstance(vals, list):
                    for item in vals:
                        if isinstance(item, dict) and "score" in item:
                            scores.append(float(item.get("score", 0.0) or 0.0))
            if scores:
                return sum(scores) / len(scores)
        except Exception:
            continue
    return None


# ── Main API ──────────────────────────────────────────────────────────────────

def get_news_sentiment(symbol: str, direction: str,
                        api_key: Optional[str] = None) -> float:
    """
    Score modifier from news sentiment for a symbol/direction.
    Returns float: positive = bullish, negative = bearish.
    """
    if not api_key:
        try:
            import config as cfg
            api_key = getattr(cfg, "NEWS_API_KEY", os.getenv("NEWS_API_KEY",""))
        except Exception:
            api_key = os.getenv("NEWS_API_KEY", "")

    if not api_key:
        cached_score = _cached_market_news_score()
        if cached_score is not None:
            return _direction_mod(float(cached_score), direction)
        return 0.0

    # Check cache
    cache_key = symbol.upper()
    try:
        if _CACHE.exists():
            cached = json.loads(_CACHE.read_text())
            entry  = cached.get(cache_key, {})
            if time.time() - entry.get("ts", 0) < _TTL:
                raw_score = float(entry.get("score", 0))
                return _direction_mod(raw_score, direction)
    except Exception:
        pass

    # Fetch fresh
    try:
        headlines = _fetch_news(symbol, api_key)
        if not headlines:
            # Try full name for indices
            name_map = {"NIFTY":"nifty india market","BANKNIFTY":"bank nifty india",
                        "FINNIFTY":"financial nifty"}
            headlines = _fetch_news(name_map.get(symbol.upper(), symbol), api_key)

        raw_score = 0.0
        for h in headlines:
            raw_score += _score_text(h)
        raw_score = round(raw_score / max(len(headlines), 1), 2)

        # Save cache
        try:
            cached = {}
            if _CACHE.exists():
                cached = json.loads(_CACHE.read_text())
            cached[cache_key] = {"ts": time.time(), "score": raw_score}
            _CACHE.write_text(json.dumps(cached))
        except Exception:
            pass

        return _direction_mod(raw_score, direction)
    except Exception as e:
        logger.debug("News sentiment: %s", e)
        return 0.0


def _direction_mod(raw_score: float, direction: str) -> float:
    """Convert raw news score to directional modifier."""
    # Cap at ±2.0
    raw_score = max(-2.0, min(2.0, raw_score))
    if direction == "BUY":
        return round(raw_score * 0.5, 2)    # positive news boosts BUY
    else:
        return round(-raw_score * 0.5, 2)   # negative news boosts SELL


def get_market_news_brief() -> str:
    """Quick market news summary for Telegram morning brief."""
    try:
        import os, config as cfg
        api_key = getattr(cfg, "NEWS_API_KEY", os.getenv("NEWS_API_KEY",""))
        if not api_key:
            return "News: API key not configured"
        headlines = _fetch_news("india stock market nifty", api_key)[:5]
        if not headlines:
            return "News: No recent headlines"
        score = sum(_score_text(h) for h in headlines) / len(headlines)
        sentiment = "🟢 Bullish" if score > 0.5 else "🔴 Bearish" if score < -0.5 else "⚪ Neutral"
        return f"📰 News: {sentiment} (score={score:+.1f})"
    except Exception:
        return "News: unavailable"
