"""
comprehensive_news_engine.py — Full-Spectrum News Intelligence

Sources: 25+ free RSS/API sources covering ALL India financial news
Inspired by: Bloomberg Terminal, Reuters Eikon, Two Sigma alt-data

Categories:
  1. India Business (ET, MC, Mint, BS, FE, BL, NDTV, Zee, BQ, CNBC18)
  2. Global Markets (Reuters, FT, WSJ, Bloomberg, SCMP)
  3. Regulatory (SEBI, RBI, MoF, NSE, BSE announcements)
  4. Commodities (Kitco, MCX, NCDEX, Arab News)
  5. Macro Data (IIP, CPI, WPI, PMI, GDP releases)
  6. Corporate (NSE corp actions, SEBI SAST, bulk deals)
  7. Geopolitical (geopolitical risk scoring)
"""
from __future__ import annotations
import logging, json, time, re, os
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)
_CACHE = Path("comprehensive_news_cache.json")
_TTL   = 600  # 10 min refresh

# ── ALL RSS FEEDS ────────────────────────────────────────────────────
ALL_FEEDS = {
    # ── India Business ──────────────────────────────────────────────
    "Economic Times Markets": {
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "weight": 1.2, "category": "India Markets"
    },
    "Economic Times Economy": {
        "url": "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",
        "weight": 1.1, "category": "India Economy"
    },
    "Moneycontrol": {
        "url": "https://www.moneycontrol.com/rss/MCreader.xml",
        "weight": 1.2, "category": "India Markets"
    },
    "LiveMint Markets": {
        "url": "https://www.livemint.com/rss/markets",
        "weight": 1.1, "category": "India Markets"
    },
    "LiveMint Economy": {
        "url": "https://www.livemint.com/rss/economy",
        "weight": 1.0, "category": "India Economy"
    },
    "Business Standard": {
        "url": "https://www.business-standard.com/rss/markets-106.rss",
        "weight": 1.1, "category": "India Markets"
    },
    "Financial Express": {
        "url": "https://www.financialexpress.com/market/feed/",
        "weight": 1.0, "category": "India Markets"
    },
    "Hindu BusinessLine": {
        "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss",
        "weight": 1.0, "category": "India Markets"
    },
    "NDTV Profit": {
        "url": "https://feeds.feedburner.com/ndtvprofit-latest",
        "weight": 0.9, "category": "India Markets"
    },
    "Zee Business": {
        "url": "https://www.zeebiz.com/rss/markets.xml",
        "weight": 0.9, "category": "India Markets"
    },
    "CNBC TV18": {
        "url": "https://www.cnbctv18.com/rss/markets.xml",
        "weight": 1.0, "category": "India Markets"
    },
    "BQ Prime (BloombergQuint)": {
        "url": "https://www.bqprime.com/feeds/rss",
        "weight": 1.2, "category": "India Markets"
    },
    # ── Regulatory / Official ───────────────────────────────────────
    "NSE Announcements": {
        "url": "https://www.nseindia.com/api/corporate-announcements?index=equities",
        "weight": 2.0, "category": "Regulatory", "is_json": True
    },
    "RBI Press Releases": {
        "url": "https://www.rbi.org.in/commonman/English/scripts/PressReleases.aspx",
        "weight": 2.0, "category": "Regulatory", "skip_rss": True
    },
    "SEBI Orders": {
        "url": "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=2&ssid=14&smid=0",
        "weight": 1.5, "category": "Regulatory", "skip_rss": True
    },
    # ── Global ──────────────────────────────────────────────────────
    "Reuters Business": {
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "weight": 1.1, "category": "Global Markets"
    },
    "Reuters India": {
        "url": "https://feeds.reuters.com/reuters/INtopNews",
        "weight": 1.2, "category": "India Economy"
    },
    "FT Markets": {
        "url": "https://www.ft.com/markets?format=rss",
        "weight": 1.1, "category": "Global Markets"
    },
    "WSJ Markets": {
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "weight": 1.0, "category": "Global Markets"
    },
    "SCMP Asia Finance": {
        "url": "https://www.scmp.com/rss/91/feed",
        "weight": 0.9, "category": "Asia Markets"
    },
    "Arab News Business": {
        "url": "https://www.arabnews.com/rss.xml?section=/business-economy",
        "weight": 0.8, "category": "Commodities"
    },
    # ── Commodities ─────────────────────────────────────────────────
    "Kitco Gold": {
        "url": "https://www.kitco.com/rss/kitcoNews.rss",
        "weight": 1.2, "category": "Commodities"
    },
    "ET Commodities": {
        "url": "https://economictimes.indiatimes.com/commodities/rssfeeds/1808152121.cms",
        "weight": 1.1, "category": "Commodities"
    },
    "OilPrice.com": {
        "url": "https://oilprice.com/rss/main",
        "weight": 1.1, "category": "Commodities"
    },
    # ── Macro Data ──────────────────────────────────────────────────
    "ET Economy": {
        "url": "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",
        "weight": 1.0, "category": "Macro"
    },
    "Mint Economy": {
        "url": "https://www.livemint.com/rss/economy",
        "weight": 1.0, "category": "Macro"
    },
}

# ── Comprehensive sentiment keywords (India-specific) ────────────────
INDIA_BULLISH = {
    # Market signals
    "nifty high", "sensex high", "rally", "surge", "breakout", "record",
    "all-time high", "52-week high", "buy rating", "upgrade",
    # Policy positive
    "rbi rate cut", "repo rate cut", "liquidity injection", "stimulus",
    "capex boost", "pli scheme", "fdi inflow", "make in india",
    "infrastructure spend", "budget allocation", "gst collection record",
    # Corporate positive
    "earnings beat", "profit surge", "revenue growth", "margin expansion",
    "buyback", "dividend", "bonus issue", "rights issue", "demerger",
    "acquisition", "merger", "order win", "contract award",
    # FII/DII positive
    "fii buying", "dii buying", "mutual fund inflow", "sip record",
    "foreign inflow", "net buyer",
    # Economic positive
    "gdp growth", "iip growth", "pmi expansion", "exports rise",
    "forex reserve high", "current account surplus", "fiscal consolidation",
    "inflation easing", "cpi below target",
}
INDIA_BEARISH = {
    # Market signals
    "nifty low", "sensex crash", "selloff", "correction", "bear market",
    "52-week low", "downgrade", "sell rating", "underperform",
    # Policy negative
    "rbi rate hike", "repo rate hike", "liquidity tightening",
    "tax hike", "surcharge", "windfall tax", "export ban",
    "import duty hike", "sebi notice", "ed raid", "gst demand",
    # Corporate negative
    "earnings miss", "profit warning", "revenue decline", "margin squeeze",
    "debt default", "rating downgrade", "npa rise", "fraud",
    "insider trading", "sebi action", "nclt", "insolvency",
    # FII/DII negative
    "fii selling", "foreign outflow", "net seller", "mutual fund redemption",
    "dii selling",
    # Economic negative
    "gdp contraction", "iip decline", "pmi contraction", "inflation spike",
    "rupee fall", "current account deficit", "fiscal slippage",
    "forex reserve fall", "trade deficit widening",
    # Geopolitical
    "india pakistan", "border tension", "war", "sanctions", "tariff war",
    "recession", "global slowdown", "fed rate hike",
}

# ── Symbol-specific triggers ─────────────────────────────────────────
SYMBOL_TRIGGERS = {
    "RELIANCE": ["reliance", "ril", "jio", "mukesh ambani", "o2c"],
    "HDFCBANK": ["hdfc bank", "hdfcbank", "hdfc merger"],
    "ICICIBANK": ["icici bank", "icicibank"],
    "SBIN": ["state bank", "sbi", "sbin"],
    "TATASTEEL": ["tata steel", "tatasteel", "steel price", "hot rolled"],
    "HINDUNILVR": ["hindustan unilever", "hul", "fmcg price"],
    "SUNPHARMA": ["sun pharma", "pharma", "drug approval", "fda"],
    "BAJFINANCE": ["bajaj finance", "nbfc", "bad loans"],
    "ONGC": ["ongc", "crude oil", "brent", "oil price"],
    "INFY": ["infosys", "infy", "it sector", "digital services"],
    "TCS": ["tcs", "tata consultancy", "it services"],
    "NIFTY": ["nifty", "sensex", "indian market", "sebi", "rbi"],
    "BANKNIFTY": ["bank nifty", "banking sector", "nbfc", "credit growth"],
    "GOLD": ["gold price", "mcx gold", "sovereign gold bond"],
    "BPCL": ["bpcl", "petrol", "diesel price", "fuel price"],
    "TATAMOTORS": ["tata motors", "ev sales", "jaguar", "commercial vehicle"],
    "ADANIENT": ["adani", "hindenburg", "adani ports", "adani green"],
}


def _fetch_rss_robust(url: str, max_items: int = 8) -> List[str]:
    """Fetch RSS with multiple fallbacks and timeout."""
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; IndiaTrader/1.0)"}, timeout=8)
        if r.status_code != 200:
            return []
        content = r.text
        # Try CDATA format
        titles = re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', content)
        if not titles:
            # Try plain title
            titles = re.findall(r'<title>(.+?)</title>', content)
            titles = [t for t in titles if len(t) > 10 and '<' not in t]
        # Clean HTML entities
        clean = []
        for t in titles[1:max_items+1]:
            t = re.sub(r'&amp;', '&', t)
            t = re.sub(r'&lt;', '<', t)
            t = re.sub(r'&gt;', '>', t)
            t = re.sub(r'&quot;', '"', t)
            t = re.sub(r'&#\d+;', '', t)
            t = re.sub(r'<[^>]+>', '', t)
            if len(t.strip()) > 10:
                clean.append(t.strip())
        return clean[:max_items]
    except Exception as e:
        logger.debug("RSS fetch %s: %s", url[:40], e)
        return []


def _fetch_nse_corporate_announcements() -> List[str]:
    """NSE official corporate announcements — highest quality signal."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get(
            "https://www.nseindia.com/api/corporate-announcements?index=equities",
            timeout=10)
        if r.status_code == 200:
            items = r.json()[:20]
            headlines = []
            for item in items:
                desc = item.get("desc", "") or item.get("an_desc", "") or ""
                sym  = item.get("symbol", "")
                if desc and sym:
                    headlines.append(f"{sym}: {desc[:80]}")
            return headlines
    except Exception as e:
        logger.debug("NSE announcements: %s", e)
    return []


def _fetch_nse_bulk_deals() -> List[str]:
    """NSE bulk deals — large institutional trades."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get("https://www.nseindia.com/api/bulk-deal-live", timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [])
            headlines = []
            for d in data[:10]:
                sym   = d.get("symbol", "")
                cname = d.get("clientName", "")
                bos   = d.get("buySell", "")
                qty   = int(d.get("quantityTraded", 0) or 0)
                price = float(d.get("tradePrice", 0) or 0)
                value = qty * price / 1e7  # in crores
                if value > 10:  # Only >₹10Cr deals
                    headlines.append(
                        f"BULK DEAL: {sym} — {cname} {bos} "
                        f"{qty:,} shares @ ₹{price:,.0f} (₹{value:.0f}Cr)"
                    )
            return headlines
    except Exception as e:
        logger.debug("NSE bulk deals: %s", e)
    return []


def _fetch_delivery_data(symbol: str) -> Optional[float]:
    """
    NSE delivery percentage for a symbol.
    High delivery % = strong conviction buying (not speculative).
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
            timeout=7)
        if r.status_code == 200:
            data = r.json()
            trade = data.get("tradeInfo", {})
            total_qty  = float(trade.get("totalTradedVolume", 0) or 0)
            deliv_qty  = float(trade.get("deliveryQuantity", 0) or 0)
            if total_qty > 0 and deliv_qty > 0:
                return round(deliv_qty / total_qty * 100, 1)
    except Exception:
        pass
    return None


def _fetch_pcr_from_nse() -> dict:
    """
    NSE Put/Call Ratio — powerful contrarian indicator.
    PCR > 1.5 = extreme fear = buy signal
    PCR < 0.6 = extreme greed = sell signal
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            timeout=10)
        if r.status_code == 200:
            data = r.json().get("filtered", {}).get("CE", {})
            ce_oi = data.get("totOI", 0)
            pe_data = r.json().get("filtered", {}).get("PE", {})
            pe_oi = pe_data.get("totOI", 0)
            if ce_oi > 0:
                pcr = pe_oi / ce_oi
                signal = ("EXTREME_FEAR_BUY" if pcr > 1.5 else
                          "FEAR" if pcr > 1.2 else
                          "NEUTRAL" if pcr > 0.8 else
                          "GREED" if pcr > 0.6 else "EXTREME_GREED_SELL")
                return {"pcr": round(pcr, 2), "signal": signal,
                        "ce_oi": ce_oi, "pe_oi": pe_oi}
    except Exception as e:
        logger.debug("PCR fetch: %s", e)
    return {}


def _score_headline_advanced(text: str) -> float:
    """
    Advanced NLP scoring using India-specific sentiment dictionary.
    Weight = 1.2x for regulatory news (highest impact).
    """
    text_lower = text.lower()
    score = 0.0

    for phrase in INDIA_BULLISH:
        if phrase in text_lower:
            score += 0.4

    for phrase in INDIA_BEARISH:
        if phrase in text_lower:
            score -= 0.4

    # Amplify regulatory news
    reg_words = ["sebi", "rbi", "government", "ministry", "supreme court", "nclat"]
    if any(w in text_lower for w in reg_words):
        score *= 1.3

    # Negation handling
    negations = ["not", "no", "despite", "although", "reverses", "denies"]
    if any(n in text_lower.split()[:5] for n in negations):
        score *= -0.6

    return max(-1.0, min(1.0, round(score, 2)))


def get_symbol_specific_news(symbol: str) -> List[dict]:
    """
    Fetch news specifically about a symbol across all sources.
    Used to add context to individual trade signals.
    """
    triggers = SYMBOL_TRIGGERS.get(symbol.upper(), [symbol.lower()])
    relevant = []

    # Scan all cached news
    if _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            all_headlines = cached.get("data", {}).get("all_headlines", [])
            for h in all_headlines:
                text = h.get("headline", "").lower()
                if any(t in text for t in triggers):
                    relevant.append(h)
        except Exception:
            pass

    # Also check NSE announcements specifically
    announcements = _fetch_nse_corporate_announcements()
    for ann in announcements:
        if symbol.upper() in ann.upper():
            relevant.append({
                "headline": ann,
                "score": _score_headline_advanced(ann),
                "category": "NSE Announcement",
                "source": "NSE Official",
            })

    return relevant[:5]


def get_geopolitical_risk_score() -> dict:
    """
    Geopolitical risk scoring from news headlines.
    High risk → safe haven assets, defence stocks.
    
    Based on: GPR Index methodology (Caldara & Iacoviello, Fed Reserve)
    """
    geo_keywords = {
        "high_risk": [
            "war", "military strike", "missile", "nuclear", "sanctions",
            "invasion", "conflict", "troops", "border tension", "ceasefire broken",
            "india pakistan", "india china", "red sea", "strait of hormuz",
        ],
        "medium_risk": [
            "tariff", "trade war", "diplomatic", "protest", "election",
            "coup", "terror", "attack", "riot", "unrest",
        ],
        "low_risk": [
            "peace talks", "ceasefire", "trade deal", "diplomatic relations",
            "cooperation", "summit", "agreement",
        ],
    }

    risk_score = 0
    risk_headlines = []

    if _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            all_h = cached.get("data", {}).get("all_headlines", [])
            for h in all_h:
                text = h.get("headline", "").lower()
                for kw in geo_keywords["high_risk"]:
                    if kw in text:
                        risk_score += 3
                        risk_headlines.append(h["headline"][:60])
                for kw in geo_keywords["medium_risk"]:
                    if kw in text:
                        risk_score += 1
                for kw in geo_keywords["low_risk"]:
                    if kw in text:
                        risk_score -= 1
        except Exception:
            pass

    level = ("CRITICAL" if risk_score > 15 else
             "HIGH" if risk_score > 8 else
             "MODERATE" if risk_score > 3 else "LOW")

    sectors_affected = []
    if risk_score > 8:
        sectors_affected = ["Defence (HAL/BEL/BEML)", "Gold (safe haven)", "IT (dollar hedge)"]
    elif risk_score > 3:
        sectors_affected = ["Gold (safe haven)", "Reduce Energy exposure"]

    return {
        "score": risk_score,
        "level": level,
        "sectors": sectors_affected,
        "top_headlines": risk_headlines[:3],
    }


def fetch_all_news() -> dict:
    """
    Master news fetch — all 25+ sources.
    Runs in parallel for speed.
    Returns structured news data.
    """
    import concurrent.futures

    all_headlines = []
    by_category = {}
    nse_announcements = []
    bulk_deals = []

    def _fetch_one(name_feed):
        name, feed = name_feed
        if feed.get("skip_rss") or feed.get("is_json"):
            return name, []
        headlines = _fetch_rss_robust(feed["url"], max_items=6)
        return name, headlines

    # Parallel fetch all RSS feeds
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_fetch_one, (name, feed)): name
            for name, feed in ALL_FEEDS.items()
            if not feed.get("skip_rss") and not feed.get("is_json")
        }
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                name, headlines = future.result()
                feed = ALL_FEEDS.get(name, {})
                cat = feed.get("category", "Other")
                weight = feed.get("weight", 1.0)

                if cat not in by_category:
                    by_category[cat] = []

                for h in headlines:
                    score = _score_headline_advanced(h) * weight
                    item = {
                        "headline": h,
                        "score": round(score, 2),
                        "source": name,
                        "category": cat,
                        "ts": time.time(),
                    }
                    all_headlines.append(item)
                    by_category[cat].append(item)
            except Exception as e:
                logger.debug("Feed error: %s", e)

    # NSE official feeds (sequential — needs session)
    try:
        nse_announcements = _fetch_nse_corporate_announcements()
        for ann in nse_announcements:
            score = _score_headline_advanced(ann) * 2.0  # NSE = 2x weight
            item = {"headline": ann, "score": score,
                    "source": "NSE Official", "category": "Regulatory"}
            all_headlines.append(item)
            by_category.setdefault("Regulatory", []).append(item)
    except Exception:
        pass

    # Bulk deals
    try:
        bulk_deals = _fetch_nse_bulk_deals()
        for bd in bulk_deals:
            score = 0.5 if "BUY" in bd else -0.3  # bulk buy = bullish
            item = {"headline": bd, "score": score,
                    "source": "NSE Bulk Deals", "category": "Institutional"}
            all_headlines.append(item)
    except Exception:
        pass

    # PCR
    pcr = _fetch_pcr_from_nse()

    # Geopolitical risk
    geo = get_geopolitical_risk_score() if all_headlines else {"score": 0, "level": "LOW"}

    # Overall sentiment
    scores = [h["score"] for h in all_headlines if h.get("score") is not None]
    avg_score = sum(scores) / len(scores) if scores else 0
    sentiment = ("STRONGLY_BULLISH" if avg_score > 0.3 else
                 "BULLISH"         if avg_score > 0.1 else
                 "NEUTRAL"         if avg_score > -0.1 else
                 "BEARISH"         if avg_score > -0.3 else
                 "STRONGLY_BEARISH")

    # Top headlines
    top_bull = sorted([h for h in all_headlines if h["score"] > 0.2],
                      key=lambda x: x["score"], reverse=True)[:5]
    top_bear = sorted([h for h in all_headlines if h["score"] < -0.2],
                      key=lambda x: x["score"])[:5]

    data = {
        "ts":             time.time(),
        "total_sources":  len(ALL_FEEDS),
        "total_headlines": len(all_headlines),
        "all_headlines":  all_headlines,
        "by_category":   {k: v[:3] for k, v in by_category.items()},
        "avg_score":      round(avg_score, 3),
        "sentiment":      sentiment,
        "top_bullish":    top_bull,
        "top_bearish":    top_bear,
        "pcr":            pcr,
        "geopolitical":   geo,
        "nse_announcements": nse_announcements[:5],
        "bulk_deals":     bulk_deals[:5],
    }

    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception:
        pass

    logger.info("News fetch: %d headlines from %d sources | sentiment=%s",
                len(all_headlines), len(ALL_FEEDS), sentiment)
    return data


def get_news(use_cache: bool = True) -> dict:
    """Get news with caching."""
    if use_cache and _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            if time.time() - cached.get("ts", 0) < _TTL:
                return cached["data"]
        except Exception:
            pass
    return fetch_all_news()


def format_telegram_comprehensive() -> str:
    """Full news + WOW factors Telegram report."""
    d = get_news()
    now = datetime.now().strftime("%d-%b %H:%M")
    sent = d.get("sentiment", "NEUTRAL")
    avg  = d.get("avg_score", 0)
    icon = ("🟢🟢" if "STRONGLY_BULLISH" in sent else
            "🟢"  if "BULLISH" in sent else
            "🔴🔴" if "STRONGLY_BEARISH" in sent else
            "🔴"  if "BEARISH" in sent else "⚪")

    lines = [
        f"📰 <b>COMPREHENSIVE MARKET INTELLIGENCE</b>",
        f"  {now}  |  {d.get('total_sources',0)} sources  |  "
        f"{d.get('total_headlines',0)} headlines",
        "",
        f"  {icon} Sentiment: <b>{sent}</b>  (score: {avg:+.3f})",
        "",
    ]

    # PCR
    pcr = d.get("pcr", {})
    if pcr:
        pcr_icon = ("🟢" if "FEAR" in pcr.get("signal","") else
                    "🔴" if "GREED" in pcr.get("signal","") else "⚪")
        lines += [
            f"  <b>PUT/CALL RATIO (NIFTY)</b>",
            f"  {pcr_icon} PCR: {pcr.get('pcr',0):.2f} → {pcr.get('signal','?')}",
            f"  CE OI: {pcr.get('ce_oi',0):,}  PE OI: {pcr.get('pe_oi',0):,}",
            "",
        ]

    # Geopolitical
    geo = d.get("geopolitical", {})
    if geo.get("level","LOW") != "LOW":
        geo_icon = "🚨" if geo["level"] == "CRITICAL" else "⚠️"
        lines += [
            f"  <b>{geo_icon} GEOPOLITICAL RISK: {geo['level']}</b>",
        ]
        for s in geo.get("sectors", [])[:2]:
            lines.append(f"   → {s}")
        lines.append("")

    # NSE Announcements
    nse_ann = d.get("nse_announcements", [])
    if nse_ann:
        lines.append("  <b>📢 NSE CORPORATE ANNOUNCEMENTS</b>")
        for ann in nse_ann[:3]:
            lines.append(f"  • {ann[:70]}")
        lines.append("")

    # Bulk Deals
    bulk = d.get("bulk_deals", [])
    if bulk:
        lines.append("  <b>💼 BULK DEALS</b>")
        for bd in bulk[:2]:
            lines.append(f"  • {bd[:70]}")
        lines.append("")

    # Top bullish
    if d.get("top_bullish"):
        lines.append("  <b>🟢 TOP BULLISH SIGNALS</b>")
        for h in d["top_bullish"][:3]:
            src = h.get("source","")[:15]
            lines.append(f"  [{src}] {h['headline'][:60]}")
        lines.append("")

    # Top bearish
    if d.get("top_bearish"):
        lines.append("  <b>🔴 TOP BEARISH SIGNALS</b>")
        for h in d["top_bearish"][:3]:
            src = h.get("source","")[:15]
            lines.append(f"  [{src}] {h['headline'][:60]}")
        lines.append("")

    lines += [
        f"  ⏰ Refreshes every 10 min",
        f"  Sources: ET·MC·Mint·BS·FE·BL·NDTV·Zee·CNBC·BQ·Reuters·FT·WSJ",
    ]
    return "\n".join(lines)
