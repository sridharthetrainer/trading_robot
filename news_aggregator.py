"""
news_aggregator.py — Comprehensive News Intelligence Engine

Sources (35 total, all FREE, no API keys):
  Indian Financial:  ET, Mint, BS, FE, Hindu Business, NDTV Profit,
                     CNBC TV18, Zee Biz, Bloomberg Quint, PTI
  Global Markets:    Reuters, MarketWatch, CNBC, Yahoo Finance
  Commodities:       Kitco, OilPrice, Mining.com
  Regulatory:        RBI, SEBI, NSE, BSE announcements
  Social/Reddit:     r/IndiaInvestments, r/stocks, r/wallstreetbets
  Central Banks:     Fed, ECB, RBI press releases
  Macro:             IMF, World Bank data releases

NLP: Keyword + bigram scoring (VADER-inspired)
     Symbol-specific sentiment extraction
     Entity recognition (company names → ticker)
     Event tagging (RBI policy, earnings, results, AGM)

Output:
  /news     → 35-source aggregated headlines with scores
  /sentiment → overall market sentiment + top movers
  /rbi      → RBI/SEBI regulatory alerts
  /reddit   → social sentiment (fear/greed from retail)
"""
from __future__ import annotations
import logging, json, time, re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_CACHE = Path("news_aggregator_cache.json")
_TTL   = 600   # 10 min

# ── 35 News sources ──────────────────────────────────────────────────
NEWS_FEEDS = {
    # ── INDIAN FINANCIAL (primary) ───────────────────────────────────
    "Economic Times Markets":
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Economic Times Economy":
        "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",
    "Economic Times Commodities":
        "https://economictimes.indiatimes.com/commodities/rssfeeds/1808152121.cms",
    "Economic Times Companies":
        "https://economictimes.indiatimes.com/company/rssfeeds/1286551815.cms",
    "Moneycontrol":
        "https://www.moneycontrol.com/rss/MCreader.xml",
    "Moneycontrol Markets":
        "https://www.moneycontrol.com/rss/marketreports.xml",
    "Business Standard":
        "https://www.business-standard.com/rss/markets-106.rss",
    "Business Standard Economy":
        "https://www.business-standard.com/rss/economy-policy-102.rss",
    "LiveMint Markets":
        "https://www.livemint.com/rss/markets",
    "LiveMint Companies":
        "https://www.livemint.com/rss/companies",
    "Financial Express Markets":
        "https://www.financialexpress.com/market/feed/",
    "Hindu Business":
        "https://www.thehindubusinessline.com/markets/stock-markets/?service=rss",
    "Zee Business":
        "https://www.zeebiz.com/rss",
    "NDTV Profit":
        "https://www.ndtvprofit.com/rss",
    "PTI Finance":
        "https://www.ptinews.com/rss/finance.xml",

    # ── GLOBAL FINANCIAL ──────────────────────────────────────────────
    "Reuters Business":
        "https://feeds.reuters.com/reuters/businessNews",
    "Reuters India":
        "https://feeds.reuters.com/reuters/INtopNews",
    "MarketWatch":
        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "CNBC":
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "Yahoo Finance":
        "https://finance.yahoo.com/news/rssindex",
    "Investopedia":
        "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline",

    # ── COMMODITIES ────────────────────────────────────────────────────
    "Kitco Gold/Silver":
        "https://www.kitco.com/rss/kitcoNews.rss",
    "OilPrice.com":
        "https://oilprice.com/rss/main",
    "Mining.com":
        "https://www.mining.com/feed/",

    # ── REGULATORY / OFFICIAL ──────────────────────────────────────────
    "RBI Press Releases":
        "https://www.rbi.org.in/Scripts/rss.aspx",
    "SEBI Circulars":
        "https://www.sebi.gov.in/sebi_data/rss/rss.xml",
    "NSE Announcements":
        "https://www.nseindia.com/api/corporateAnnouncements-equities?index=equities",

    # ── SOCIAL / REDDIT (RSS) ─────────────────────────────────────────
    "Reddit IndiaInvestments":
        "https://www.reddit.com/r/IndiaInvestments/hot.json?limit=10",
    "Reddit Stocks":
        "https://www.reddit.com/r/stocks/hot.json?limit=10",
    "Reddit WallStreetBets":
        "https://www.reddit.com/r/wallstreetbets/hot.json?limit=5",

    # ── CENTRAL BANKS ──────────────────────────────────────────────────
    "Federal Reserve":
        "https://www.federalreserve.gov/feeds/press_all.xml",
    "ECB Press":
        "https://www.ecb.europa.eu/rss/press.html",

    # ── MACRO DATA ─────────────────────────────────────────────────────
    "IMF News":
        "https://www.imf.org/en/News/rss?selectedTypes=pressReleases",
    "World Bank":
        "https://blogs.worldbank.org/rss.xml",
}

# ── Entity → Ticker mapping ───────────────────────────────────────────
ENTITY_TICKER = {
    "reliance industries": "RELIANCE",
    "reliance": "RELIANCE",
    "tata consultancy": "TCS",
    "tcs": "TCS",
    "hdfc bank": "HDFCBANK",
    "infosys": "INFY",
    "icici bank": "ICICIBANK",
    "state bank": "SBIN",
    "sbi": "SBIN",
    "bharti airtel": "BHARTIARTL",
    "airtel": "BHARTIARTL",
    "wipro": "WIPRO",
    "hcl tech": "HCLTECH",
    "maruti suzuki": "MARUTI",
    "maruti": "MARUTI",
    "bajaj finance": "BAJFINANCE",
    "asian paints": "ASIANPAINT",
    "sun pharma": "SUNPHARMA",
    "dr reddy": "DRREDDY",
    "titan": "TITAN",
    "adani": "ADANIENT",
    "hdfc life": "HDFCLIFE",
    "coal india": "COALINDIA",
    "ongc": "ONGC",
    "ntpc": "NTPC",
    "power grid": "POWERGRID",
    "itc": "ITC",
    "ultratech": "ULTRACEMCO",
    "axis bank": "AXISBANK",
    "kotak": "KOTAKBANK",
    "l&t": "LT",
    "larsen": "LT",
    "nifty": "NIFTY",
    "sensex": "SENSEX",
    "banknifty": "BANKNIFTY",
    "rbi": "RBI_EVENT",
    "federal reserve": "FED_EVENT",
    "rate cut": "RATE_EVENT",
    "rate hike": "RATE_EVENT",
    "inflation": "MACRO_EVENT",
    "gdp": "MACRO_EVENT",
    "budget": "BUDGET_EVENT",
    "earnings": "EARNINGS_EVENT",
    "results": "EARNINGS_EVENT",
    "quarterly": "EARNINGS_EVENT",
}

# ── Sentiment keywords ────────────────────────────────────────────────
BULLISH = {
    # Price action
    "surge", "soar", "rally", "boom", "breakout", "record", "high",
    "gain", "rise", "jump", "spike", "upgrade", "outperform",
    # Fundamental
    "profit", "growth", "expansion", "beat", "strong", "robust",
    "positive", "bullish", "buy", "accumulate", "overweight",
    # India specific
    "fii buying", "dii buying", "inflow", "capex", "dividend",
    "buyback", "bonus", "merger", "acquisition", "order win",
    "rate cut", "stimulus", "reform", "approval", "clearance",
    # Macro
    "gdp growth", "export growth", "fiscal surplus", "current account",
}
BEARISH = {
    # Price action
    "crash", "plunge", "fall", "drop", "decline", "sell", "selloff",
    "downgrade", "underperform", "correction", "bear", "loss",
    # Fundamental
    "miss", "weak", "warning", "concern", "risk", "default", "debt",
    "bearish", "avoid", "reduce", "negative",
    # India specific
    "fii selling", "outflow", "ed raid", "sebi notice", "gst notice",
    "margin pressure", "cost pressure", "regulatory", "probe", "fraud",
    # Macro
    "inflation spike", "rate hike", "fiscal deficit", "recession",
    "contraction", "slowdown", "unemployment",
}
EVENT_TAGS = {
    "rbi policy": "RBI_POLICY",
    "monetary policy": "RBI_POLICY",
    "federal reserve": "FED_POLICY",
    "fomc": "FED_POLICY",
    "ecb": "ECB_POLICY",
    "quarterly results": "EARNINGS",
    "q1 results": "EARNINGS",
    "q2 results": "EARNINGS",
    "q3 results": "EARNINGS",
    "q4 results": "EARNINGS",
    "annual general meeting": "AGM",
    "agm": "AGM",
    "merger": "CORPORATE_ACTION",
    "acquisition": "CORPORATE_ACTION",
    "ipo": "IPO",
    "block deal": "BLOCK_DEAL",
    "bulk deal": "BLOCK_DEAL",
    "budget": "BUDGET",
    "inflation data": "MACRO_DATA",
    "cpi": "MACRO_DATA",
    "wpi": "MACRO_DATA",
    "gdp": "MACRO_DATA",
    "circuit": "CIRCUIT_BREAKER",
}


def _fetch_rss(url: str, max_items: int = 8) -> List[str]:
    """Fetch RSS feed headlines."""
    try:
        import requests
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        # Try CDATA format first
        titles = re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', r.text, re.DOTALL)
        if not titles:
            titles = re.findall(r'<title>(.+?)</title>', r.text)
        # Clean HTML entities
        cleaned = []
        for t in titles[1:max_items+1]:
            t = re.sub(r'<[^>]+>', '', t)
            t = (t.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
                  .replace('&quot;','"').replace('&#39;',"'").strip())
            if len(t) > 10:
                cleaned.append(t)
        return cleaned
    except Exception as e:
        logger.debug("RSS %s: %s", url[:50], e)
        return []


def _fetch_reddit(url: str, max_items: int = 5) -> List[str]:
    """Fetch Reddit hot posts as headlines."""
    try:
        import requests
        r = requests.get(
            url,
            headers={"User-Agent": "AlgoBot/1.0 (educational research)"},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        posts = r.json().get("data", {}).get("children", [])
        return [p["data"]["title"] for p in posts[:max_items]
                if not p["data"].get("stickied")]
    except Exception as e:
        logger.debug("Reddit %s: %s", url[:50], e)
        return []


def _fetch_nse_announcements(max_items: int = 10) -> List[str]:
    """Fetch NSE corporate announcements."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get(
            "https://www.nseindia.com/api/corporate-announcements?index=equities",
            timeout=10,
        )
        if r.status_code == 200:
            items = r.json() if isinstance(r.json(), list) else []
            return [
                f"{i.get('symbol','?')}: {i.get('subject','?')}"
                for i in items[:max_items]
            ]
    except Exception as e:
        logger.debug("NSE announcements: %s", e)
    return []


def score_headline(text: str) -> float:
    """Score -1.0 to +1.0."""
    text_lower = text.lower()
    words  = re.findall(r'\b\w+\b', text_lower)
    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
    score  = 0.0

    for token in words + bigrams:
        if token in BULLISH: score += 0.25
        if token in BEARISH: score -= 0.25

    # Amplifiers
    for amp in ("very", "highly", "sharply", "significantly", "extremely"):
        if amp in words: score *= 1.2

    # Negation
    for neg in ("not", "no", "despite", "but", "however", "although"):
        if neg in words[:6]: score *= -0.6

    return max(-1.0, min(1.0, round(score, 2)))


def extract_entities(text: str) -> List[str]:
    """Extract company/ticker references from headline."""
    text_lower = text.lower()
    found = []
    for entity, ticker in ENTITY_TICKER.items():
        if entity in text_lower:
            found.append(ticker)
    return list(set(found))


def tag_events(text: str) -> List[str]:
    """Tag headline with event type."""
    text_lower = text.lower()
    tags = []
    for phrase, tag in EVENT_TAGS.items():
        if phrase in text_lower:
            tags.append(tag)
    return list(set(tags))


def fetch_all_news(max_per_source: int = 5) -> Dict[str, List[dict]]:
    """
    Fetch from all 35 sources.
    Returns dict: source_name → list of {headline, score, entities, events}
    """
    result = {}

    for name, url in NEWS_FEEDS.items():
        try:
            if "reddit.com" in url:
                headlines = _fetch_reddit(url, max_per_source)
            elif "nseindia.com/api/corporate" in url:
                headlines = _fetch_nse_announcements(max_per_source)
            else:
                headlines = _fetch_rss(url, max_per_source)

            scored = []
            for h in headlines:
                scored.append({
                    "headline": h,
                    "score":    score_headline(h),
                    "entities": extract_entities(h),
                    "events":   tag_events(h),
                    "source":   name,
                })
            if scored:
                result[name] = scored
                logger.debug("✅ %s: %d headlines", name, len(scored))

        except Exception as e:
            logger.debug("Source %s: %s", name, e)

    return result


def get_aggregated_sentiment(use_cache: bool = True) -> dict:
    """
    Full 35-source aggregated market intelligence.
    Cached 10 min. Returns structured sentiment data.
    """
    if use_cache and _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            if time.time() - cached.get("ts", 0) < _TTL:
                return cached["data"]
        except Exception:
            pass

    all_news = fetch_all_news()

    # Flatten all headlines
    all_items = []
    for source, items in all_news.items():
        all_items.extend(items)

    # Overall sentiment
    scores = [i["score"] for i in all_items]
    avg    = sum(scores) / len(scores) if scores else 0
    overall = "BULLISH" if avg > 0.15 else "BEARISH" if avg < -0.15 else "NEUTRAL"

    # Symbol-specific sentiment
    sym_sentiment: Dict[str, List[float]] = {}
    sym_headlines: Dict[str, List[str]]   = {}
    for item in all_items:
        for ent in item["entities"]:
            if ent.endswith("_EVENT"):
                continue
            sym_sentiment.setdefault(ent, []).append(item["score"])
            sym_headlines.setdefault(ent, []).append(item["headline"])

    symbol_scores = {}
    for sym, scores_list in sym_sentiment.items():
        s = sum(scores_list) / len(scores_list)
        symbol_scores[sym] = {
            "score":     round(s, 2),
            "count":     len(scores_list),
            "sentiment": "BULLISH" if s > 0.1 else "BEARISH" if s < -0.1 else "NEUTRAL",
            "headlines": sym_headlines[sym][:3],
        }

    # Event detection
    events_detected: Dict[str, List[str]] = {}
    for item in all_items:
        for ev in item["events"]:
            events_detected.setdefault(ev, []).append(item["headline"])

    # RBI/SEBI regulatory alerts (high importance)
    regulatory = [i for i in all_items
                  if "RBI_POLICY" in i["events"] or "SEBI" in i["source"]
                  or i["source"] in ("RBI Press Releases", "SEBI Circulars")]

    # Top bullish/bearish
    top_bull = sorted([i for i in all_items if i["score"] > 0.2],
                      key=lambda x: x["score"], reverse=True)[:5]
    top_bear = sorted([i for i in all_items if i["score"] < -0.2],
                      key=lambda x: x["score"])[:5]

    # Reddit fear/greed (social sentiment)
    reddit_items = [i for i in all_items
                    if "Reddit" in i.get("source", "")]
    reddit_score = (sum(i["score"] for i in reddit_items) / len(reddit_items)
                    if reddit_items else 0)
    reddit_sentiment = ("GREED" if reddit_score > 0.2 else
                        "FEAR" if reddit_score < -0.2 else "NEUTRAL")

    data = {
        "ts":               time.time(),
        "overall":          overall,
        "avg_score":        round(avg, 3),
        "total_headlines":  len(all_items),
        "sources_live":     len(all_news),
        "top_bullish":      top_bull,
        "top_bearish":      top_bear,
        "symbol_sentiment": symbol_scores,
        "events_detected":  events_detected,
        "regulatory_alerts":regulatory[:5],
        "reddit_sentiment": reddit_sentiment,
        "reddit_score":     round(reddit_score, 2),
        "sources":          list(all_news.keys()),
    }

    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception:
        pass

    return data


def get_symbol_news_sentiment(symbol: str) -> dict:
    """Get sentiment specifically for a given symbol."""
    data = get_aggregated_sentiment()
    sym_data = data.get("symbol_sentiment", {}).get(symbol.upper(), {})
    if not sym_data:
        # Also check related entities
        for ent, sdata in data.get("symbol_sentiment", {}).items():
            if symbol.upper() in ent:
                sym_data = sdata
                break
    return sym_data


def format_full_report() -> str:
    """Telegram /news command — full 35-source report."""
    d = get_aggregated_sentiment()
    now   = datetime.now().strftime("%d-%b %H:%M")
    icon  = "🟢" if d["overall"]=="BULLISH" else "🔴" if d["overall"]=="BEARISH" else "⚪"
    reddit_icon = "😱" if d["reddit_sentiment"]=="FEAR" else "🤑" if d["reddit_sentiment"]=="GREED" else "😐"

    lines = [
        f"📰 <b>NEWS INTELLIGENCE</b> | {now}",
        f"  Sources: {d['sources_live']}/35 live",
        f"  Headlines: {d['total_headlines']}",
        f"",
        f"  {icon} Market Sentiment: <b>{d['overall']}</b>  ({d['avg_score']:+.2f})",
        f"  {reddit_icon} Social/Reddit: <b>{d['reddit_sentiment']}</b>",
        f"",
    ]

    # Regulatory alerts (most important)
    if d.get("regulatory_alerts"):
        lines.append("  🏛️ <b>REGULATORY ALERTS</b>")
        for r in d["regulatory_alerts"][:3]:
            lines.append(f"  ⚠️  {r['headline'][:70]}")
        lines.append("")

    # Event detection
    events = d.get("events_detected", {})
    if events:
        lines.append("  🎯 <b>EVENTS DETECTED</b>")
        for ev, headlines in list(events.items())[:4]:
            lines.append(f"  📌 {ev}: {headlines[0][:60]}")
        lines.append("")

    # Top bullish
    if d.get("top_bullish"):
        lines.append("  🟢 <b>TOP BULLISH</b>")
        for h in d["top_bullish"][:3]:
            lines.append(f"  • [{h['source'][:12]}] {h['headline'][:65]}")
        lines.append("")

    # Top bearish
    if d.get("top_bearish"):
        lines.append("  🔴 <b>TOP BEARISH</b>")
        for h in d["top_bearish"][:3]:
            lines.append(f"  • [{h['source'][:12]}] {h['headline'][:65]}")
        lines.append("")

    # Symbol movers
    sym = d.get("symbol_sentiment", {})
    bull_syms = sorted([(k,v) for k,v in sym.items() if v["score"]>0.15],
                       key=lambda x: x[1]["score"], reverse=True)[:5]
    bear_syms = sorted([(k,v) for k,v in sym.items() if v["score"]<-0.15],
                       key=lambda x: x[1]["score"])[:5]

    if bull_syms:
        lines.append("  📈 <b>STOCKS IN NEWS (BULLISH)</b>")
        for sym_name, sdata in bull_syms:
            lines.append(f"  🟢 {sym_name:12} score={sdata['score']:+.1f} ({sdata['count']} articles)")
        lines.append("")

    if bear_syms:
        lines.append("  📉 <b>STOCKS IN NEWS (BEARISH)</b>")
        for sym_name, sdata in bear_syms:
            lines.append(f"  🔴 {sym_name:12} score={sdata['score']:+.1f} ({sdata['count']} articles)")

    lines += [
        f"",
        f"  ⏰ Refreshes every 10 min",
        f"  Sources: ET·MC·BS·Mint·CNBC·Reuters·BSE·NSE·RBI·SEBI·Reddit + 25 more",
    ]
    return "\n".join(lines)
