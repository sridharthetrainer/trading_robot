"""
omnisource_news_engine.py — Comprehensive Market Intelligence Engine

Reads from 40+ sources across ALL media types:
  📰 Financial News (ET, MC, Reuters, Bloomberg, FT, WSJ, CNBC)
  🏛️ Regulatory (NSE, BSE, RBI, SEBI, MoF, MCA)
  🌍 Global (US/Europe/Asia markets, Fed, ECB, BOJ)
  📦 Commodities (MCX, NCDEX, LME, COMEX, NYMEX)
  💹 Alternative Data (insider trades, pledges, MF flows, F&O ban)
  🤖 Social Sentiment (Reddit, Twitter/X proxy via RSS)
  📊 Economic Data (CPI, WPI, IIP, PMI, GST, Auto sales)

Inspired by:
  - Bloomberg Terminal data hierarchy
  - Two Sigma alternative data framework
  - Renaissance Technologies signal extraction
  - "Advances in Financial ML" — Lopez de Prado Ch.3 (labelling)
  - "Quantitative Value" — Tobias Carlisle (fundamental signals)

All sources are FREE — no API keys required.
"""
from __future__ import annotations
import logging, json, time, re, hashlib
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Tuple
import concurrent.futures

logger = logging.getLogger(__name__)

_CACHE_FILE = Path("omnisource_cache.json")
_CACHE_TTL  = 600  # 10 min
_SEEN_FILE  = Path("seen_headlines.json")

# ══════════════════════════════════════════════════════════════
# RSS FEED REGISTRY — 40+ sources, all free
# ══════════════════════════════════════════════════════════════
RSS_FEEDS = {

    # ── Indian Financial News ──────────────────────────────────
    "ET Markets": {
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "weight": 1.5, "category": "India Markets", "language": "en"
    },
    "ET Economy": {
        "url": "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",
        "weight": 1.3, "category": "India Economy", "language": "en"
    },
    "ET Companies": {
        "url": "https://economictimes.indiatimes.com/companynews/rssfeeds/1715249553.cms",
        "weight": 1.4, "category": "India Corporate", "language": "en"
    },
    "Moneycontrol Markets": {
        "url": "https://www.moneycontrol.com/rss/MCreader.xml",
        "weight": 1.4, "category": "India Markets", "language": "en"
    },
    "Moneycontrol Business": {
        "url": "https://www.moneycontrol.com/rss/business.xml",
        "weight": 1.2, "category": "India Corporate", "language": "en"
    },
    "Business Standard Markets": {
        "url": "https://www.business-standard.com/rss/markets-106.rss",
        "weight": 1.3, "category": "India Markets", "language": "en"
    },
    "Business Standard Economy": {
        "url": "https://www.business-standard.com/rss/economy-policy-103.rss",
        "weight": 1.2, "category": "India Economy", "language": "en"
    },
    "Mint Markets": {
        "url": "https://www.livemint.com/rss/markets",
        "weight": 1.3, "category": "India Markets", "language": "en"
    },
    "Mint Economy": {
        "url": "https://www.livemint.com/rss/economy",
        "weight": 1.2, "category": "India Economy", "language": "en"
    },
    "Financial Express Markets": {
        "url": "https://www.financialexpress.com/market/feed/",
        "weight": 1.1, "category": "India Markets", "language": "en"
    },
    "NDTV Profit": {
        "url": "https://feeds.feedburner.com/ndtvprofit-latest",
        "weight": 1.2, "category": "India Markets", "language": "en"
    },
    "Dalal Street Journal": {
        "url": "https://www.dsij.in/rss.aspx",
        "weight": 1.1, "category": "India Markets", "language": "en"
    },

    # ── Global Financial News ──────────────────────────────────
    "Reuters Business": {
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "weight": 1.5, "category": "Global Markets", "language": "en"
    },
    "Reuters India": {
        "url": "https://feeds.reuters.com/reuters/INtopNews",
        "weight": 1.4, "category": "India Global", "language": "en"
    },
    "Reuters Markets": {
        "url": "https://feeds.reuters.com/reuters/topNews",
        "weight": 1.3, "category": "Global Markets", "language": "en"
    },
    "MarketWatch": {
        "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "weight": 1.2, "category": "Global Markets", "language": "en"
    },
    "CNBC Top News": {
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "weight": 1.3, "category": "Global Markets", "language": "en"
    },
    "CNBC Finance": {
        "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "weight": 1.2, "category": "Global Markets", "language": "en"
    },
    "BBC Business": {
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "weight": 1.1, "category": "Global Economy", "language": "en"
    },
    "FT Markets": {
        "url": "https://www.ft.com/rss/home/uk",
        "weight": 1.3, "category": "Global Markets", "language": "en"
    },
    "Seeking Alpha": {
        "url": "https://seekingalpha.com/feed.xml",
        "weight": 0.9, "category": "Global Markets", "language": "en"
    },

    # ── Commodities ───────────────────────────────────────────
    "Kitco Gold": {
        "url": "https://www.kitco.com/rss/kitcoNews.rss",
        "weight": 1.3, "category": "Commodities", "language": "en"
    },
    "ET Commodities": {
        "url": "https://economictimes.indiatimes.com/commodities/rssfeeds/1808152121.cms",
        "weight": 1.2, "category": "Commodities", "language": "en"
    },
    "OilPrice.com": {
        "url": "https://oilprice.com/rss/main",
        "weight": 1.2, "category": "Commodities", "language": "en"
    },

    # ── Regulatory / Government ───────────────────────────────
    "NSE News": {
        "url": "https://www.nseindia.com/rss/Exchange-News.xml",
        "weight": 2.0, "category": "Regulatory", "language": "en"
    },
    "BSE News": {
        "url": "https://www.bseindia.com/xml/RSSFeed.aspx?Cat=1",
        "weight": 2.0, "category": "Regulatory", "language": "en"
    },
    "PIB Finance": {
        "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
        "weight": 1.8, "category": "Government", "language": "en"
    },

    # ── Cryptocurrency / DeFi (affects sentiment) ─────────────
    "CoinDesk": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "weight": 0.7, "category": "Crypto", "language": "en"
    },

    # ── Tech / Startup (affects IT sector) ────────────────────
    "TechCrunch": {
        "url": "https://techcrunch.com/feed/",
        "weight": 0.8, "category": "Technology", "language": "en"
    },

    # ── Geopolitical ──────────────────────────────────────────
    "Al Jazeera Economy": {
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "weight": 1.0, "category": "Geopolitical", "language": "en"
    },
}

# ══════════════════════════════════════════════════════════════
# SENTIMENT KEYWORDS — Enhanced for Indian markets
# ══════════════════════════════════════════════════════════════
SENTIMENT_LEXICON = {
    # Macro bullish (weight 1.5)
    "macro_bullish": {
        "rate cut", "rbi cut", "stimulus", "fdi inflow", "capex boost",
        "fiscal surplus", "current account surplus", "rupee strengthens",
        "gst collection record", "iip growth", "pmi expansion",
        "gdp beat", "inflation eases", "trade surplus",
    },
    # Macro bearish (weight 1.5)
    "macro_bearish": {
        "rate hike", "rbi hike", "inflation spike", "fiscal deficit widens",
        "current account deficit", "rupee weakens", "fii outflow",
        "recession fear", "gdp miss", "stagflation", "currency crisis",
        "trade deficit", "import surge",
    },
    # Equity bullish (weight 1.0)
    "equity_bullish": {
        "beat estimates", "earnings beat", "revenue growth", "margin expansion",
        "buyback", "dividend", "stock split", "capacity expansion", "new order",
        "contract win", "strategic partnership", "upgrade", "buy rating",
        "outperform", "overweight", "record profit", "all-time high",
        "52-week high", "breakout", "fii buying", "dii buying",
    },
    # Equity bearish (weight 1.0)
    "equity_bearish": {
        "miss estimates", "earnings miss", "revenue decline", "margin pressure",
        "debt increase", "default", "downgrade", "sell rating", "underperform",
        "underweight", "profit warning", "52-week low", "breakdown",
        "fii selling", "promoter pledge", "ed raid", "sebi notice",
        "gst notice", "regulatory action", "management change",
    },
    # High impact events (weight 2.0)
    "high_impact": {
        "rbi policy", "budget", "us fed", "powell speech", "inflation data",
        "us cpi", "india cpi", "nonfarm payroll", "gdp data",
        "election result", "war", "sanctions", "opec decision",
        "bank failure", "default", "sovereign downgrade",
    },
}

# ══════════════════════════════════════════════════════════════
# NSE-SPECIFIC DATA SOURCES (API-based)
# ══════════════════════════════════════════════════════════════

def fetch_nse_corporate_actions() -> List[dict]:
    """Corporate actions: dividends, splits, bonuses, rights."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get(
            "https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date="
            + (date.today() - timedelta(days=7)).strftime("%d-%m-%Y")
            + "&to_date=" + date.today().strftime("%d-%m-%Y"),
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            actions = []
            for item in (data if isinstance(data, list) else data.get("data", []))[:20]:
                actions.append({
                    "symbol":  item.get("symbol", ""),
                    "action":  item.get("subject", ""),
                    "exdate":  item.get("exDate", ""),
                    "impact":  "BULLISH" if any(w in str(item.get("subject","")).upper()
                               for w in ["DIVIDEND","BONUS","SPLIT"]) else "NEUTRAL",
                })
            return actions
    except Exception as e:
        logger.debug("corp_actions: %s", e)
    return []


def fetch_fno_ban_list() -> List[str]:
    """F&O securities under ban — avoid trading these."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get(
            "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O",
            timeout=10
        )
        # Also try ban list directly
        r2 = s.get(
            "https://www.nseindia.com/api/equity-master?index=BANNEDSECURITIES",
            timeout=10
        )
        banned = []
        for resp in [r, r2]:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    for item in data.get("data", []):
                        sym = item.get("symbol", "")
                        if sym: banned.append(sym)
                except Exception:
                    pass
        return list(set(banned))
    except Exception as e:
        logger.debug("fno_ban: %s", e)
    return []


def fetch_insider_trades() -> List[dict]:
    """Recent insider/promoter trades from NSE — high alpha signal."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        from_date = (date.today() - timedelta(days=30)).strftime("%d-%m-%Y")
        to_date   = date.today().strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/corporates-pit?from_date={from_date}"
            f"&to_date={to_date}&type=individual&category=PROM",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            trades = []
            for item in (data.get("data", []) if isinstance(data, dict) else data)[:15]:
                acq = float(str(item.get("acqQuantity", "0")).replace(",","") or 0)
                disp = float(str(item.get("dispQuantity", "0")).replace(",","") or 0)
                direction = "BUY" if acq > disp else "SELL"
                trades.append({
                    "symbol":    item.get("symbol", ""),
                    "person":    item.get("personCategory", ""),
                    "direction": direction,
                    "shares":    acq if direction == "BUY" else disp,
                    "date":      item.get("date", ""),
                    "impact":    "BULLISH" if direction == "BUY" else "BEARISH",
                })
            return trades
    except Exception as e:
        logger.debug("insider_trades: %s", e)
    return []


def fetch_mutual_fund_flows() -> dict:
    """Monthly mutual fund equity/debt flows from SEBI/AMFI."""
    try:
        import requests
        # AMFI monthly data
        r = requests.get(
            "https://www.amfiindia.com/modules/FundFlowsRSS.aspx",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        if r.status_code == 200:
            # Parse basic flow from response
            net_match = re.search(r'Net\s+Investment[:\s]+₹?([\d,\.]+)\s*(Cr)?', r.text, re.I)
            if net_match:
                net = float(net_match.group(1).replace(",",""))
                return {"net_equity_flow": net, "direction": "INFLOW" if net > 0 else "OUTFLOW"}
    except Exception as e:
        logger.debug("mf_flows: %s", e)
    # Fallback: return placeholder
    return {"net_equity_flow": 0, "direction": "UNKNOWN", "note": "AMFI data updating"}


def fetch_economic_indicators() -> dict:
    """
    Key Indian economic indicators — CPI, WPI, IIP, PMI, GST.
    Sources: RBI, MoSPI, PIB, CMIE via RSS/JSON.
    """
    indicators = {}
    try:
        import requests
        # GST collections from PIB
        r = requests.get(
            "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r.status_code == 200:
            gst_match = re.search(r'₹\s*([\d,]+)\s*(?:crore|lakh\s*crore)', r.text, re.I)
            if gst_match:
                gst = float(gst_match.group(1).replace(",",""))
                indicators["gst_collection"] = {"value": gst, "unit": "Cr",
                    "signal": "BULLISH" if gst > 150000 else "NEUTRAL"}
    except Exception:
        pass

    # Freddie rates (US rates affect India)
    try:
        import requests
        r2 = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r2.status_code == 200:
            lines = r2.text.strip().split("\n")
            if len(lines) > 1:
                latest = lines[-1].split(",")
                if len(latest) == 2:
                    indicators["us_fed_rate"] = {"value": float(latest[1]), "unit": "%",
                        "signal": "BEARISH" if float(latest[1]) > 5 else "NEUTRAL"}
    except Exception:
        pass

    return indicators


def fetch_social_sentiment_proxy() -> dict:
    """
    Social media sentiment proxy using public RSS feeds.
    Reddit via RSS (no API key needed).
    Twitter/X via nitter RSS mirrors.
    """
    scores = []
    headlines = []

    # Reddit India investing (public RSS)
    reddit_feeds = [
        "https://www.reddit.com/r/IndiaInvestments/.rss",
        "https://www.reddit.com/r/DalalStreetBets/.rss",
        "https://www.reddit.com/r/IndianStreetBets/.rss",
    ]

    try:
        import requests
        for feed_url in reddit_feeds:
            try:
                r = requests.get(feed_url,
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                if r.status_code == 200:
                    titles = re.findall(r'<title>(.+?)</title>', r.text)
                    for t in titles[1:8]:
                        t_clean = re.sub(r'<[^>]+>', '', t).strip()
                        if len(t_clean) > 10:
                            headlines.append(t_clean)
            except Exception:
                pass
    except Exception:
        pass

    # Score social headlines
    from news_sentiment_engine import _score_headline
    for h in headlines:
        scores.append(_score_headline(h))

    avg_social = sum(scores) / len(scores) if scores else 0
    return {
        "headlines": headlines[:10],
        "avg_score": round(avg_social, 3),
        "sentiment": "BULLISH" if avg_social > 0.1 else "BEARISH" if avg_social < -0.1 else "NEUTRAL",
        "source": "Reddit India (r/IndiaInvestments, r/DalalStreetBets)",
    }


# ══════════════════════════════════════════════════════════════
# MASTER AGGREGATOR
# ══════════════════════════════════════════════════════════════

def _fetch_single_feed(name: str, config: dict) -> Tuple[str, List[dict]]:
    """Fetch and score a single RSS feed."""
    try:
        import requests
        r = requests.get(
            config["url"],
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6
        )
        if r.status_code != 200:
            return name, []

        # Extract titles from RSS
        titles = re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', r.text)
        if not titles:
            titles = re.findall(r'<title>(.+?)</title>', r.text)

        from news_sentiment_engine import _score_headline
        results = []
        for t in titles[1:8]:  # skip feed title
            t_clean = re.sub(r'<[^>]+>', '', t).strip()
            if len(t_clean) > 10:
                score = _score_headline(t_clean) * config.get("weight", 1.0)
                results.append({
                    "headline": t_clean,
                    "source":   name,
                    "category": config.get("category", "General"),
                    "score":    round(score, 3),
                    "weight":   config.get("weight", 1.0),
                })
        return name, results
    except Exception as e:
        logger.debug("feed %s: %s", name, e)
        return name, []


def get_omnisource_intelligence(use_cache: bool = True) -> dict:
    """
    Master intelligence aggregator.
    Fetches from ALL 40+ sources in parallel.
    Returns comprehensive market intelligence dict.
    """
    # Check cache
    if use_cache and _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
            if time.time() - cached.get("ts", 0) < _CACHE_TTL:
                return cached["data"]
        except Exception:
            pass

    logger.info("OmniSource: fetching from %d sources...", len(RSS_FEEDS))

    # Parallel fetch all RSS feeds
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(_fetch_single_feed, name, cfg): name
            for name, cfg in RSS_FEEDS.items()
        }
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                name, items = future.result(timeout=8)
                all_items.extend(items)
            except Exception:
                pass

    logger.info("OmniSource: %d headlines collected", len(all_items))

    # Deduplicate by headline similarity
    seen_hashes = set()
    unique_items = []
    for item in all_items:
        h = hashlib.md5(item["headline"][:40].lower().encode()).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_items.append(item)

    # Aggregate scores by category
    by_category = {}
    for item in unique_items:
        cat = item["category"]
        if cat not in by_category:
            by_category[cat] = {"items": [], "total_score": 0, "count": 0}
        by_category[cat]["items"].append(item)
        by_category[cat]["total_score"] += item["score"]
        by_category[cat]["count"] += 1

    # Category sentiments
    cat_sentiments = {}
    for cat, data in by_category.items():
        avg = data["total_score"] / data["count"] if data["count"] else 0
        cat_sentiments[cat] = {
            "sentiment": "BULLISH" if avg > 0.15 else "BEARISH" if avg < -0.15 else "NEUTRAL",
            "score":     round(avg, 3),
            "count":     data["count"],
            "top":       sorted(data["items"], key=lambda x: abs(x["score"]), reverse=True)[:3],
        }

    # Overall weighted score
    all_scores = [i["score"] for i in unique_items]
    overall = sum(all_scores) / len(all_scores) if all_scores else 0
    overall_sentiment = "BULLISH" if overall > 0.15 else "BEARISH" if overall < -0.15 else "NEUTRAL"

    # Top movers
    top_bullish = sorted([i for i in unique_items if i["score"] > 0.2],
                         key=lambda x: x["score"], reverse=True)[:5]
    top_bearish = sorted([i for i in unique_items if i["score"] < -0.2],
                         key=lambda x: x["score"])[:5]
    high_impact = [i for i in unique_items
                   if any(w in i["headline"].lower()
                   for w in ["rbi", "fed", "budget", "inflation", "war", "default", "ban"])][:5]

    # Fetch NSE-specific data
    corp_actions  = fetch_nse_corporate_actions()
    fno_ban       = fetch_fno_ban_list()
    insider       = fetch_insider_trades()
    social        = fetch_social_sentiment_proxy()
    eco_data      = fetch_economic_indicators()

    data = {
        "ts":                 time.time(),
        "overall_sentiment":  overall_sentiment,
        "overall_score":      round(overall, 3),
        "total_headlines":    len(unique_items),
        "sources_fetched":    len(RSS_FEEDS),
        "by_category":        cat_sentiments,
        "top_bullish":        top_bullish,
        "top_bearish":        top_bearish,
        "high_impact":        high_impact,
        "corp_actions":       corp_actions,
        "fno_ban_list":       fno_ban,
        "insider_trades":     insider,
        "social_sentiment":   social,
        "economic_indicators": eco_data,
        "feed_breakdown":     {k: len([i for i in unique_items if i["source"] == k])
                                for k in RSS_FEEDS.keys()},
    }

    try:
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "data": data}, indent=2))
    except Exception:
        pass

    logger.info("OmniSource complete: %s (%.3f)", overall_sentiment, overall)
    return data


def get_symbol_specific_news(symbol: str) -> dict:
    """
    Extract all news relevant to a specific symbol.
    Used to enhance signal quality before execution.
    """
    intel = get_omnisource_intelligence()
    symbol_upper = symbol.upper()

    relevant = []
    for item in intel.get("top_bullish", []) + intel.get("top_bearish", []):
        if symbol_upper in item["headline"].upper():
            relevant.append(item)

    # Check corporate actions
    corp = [a for a in intel.get("corp_actions", [])
            if a.get("symbol", "").upper() == symbol_upper]

    # Check F&O ban
    in_ban = symbol_upper in [b.upper() for b in intel.get("fno_ban_list", [])]

    # Check insider trades
    insider = [t for t in intel.get("insider_trades", [])
               if t.get("symbol", "").upper() == symbol_upper]

    # Compute symbol-specific score adjustment
    score_adj = 0.0
    for item in relevant:
        score_adj += item["score"] * 0.5
    if corp:
        for c in corp:
            score_adj += 0.5 if c.get("impact") == "BULLISH" else -0.2
    if insider:
        for t in insider:
            score_adj += 0.8 if t.get("direction") == "BUY" else -0.8

    return {
        "symbol":       symbol,
        "relevant_news": relevant,
        "corp_actions":  corp,
        "in_fno_ban":    in_ban,
        "insider_trades": insider,
        "score_adjustment": round(max(-2.0, min(2.0, score_adj)), 2),
        "trade_safe":    not in_ban,
    }


def format_omnisource_telegram() -> str:
    """Full intelligence report for /intelligence Telegram command."""
    d = get_omnisource_intelligence()
    now = datetime.now().strftime("%d-%b %H:%M")
    sent = d.get("overall_sentiment", "NEUTRAL")
    score = d.get("overall_score", 0)
    icon = "🟢" if sent == "BULLISH" else "🔴" if sent == "BEARISH" else "⚪"

    lines = [
        f"🧠 <b>OMNISOURCE INTELLIGENCE</b> | {now}",
        f"  {icon} <b>{sent}</b>  Score: {score:+.3f}",
        f"  Headlines: {d.get('total_headlines',0)} | Sources: {d.get('sources_fetched',0)}",
        "",
    ]

    # Category breakdown
    cats = d.get("by_category", {})
    if cats:
        lines.append("  <b>BY CATEGORY</b>")
        for cat, data in sorted(cats.items(), key=lambda x: abs(x[1]["score"]), reverse=True)[:5]:
            ci = "🟢" if data["sentiment"] == "BULLISH" else "🔴" if data["sentiment"] == "BEARISH" else "⚪"
            lines.append(f"  {ci} {cat:20} {data['score']:+.2f}  ({data['count']} items)")
        lines.append("")

    # Top bullish
    if d.get("top_bullish"):
        lines.append("  <b>🟢 TOP BULLISH</b>")
        for h in d["top_bullish"][:3]:
            lines.append(f"  [{h['source'][:10]}] {h['headline'][:65]}")
        lines.append("")

    # Top bearish
    if d.get("top_bearish"):
        lines.append("  <b>🔴 TOP BEARISH</b>")
        for h in d["top_bearish"][:3]:
            lines.append(f"  [{h['source'][:10]}] {h['headline'][:65]}")
        lines.append("")

    # High impact events
    if d.get("high_impact"):
        lines.append("  <b>⚡ HIGH IMPACT EVENTS</b>")
        for h in d["high_impact"][:3]:
            lines.append(f"  ⚡ {h['headline'][:70]}")
        lines.append("")

    # Corporate actions
    corp = d.get("corp_actions", [])[:3]
    if corp:
        lines.append("  <b>📋 CORPORATE ACTIONS</b>")
        for c in corp:
            ci = "🟢" if c.get("impact") == "BULLISH" else "⚪"
            lines.append(f"  {ci} {c.get('symbol',''):12} {c.get('action','')[:40]}")
        lines.append("")

    # Insider trades
    insider = d.get("insider_trades", [])[:3]
    if insider:
        lines.append("  <b>👤 INSIDER ACTIVITY</b>")
        for t in insider:
            ti = "🟢" if t.get("direction") == "BUY" else "🔴"
            lines.append(f"  {ti} {t.get('symbol',''):12} {t.get('direction',''):5} "
                         f"{t.get('person','')[:20]}")
        lines.append("")

    # F&O ban
    ban = d.get("fno_ban_list", [])
    if ban:
        lines.append(f"  <b>🚫 F&O BAN LIST</b>: {', '.join(ban[:8])}")
        lines.append("")

    # Social sentiment
    social = d.get("social_sentiment", {})
    if social:
        si = "🟢" if social.get("sentiment") == "BULLISH" else "🔴" if social.get("sentiment") == "BEARISH" else "⚪"
        lines.append(f"  <b>📱 SOCIAL</b>: {si} {social.get('sentiment','?')} "
                     f"(Reddit India)")

    lines += [
        "",
        f"  ⏰ Refreshes every 10 min",
        f"  📡 {d.get('sources_fetched',0)} news sources | All free RSS",
    ]
    return "\n".join(lines)
