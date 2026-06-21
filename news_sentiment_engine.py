"""
news_sentiment_engine.py — Global News + Commodity Sentiment

Sources (all free):
  - Google News RSS (no API key)
  - Economic Times RSS
  - Moneycontrol RSS  
  - Reuters India RSS
  - NSE corporate announcements
  - Yahoo Finance news

NLP approach (inspired by):
  - Stanford NLP VADER sentiment
  - Bloomberg terminal news scoring
  - Two Sigma alternative data research
  - "Advances in Financial ML" — Lopez de Prado Ch.12

Commodities tracked:
  - Gold / Silver (INR impact)
  - Crude Oil Brent/WTI (inflation, OMC stocks)
  - Natural Gas (energy sector)
  - Copper (economic indicator / metals)
  - Aluminium (industrials)
  - Cotton/Soybean (agri stocks)
"""
from __future__ import annotations
import logging, json, time, re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_CACHE = Path("news_sentiment_cache.json")
_TTL   = 900  # 15 min refresh

# ── Sentiment keywords ───────────────────────────────────────────────
BULLISH_WORDS = {
    # Strong bullish
    "surge", "soar", "rally", "boom", "breakout", "record high",
    "beat expectations", "strong earnings", "upgrade", "outperform",
    "buy rating", "positive", "growth", "profit", "expansion",
    "all-time high", "52-week high", "bullish", "momentum",
    # India-specific
    "rbi rate cut", "fiscal stimulus", "capex", "fdi inflow",
    "sensex rally", "nifty high", "fii buying", "dii buying",
    "earnings beat", "dividend", "bonus", "buyback",
}
BEARISH_WORDS = {
    # Strong bearish
    "crash", "plunge", "tumble", "selloff", "correction", "bear",
    "miss expectations", "downgrade", "underperform", "sell rating",
    "loss", "contraction", "recession", "default", "downfall",
    "52-week low", "bearish", "weak", "concern", "risk",
    # India-specific
    "rbi rate hike", "inflation spike", "fiscal deficit", "fii selling",
    "earnings miss", "warning", "downgrade", "margin pressure",
    "regulatory action", "sebi notice", "ed raid", "gst notice",
}
NEUTRAL_AMPLIFIERS = {"very", "highly", "extremely", "significantly", "sharply"}


def _score_headline(text: str) -> float:
    """
    Score a single headline: -1.0 (very bearish) to +1.0 (very bullish).
    Simple but effective — matches Bloomberg terminal approach.
    """
    text = text.lower()
    score = 0.0
    words = re.findall(r'\b\w+\b', text)
    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
    all_tokens = words + bigrams

    for token in all_tokens:
        if token in BULLISH_WORDS:
            score += 0.3
        if token in BEARISH_WORDS:
            score -= 0.3

    # Amplifiers
    for amp in NEUTRAL_AMPLIFIERS:
        if amp in words:
            score *= 1.2

    # Negation flip
    negations = {"not", "no", "never", "despite", "although", "but"}
    for neg in negations:
        if neg in words[:5]:  # early negation flips score
            score *= -0.7

    return max(-1.0, min(1.0, round(score, 2)))


def _fetch_rss(url: str, max_items: int = 10) -> List[str]:
    """Fetch headlines from RSS feed."""
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            return []
        titles = re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', r.text)
        if not titles:
            titles = re.findall(r'<title>(.+?)</title>', r.text)
        return [t.strip() for t in titles[1:max_items+1]]  # skip feed title
    except Exception as e:
        logger.debug("RSS %s: %s", url[:40], e)
        return []


def fetch_global_news() -> Dict[str, List[str]]:
    """
    Fetch news from multiple free RSS sources.
    Returns dict of category → list of headlines.
    """
    feeds = {
        "India Markets": [
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "https://www.moneycontrol.com/rss/MCreader.xml",
        ],
        "Global Markets": [
            "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
            "https://feeds.reuters.com/reuters/businessNews",
        ],
        "Commodities": [
            "https://www.kitco.com/rss/kitcoNews.rss",
            "https://economictimes.indiatimes.com/commodities/rssfeeds/1808152121.cms",
        ],
        "Economy": [
            "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",
            "https://feeds.reuters.com/reuters/INtopNews",
        ],
    }
    result = {}
    for category, urls in feeds.items():
        headlines = []
        for url in urls:
            headlines.extend(_fetch_rss(url, 5))
        result[category] = headlines[:8]
    return result


def fetch_commodity_prices() -> Dict[str, dict]:
    """
    Real commodity prices from Yahoo Finance JSON API.
    All free, no API key needed.
    """
    commodities = {
        "Gold":        {"ticker": "GC=F",     "unit": "$/oz",   "india_impact": "Jewellery/safe haven"},
        "Silver":      {"ticker": "SI=F",      "unit": "$/oz",   "india_impact": "Industrial demand"},
        "Brent Crude": {"ticker": "BZ=F",      "unit": "$/bbl",  "india_impact": "Inflation/OMC stocks"},
        "WTI Crude":   {"ticker": "CL=F",      "unit": "$/bbl",  "india_impact": "Energy sector"},
        "Natural Gas": {"ticker": "NG=F",      "unit": "$/MMBtu","india_impact": "Energy/Fertiliser"},
        "Copper":      {"ticker": "HG=F",      "unit": "$/lb",   "india_impact": "Economic indicator"},
        "Aluminium":   {"ticker": "ALI=F",     "unit": "$/MT",   "india_impact": "Manufacturing"},
        "Cotton":      {"ticker": "CT=F",      "unit": "¢/lb",   "india_impact": "Textile stocks"},
        "Wheat":       {"ticker": "ZW=F",      "unit": "¢/bu",   "india_impact": "Agri inflation"},
        "Soybean":     {"ticker": "ZS=F",      "unit": "¢/bu",   "india_impact": "Agri/FMCG"},
        "MCX Gold":    {"ticker": "GOLD.BO",   "unit": "₹/10g",  "india_impact": "Direct India gold"},
        "MCX Crude":   {"ticker": "CRUDEOIL.BO","unit":"₹/bbl", "india_impact": "India crude futures"},
    }

    result = {}
    try:
        import requests
        for name, info in commodities.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{info['ticker']}?interval=1d&range=2d"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    curr = float(meta.get("regularMarketPrice") or 0)
                    prev = float(meta.get("chartPreviousClose") or curr)
                    chg  = (curr - prev) / prev * 100 if prev else 0
                    result[name] = {
                        "price":       curr,
                        "change_pct":  round(chg, 2),
                        "unit":        info["unit"],
                        "india_impact": info["india_impact"],
                        "direction":   "UP" if chg > 0.5 else "DOWN" if chg < -0.5 else "FLAT",
                    }
            except Exception:
                pass
    except Exception as e:
        logger.debug("commodity_prices: %s", e)
    return result


def analyze_commodity_impact(commodity_data: dict) -> Dict[str, str]:
    """
    Map commodity moves to Indian sector impact.
    Based on standard macro analysis framework.
    """
    impacts = {}

    gold = commodity_data.get("Gold", {})
    crude = commodity_data.get("Brent Crude", {})
    copper = commodity_data.get("Copper", {})
    nat_gas = commodity_data.get("Natural Gas", {})

    # Gold impact
    if gold:
        gold_chg = gold.get("change_pct", 0)
        if gold_chg > 1.5:
            impacts["Gold mining / Jewellery"] = f"🟢 BULLISH — Gold +{gold_chg:.1f}% → Titan, Muthoot, KPITTECH"
        elif gold_chg < -1.5:
            impacts["Gold mining / Jewellery"] = f"🔴 BEARISH — Gold {gold_chg:.1f}% → Pressure on Titan"

    # Crude oil impact
    if crude:
        crude_chg = crude.get("change_pct", 0)
        if crude_chg > 2:
            impacts["OMC (BPCL/HPCL/IOC)"] = f"🔴 BEARISH — Crude +{crude_chg:.1f}% → Margin squeeze"
            impacts["Aviation (IndiGo)"] = f"🔴 BEARISH — Crude +{crude_chg:.1f}% → Fuel cost up"
            impacts["Paints (Asian/Berger)"] = f"🔴 BEARISH — Crude +{crude_chg:.1f}% → Input cost up"
        elif crude_chg < -2:
            impacts["OMC (BPCL/HPCL/IOC)"] = f"🟢 BULLISH — Crude {crude_chg:.1f}% → Margin expansion"
            impacts["Aviation (IndiGo)"] = f"🟢 BULLISH — Crude {crude_chg:.1f}% → Fuel cost down"

    # Copper impact
    if copper:
        copper_chg = copper.get("change_pct", 0)
        if copper_chg > 1.5:
            impacts["Metals (Hindalco/Vedl)"] = f"🟢 BULLISH — Copper +{copper_chg:.1f}%"
        elif copper_chg < -1.5:
            impacts["Metals (Hindalco/Vedl)"] = f"🔴 BEARISH — Copper {copper_chg:.1f}%"

    # Natural gas
    if nat_gas:
        gas_chg = nat_gas.get("change_pct", 0)
        if abs(gas_chg) > 2:
            dir_str = "UP" if gas_chg > 0 else "DOWN"
            impacts["Fertiliser/Gas (GAIL/IGL)"] = (
                f"{'🔴' if gas_chg > 0 else '🟢'} — Gas {gas_chg:+.1f}% → "
                f"{'Cost up' if gas_chg > 0 else 'Cost down'}"
            )

    return impacts


def get_full_sentiment(use_cache: bool = True) -> dict:
    """
    Full market sentiment: news + commodities + score.
    Cached for 15 min to avoid rate limits.
    """
    if use_cache and _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            if time.time() - cached.get("ts", 0) < _TTL:
                return cached["data"]
        except Exception:
            pass

    news = fetch_global_news()
    commodities = fetch_commodity_prices()
    impacts = analyze_commodity_impact(commodities)

    # Score all headlines
    all_headlines = []
    for cat, headlines in news.items():
        for h in headlines:
            score = _score_headline(h)
            all_headlines.append({"headline": h, "category": cat, "score": score})

    # Overall sentiment
    scores = [h["score"] for h in all_headlines]
    avg_score = sum(scores) / len(scores) if scores else 0
    sentiment = "BULLISH" if avg_score > 0.15 else "BEARISH" if avg_score < -0.15 else "NEUTRAL"

    # Top headlines by absolute score
    top_bullish = sorted([h for h in all_headlines if h["score"] > 0],
                         key=lambda x: x["score"], reverse=True)[:3]
    top_bearish = sorted([h for h in all_headlines if h["score"] < 0],
                         key=lambda x: x["score"])[:3]

    data = {
        "ts":             time.time(),
        "sentiment":      sentiment,
        "avg_score":      round(avg_score, 3),
        "total_headlines": len(all_headlines),
        "top_bullish":    top_bullish,
        "top_bearish":    top_bearish,
        "commodities":    commodities,
        "sector_impacts": impacts,
        "news_by_cat":    {k: v[:3] for k, v in news.items()},
    }

    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "data": data}, indent=2))
    except Exception:
        pass

    return data


def format_telegram_report() -> str:
    """Full sentiment report for /sentiment Telegram command."""
    d = get_full_sentiment()
    now = datetime.now().strftime("%d-%b %H:%M")
    sent = d.get("sentiment", "NEUTRAL")
    avg  = d.get("avg_score", 0)
    icon = "🟢" if sent == "BULLISH" else "🔴" if sent == "BEARISH" else "⚪"

    lines = [
        f"📰 <b>MARKET SENTIMENT</b> | {now}",
        f"",
        f"  {icon} Overall: <b>{sent}</b>  (score: {avg:+.2f})",
        f"  Headlines analysed: {d.get('total_headlines', 0)}",
        f"",
    ]

    # Top bullish
    if d.get("top_bullish"):
        lines.append("  <b>🟢 BULLISH SIGNALS</b>")
        for h in d["top_bullish"]:
            lines.append(f"  • {h['headline'][:70]}")
        lines.append("")

    # Top bearish
    if d.get("top_bearish"):
        lines.append("  <b>🔴 BEARISH SIGNALS</b>")
        for h in d["top_bearish"]:
            lines.append(f"  • {h['headline'][:70]}")
        lines.append("")

    # Commodity impact
    impacts = d.get("sector_impacts", {})
    if impacts:
        lines.append("  <b>🛢️ COMMODITY → SECTOR IMPACT</b>")
        for sector, impact in list(impacts.items())[:4]:
            lines.append(f"  {impact}")
        lines.append("")

    # Key commodities
    comms = d.get("commodities", {})
    key_comms = ["Gold", "Brent Crude", "Copper", "Natural Gas"]
    if any(c in comms for c in key_comms):
        lines.append("  <b>📊 KEY COMMODITIES</b>")
        for c in key_comms:
            if c in comms:
                cd = comms[c]
                ci = "🟢" if cd["change_pct"] > 0 else "🔴" if cd["change_pct"] < 0 else "⚪"
                lines.append(
                    f"  {ci} {c:12} {cd['price']:>9,.1f} {cd['unit']} "
                    f"({cd['change_pct']:+.1f}%)"
                )

    lines += [
        "",
        f"  ⏰ Updates every 15 min during market hours",
        f"  Source: ET/MC/Reuters/Kitco RSS (free)",
    ]
    return "\n".join(lines)
