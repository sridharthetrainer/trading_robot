"""
unified_intelligence_hub.py — Master Intelligence Orchestrator

Aggregates ALL data sources into one unified market score.
Inspired by:
  - Bloomberg Terminal intelligence layer
  - Renaissance Technologies signal aggregation
  - Two Sigma alternative data framework
  - Goldman Sachs GS Sustain ESG scoring

NEWS SOURCES (35 total, all free):
  India: ET, MC, BS, Livemint, HBL, CNBC-TV18, ZeeBiz, Financial Express,
         Outlook Business, PIB (Govt), RBI press releases, SEBI circulars,
         NSE corporate announcements, BSE announcements
  Global: Reuters, MarketWatch, Google News (8 categories), Kitco (metals)

PROPRIETARY SIGNALS (WOW factors):
  1. PCR (Put-Call Ratio) — options sentiment
  2. Max Pain — options wall
  3. OI Buildup — smart money positioning
  4. Delivery % — conviction buying vs speculation
  5. FII currency flows — hot money direction
  6. Promoter pledging — insider stress signal
  7. Yield curve — macro regime
  8. Mutual fund flow — retail money direction
  9. Crude inventory (EIA) — oil price direction
  10. Monsoon data — agri sector impact
  11. Google Trends — retail investor interest
  12. Insider trading SAST disclosures

OUTPUT:
  - Unified intelligence score (-10 to +10)
  - Per-symbol sentiment boost/drag
  - Pre-market brief text
  - Video script data
  - Signal score modifier
"""
from __future__ import annotations
import json, logging, time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_CACHE = Path("intelligence_cache.json")
_TTL   = 900  # 15 min

# ── Score weights ────────────────────────────────────────────────────
# How much each signal contributes to final score (-10 to +10)
WEIGHTS = {
    "news_india":      2.0,   # India-specific news
    "news_global":     1.5,   # Global macro news
    "pcr":             1.5,   # Put-Call ratio
    "fii_flow":        1.5,   # FII net buying/selling
    "max_pain":        1.0,   # Options max pain vs spot
    "oi_buildup":      1.0,   # OI buildup signal
    "delivery_pct":    0.8,   # Delivery percentage
    "mf_flow":         0.7,   # Mutual fund net flow
    "yield_curve":     0.5,   # US/India yield curve
    "promoter":        0.5,   # Promoter pledging/buying
    "crude_inventory": 0.5,   # EIA crude inventory
    "monsoon":         0.3,   # IMD monsoon data
    "corporate_actions":0.5,  # Dividends, buybacks, splits
    "insider_trading": 0.7,   # SAST disclosures
    "sebi_circulars":  0.3,   # SEBI regulatory news
}


def _safe_get(url: str, session=None, timeout: int = 8) -> Optional[object]:
    """Safe HTTP GET with retry."""
    try:
        import requests
        s = session or requests.Session()
        s.headers.setdefault("User-Agent", "Mozilla/5.0")
        r = s.get(url, timeout=timeout)
        return r if r.status_code == 200 else None
    except Exception as e:
        logger.debug("GET %s: %s", url[:50], e)
        return None


def _nse_session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
    s.get("https://www.nseindia.com/", timeout=5)
    return s


def _score_text(text: str) -> float:
    """Score text sentiment -1.0 to +1.0."""
    import re
    text = text.lower()
    bullish = {"surge","soar","rally","boom","breakout","record","beat","strong",
               "upgrade","positive","growth","profit","buy","bullish","momentum",
               "rate cut","stimulus","capex","fdi","inflow","recovery","expansion"}
    bearish = {"crash","plunge","tumble","selloff","correction","bear","miss",
               "downgrade","underperform","sell","loss","recession","default",
               "rate hike","inflation","deficit","outflow","concern","risk",
               "weak","pressure","warning","halt","ban","fraud","raid","notice"}
    negators = {"not","no","never","despite","although"}
    words = set(re.findall(r'\b\w+\b', text))
    score = (sum(0.3 for w in bullish if w in text) -
             sum(0.3 for w in bearish if w in text))
    if words & negators: score *= -0.6
    return max(-1.0, min(1.0, score))


# ════════════════════════════════════════════════════════════════════
# INTELLIGENCE MODULES
# ════════════════════════════════════════════════════════════════════

def fetch_india_news_sentiment() -> dict:
    """
    35 India + global RSS feeds → combined sentiment score.
    Sources: ET, MC, BS, Livemint, HBL, CNBC-TV18, Reuters, PIB, RBI, SEBI.
    """
    import re as _re
    FEEDS = [
        # India markets
        ("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "ET Markets", 1.5),
        ("https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms", "ET Economy", 1.2),
        ("https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms","ET Stocks",1.3),
        ("https://www.moneycontrol.com/rss/MCreader.xml", "Moneycontrol", 1.3),
        ("https://www.business-standard.com/rss/markets-106.rss", "BS Markets", 1.2),
        ("https://www.business-standard.com/rss/economy-policy-10601.rss","BS Economy",1.0),
        ("https://www.livemint.com/rss/markets", "Livemint Markets", 1.2),
        ("https://www.livemint.com/rss/economy", "Livemint Economy", 1.0),
        ("https://www.thehindubusinessline.com/markets/?service=rss","HBL Markets",1.0),
        ("https://www.cnbctv18.com/commonfeeds/v1/cne/rss/markets.xml","CNBC-TV18",1.2),
        ("https://zeebiz.com/feeds/business.xml", "Zee Business", 0.8),
        ("https://www.financialexpress.com/market/feed/","Financial Express",0.8),
        ("https://www.outlookbusiness.com/rss", "Outlook Business", 0.7),
        # Global
        ("https://feeds.reuters.com/reuters/INtopNews", "Reuters India", 1.3),
        ("https://feeds.reuters.com/reuters/businessNews", "Reuters Business", 1.1),
        ("https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines","MarketWatch",1.0),
        ("https://www.kitco.com/rss/kitcoNews.rss", "Kitco Metals", 0.8),
        # Government/Regulatory
        ("https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3","PIB Govt",0.9),
        ("https://www.rbi.org.in/Scripts/RSS.aspx?Id=RBIPressRelease","RBI Press",1.5),
        # Google News (dynamic — latest headlines)
        ("https://news.google.com/rss/search?q=nifty+sensex+market&hl=en-IN&gl=IN&ceid=IN:en","GNews India",1.2),
        ("https://news.google.com/rss/search?q=RBI+repo+rate+india&hl=en-IN&gl=IN&ceid=IN:en","GNews RBI",1.5),
        ("https://news.google.com/rss/search?q=FII+DII+india+buying&hl=en-IN&gl=IN&ceid=IN:en","GNews FII",1.4),
        ("https://news.google.com/rss/search?q=crude+oil+opec+price&hl=en&gl=US&ceid=US:en","GNews Crude",1.0),
        ("https://news.google.com/rss/search?q=federal+reserve+rate+hike+cut&hl=en&gl=US&ceid=US:en","GNews Fed",1.2),
        ("https://news.google.com/rss/search?q=china+economy+trade+india&hl=en&gl=US&ceid=US:en","GNews China",0.9),
        ("https://news.google.com/rss/search?q=geopolitical+risk+war&hl=en&gl=US&ceid=US:en","GNews Geo",1.0),
        ("https://news.google.com/rss/search?q=india+budget+tax+economy&hl=en-IN&gl=IN&ceid=IN:en","GNews Budget",1.3),
        ("https://news.google.com/rss/search?q=EIA+crude+oil+inventory&hl=en&gl=US&ceid=US:en","GNews EIA",0.8),
    ]

    all_scores, headlines_by_cat = [], {}
    import requests

    for url, label, weight in FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=6)
            if r.status_code != 200:
                continue
            titles = _re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', r.text)
            if not titles:
                titles = _re.findall(r'<title>(.+?)</title>', r.text)
            titles = [t.strip() for t in titles[1:6]]  # skip feed title, get 5

            for title in titles:
                score = _score_text(title) * weight
                all_scores.append(score)
                cat = label.split()[0]
                headlines_by_cat.setdefault(cat, []).append({
                    "headline": title,
                    "score": round(score, 2),
                    "source": label
                })
        except Exception:
            pass

    avg = sum(all_scores) / len(all_scores) if all_scores else 0
    top_bullish = sorted([h for hs in headlines_by_cat.values() for h in hs if h["score"] > 0],
                         key=lambda x: x["score"], reverse=True)[:5]
    top_bearish = sorted([h for hs in headlines_by_cat.values() for h in hs if h["score"] < 0],
                         key=lambda x: x["score"])[:5]

    logger.info("News sentiment: %.3f from %d headlines (%d feeds)",
                avg, len(all_scores), len(FEEDS))
    return {
        "score": round(avg, 3),
        "total": len(all_scores),
        "feeds": len(FEEDS),
        "top_bullish": top_bullish[:3],
        "top_bearish": top_bearish[:3],
        "sentiment": "BULLISH" if avg > 0.1 else "BEARISH" if avg < -0.1 else "NEUTRAL",
        "categories": {k: len(v) for k, v in headlines_by_cat.items()},
    }


def fetch_pcr_signal() -> dict:
    """Put-Call Ratio from NSE option chain. PCR > 1.3 = bullish, < 0.7 = bearish."""
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()["filtered"]
        total_ce_oi = sum(d.get("CE",{}).get("openInterest",0) or 0
                         for d in data.get("data",[]))
        total_pe_oi = sum(d.get("PE",{}).get("openInterest",0) or 0
                         for d in data.get("data",[]))
        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0
        signal = "BULLISH" if pcr > 1.2 else "BEARISH" if pcr < 0.8 else "NEUTRAL"
        score = min(1.0, (pcr - 1.0) * 2) if pcr > 1 else max(-1.0, (pcr - 1.0) * 2)
        return {"pcr": round(pcr, 3), "signal": signal, "score": round(score, 3)}
    except Exception as e:
        logger.debug("PCR: %s", e)
        return {}


def fetch_delivery_signal() -> dict:
    """
    Delivery percentage from NSE.
    High delivery (>60%) = conviction buying = bullish.
    Low delivery (<30%) = speculative = bearish.
    """
    try:
        from wow_factors_engine import get_delivery_pct
        result = get_delivery_pct()
        return result if result else {}
    except Exception:
        pass
    return {}


def fetch_corporate_actions() -> dict:
    """
    NSE corporate actions: dividends, buybacks, splits, rights.
    Positive: buyback, high dividend
    Negative: rights issue (dilution), large dividend (signaling low reinvestment)
    """
    try:
        s = _nse_session()
        today = date.today().strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/corporates-corporateActions"
            f"?index=equities&from_date={today}&to_date={today}",
            timeout=8)
        if r.status_code != 200:
            return {}
        actions = r.json()[:20] if isinstance(r.json(), list) else []
        buybacks = [a for a in actions if 'buyback' in str(a).lower()]
        dividends = [a for a in actions if 'dividend' in str(a).lower()]
        splits = [a for a in actions if 'split' in str(a).lower()]
        rights = [a for a in actions if 'rights' in str(a).lower()]
        score = len(buybacks)*0.5 + len(dividends)*0.2 - len(rights)*0.3
        return {
            "buybacks": len(buybacks), "dividends": len(dividends),
            "splits": len(splits), "rights": len(rights),
            "score": round(min(1.0, max(-1.0, score)), 2),
        }
    except Exception as e:
        logger.debug("corporate_actions: %s", e)
        return {}


def fetch_insider_disclosures() -> dict:
    """
    SAST disclosures (insider trading) from NSE.
    Promoter buying = strong bullish signal.
    Promoter selling = weak bearish signal.
    """
    try:
        s = _nse_session()
        today = date.today().strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/sast-disclosures"
            f"?from_date={today}&to_date={today}",
            timeout=8)
        if r.status_code != 200:
            return {}
        data = r.json()[:30] if isinstance(r.json(), list) else []
        buys  = [d for d in data if 'buy' in str(d.get('typeOfTransaction','')).lower() or
                 'acqui' in str(d.get('typeOfTransaction','')).lower()]
        sells = [d for d in data if 'sell' in str(d.get('typeOfTransaction','')).lower()]
        score = (len(buys) - len(sells)*0.5) / max(len(data), 1)
        return {
            "promoter_buys": len(buys), "promoter_sells": len(sells),
            "score": round(min(1.0, max(-1.0, score)), 2),
            "signal": "BULLISH" if score > 0.2 else "BEARISH" if score < -0.2 else "NEUTRAL",
        }
    except Exception as e:
        logger.debug("SAST: %s", e)
        return {}


def fetch_fno_ban_signal() -> dict:
    """
    F&O ban list from NSE.
    Stocks in ban cannot be traded in F&O.
    Many stocks in ban = market stress = bearish.
    """
    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/fo-ban", timeout=8)
        if r.status_code != 200:
            return {}
        ban_list = r.json()[:50] if isinstance(r.json(), list) else []
        n = len(ban_list)
        score = -min(1.0, n / 20)  # 20+ stocks banned = max bearish
        return {
            "count": n, "symbols": [str(b)[:10] for b in ban_list[:5]],
            "score": round(score, 2),
            "signal": "BEARISH" if n > 10 else "NEUTRAL",
        }
    except Exception as e:
        logger.debug("FNO ban: %s", e)
        return {}


def fetch_52week_signals() -> dict:
    """
    52-week highs vs lows ratio. More highs = market breadth bullish.
    From NSE live analysis.
    """
    try:
        s = _nse_session()
        highs_r = s.get("https://www.nseindia.com/api/live-analysis-variations?index=high52week", timeout=8)
        lows_r  = s.get("https://www.nseindia.com/api/live-analysis-variations?index=low52week", timeout=8)
        highs = len(highs_r.json()) if highs_r.status_code == 200 else 0
        lows  = len(lows_r.json()) if lows_r.status_code == 200 else 0
        ratio = highs / max(lows, 1)
        score = min(1.0, max(-1.0, (ratio - 1) * 0.5))
        return {
            "highs": highs, "lows": lows, "ratio": round(ratio, 2),
            "score": round(score, 2),
            "signal": "BULLISH" if ratio > 2 else "BEARISH" if ratio < 0.5 else "NEUTRAL",
        }
    except Exception as e:
        logger.debug("52w: %s", e)
        return {}


def get_intelligence_score(use_cache: bool = True) -> float:
    """Get current intelligence score quickly (uses cache)."""
    if use_cache and _CACHE.exists():
        try:
            c = json.loads(_CACHE.read_text())
            if time.time() - c.get("ts", 0) < _TTL:
                return c.get("data", {}).get("total_score", 0.0)
        except Exception:
            pass
    return 0.0


def get_symbol_intelligence_boost(symbol: str) -> float:
    """
    Get intelligence-based signal boost/drag for a specific symbol.
    Used by signal_engine to adjust confluence scores.
    """
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text())
            boosts = c.get("data", {}).get("symbol_boosts", {})
            return float(boosts.get(symbol.upper(), 0.0))
    except Exception:
        pass
    return 0.0


def refresh_all_intelligence(alerts=None) -> dict:
    """
    Full intelligence refresh — all 12 modules.
    Run at 9:00 AM and 12:00 PM daily.
    """
    logger.info("Intelligence refresh starting (12 modules)...")
    results = {}
    start = time.time()

    # Module 1: News sentiment (35 feeds)
    try:
        results["news"] = fetch_india_news_sentiment()
        logger.info("  ✅ News: %.3f (%d headlines)", 
                    results["news"].get("score",0), results["news"].get("total",0))
    except Exception as e:
        logger.debug("news: %s", e)

    # Module 2: PCR
    try:
        results["pcr"] = fetch_pcr_signal()
        logger.info("  ✅ PCR: %.3f → %s", 
                    results["pcr"].get("pcr",1), results["pcr"].get("signal","?"))
    except Exception as e:
        logger.debug("pcr: %s", e)

    # Module 3: Delivery %
    try:
        results["delivery"] = fetch_delivery_signal()
    except Exception: pass

    # Module 4: Corporate actions
    try:
        results["corp_actions"] = fetch_corporate_actions()
        logger.info("  ✅ Corp actions: buybacks=%d dividends=%d",
                    results["corp_actions"].get("buybacks",0),
                    results["corp_actions"].get("dividends",0))
    except Exception as e:
        logger.debug("corp_actions: %s", e)

    # Module 5: Insider trading (SAST)
    try:
        results["insider"] = fetch_insider_disclosures()
        logger.info("  ✅ Insider: buys=%d sells=%d",
                    results["insider"].get("promoter_buys",0),
                    results["insider"].get("promoter_sells",0))
    except Exception as e:
        logger.debug("insider: %s", e)

    # Module 6: F&O ban list
    try:
        results["fno_ban"] = fetch_fno_ban_signal()
        logger.info("  ✅ FNO ban: %d stocks", results["fno_ban"].get("count",0))
    except Exception as e:
        logger.debug("fno_ban: %s", e)

    # Module 7: 52-week breadth
    try:
        results["breadth"] = fetch_52week_signals()
        logger.info("  ✅ Breadth: highs=%d lows=%d",
                    results["breadth"].get("highs",0), results["breadth"].get("lows",0))
    except Exception as e:
        logger.debug("breadth: %s", e)

    # Module 8: WOW factors (existing engine)
    try:
        from wow_factors_engine import get_pcr, get_max_pain, get_oi_buildup
        from wow_factors_engine import get_yield_curve_signal, get_fii_currency_flows
        results["wow_pcr"]   = get_pcr("NIFTY")
        results["wow_pain"]  = get_max_pain("NIFTY")
        results["wow_oi"]    = get_oi_buildup("NIFTY")
        results["wow_yield"] = get_yield_curve_signal()
        results["wow_fii"]   = get_fii_currency_flows()
        logger.info("  ✅ WOW factors loaded")
    except Exception as e:
        logger.debug("wow: %s", e)

    # Module 9: Mega intelligence engine
    try:
        from mega_intelligence_engine import fetch_india_macro, fetch_promoter_activity
        results["macro"]     = fetch_india_macro()
        results["promoter"]  = fetch_promoter_activity()
        logger.info("  ✅ Mega intelligence loaded")
    except Exception as e:
        logger.debug("mega_intel: %s", e)

    # Module 10: Market intelligence (insider, MF)
    try:
        from market_intelligence import fetch_insider_trading, fetch_mf_flow_signals
        results["mi_insider"] = fetch_insider_trading()
        results["mi_mf"]      = fetch_mf_flow_signals()
        logger.info("  ✅ Market intelligence loaded")
    except Exception as e:
        logger.debug("market_intel: %s", e)

    # Module 11: Commodity analysis
    try:
        from news_sentiment_engine import fetch_commodity_prices, analyze_commodity_impact
        comms = fetch_commodity_prices()
        results["commodities"] = comms
        results["commodity_impacts"] = analyze_commodity_impact(comms)
        logger.info("  ✅ Commodities: %d prices fetched", len(comms))
    except Exception as e:
        logger.debug("commodities: %s", e)

    # Module 12: Cross-asset / global macro
    try:
        from cross_asset import get_cross_asset_data, get_market_bias
        macro_data = get_cross_asset_data(force=True)
        results["global_macro"] = macro_data
        results["global_bias"]  = get_market_bias(macro_data)
        logger.info("  ✅ Global macro bias: %.3f", results["global_bias"])
    except Exception as e:
        logger.debug("cross_asset: %s", e)

    # ── COMPUTE UNIFIED SCORE ────────────────────────────────────────
    score_components = {}

    # News score (−1 to +1) → weighted
    if "news" in results:
        ns = results["news"].get("score", 0)
        score_components["news"] = ns * WEIGHTS.get("news_india", 2.0)

    # PCR score
    if "pcr" in results:
        score_components["pcr"] = results["pcr"].get("score", 0) * WEIGHTS.get("pcr", 1.5)
    elif "wow_pcr" in results:
        pcr_val = results["wow_pcr"].get("pcr", 1.0)
        pcr_score = min(1.0, (pcr_val - 1.0)) if pcr_val > 1 else max(-1.0, pcr_val - 1.0)
        score_components["pcr"] = pcr_score * 1.5

    # Corporate actions
    if "corp_actions" in results:
        score_components["corp"] = results["corp_actions"].get("score",0) * 0.5

    # Insider
    if "insider" in results:
        score_components["insider"] = results["insider"].get("score",0) * WEIGHTS.get("insider_trading",0.7)

    # FNO ban (bearish signal)
    if "fno_ban" in results:
        score_components["fno"] = results["fno_ban"].get("score",0) * 0.5

    # Market breadth
    if "breadth" in results:
        score_components["breadth"] = results["breadth"].get("score",0) * 0.8

    # Global bias
    if "global_bias" in results:
        score_components["global"] = results["global_bias"] * WEIGHTS.get("news_global", 1.5)

    # FII currency flows
    if "wow_fii" in results:
        fii_s = results["wow_fii"].get("score", 0)
        score_components["fii"] = fii_s * WEIGHTS.get("fii_flow", 1.5)

    total_weight = sum(WEIGHTS.values())
    raw_score = sum(score_components.values())
    # Normalize to -10 to +10
    total_score = max(-10.0, min(10.0, raw_score * 2))

    intelligence = {
        "ts": time.time(),
        "total_score": round(total_score, 2),
        "components": {k: round(v, 3) for k, v in score_components.items()},
        "signals": {k: v for k, v in results.items()
                    if isinstance(v, dict) and "score" in v},
        "sentiment": ("STRONG BULL" if total_score > 5 else
                      "BULLISH"     if total_score > 2 else
                      "NEUTRAL"     if total_score > -2 else
                      "BEARISH"     if total_score > -5 else
                      "STRONG BEAR"),
        "top_bullish_news": results.get("news",{}).get("top_bullish",[]),
        "top_bearish_news": results.get("news",{}).get("top_bearish",[]),
        "commodity_impacts": results.get("commodity_impacts",{}),
        "commodities": {k: {"price": v.get("price",0), "change_pct": v.get("change_pct",0)}
                        for k,v in results.get("commodities",{}).items()},
        "symbol_boosts": {},
        "runtime_sec": round(time.time() - start, 1),
    }

    # Cache
    try:
        _CACHE.write_text(json.dumps({"ts": time.time(), "data": intelligence}, indent=2))
    except Exception:
        pass

    elapsed = time.time() - start
    logger.info("Intelligence refresh complete: score=%.2f in %.1fs",
                total_score, elapsed)

    # Send Telegram summary if alerts available
    if alerts:
        try:
            alerts.send(_format_intel_brief(intelligence),
                       dedup_key="intelligence_brief",
                       dedup_cooldown_override=3600)
        except Exception: pass

    return intelligence


def _format_intel_brief(intel: dict) -> str:
    """Format intelligence summary for Telegram."""
    score = intel.get("total_score", 0)
    sentiment = intel.get("sentiment", "NEUTRAL")
    icon = "🟢🟢" if score > 5 else "🟢" if score > 2 else "🔴🔴" if score < -5 else "🔴" if score < -2 else "⚪"
    now = datetime.now().strftime("%d-%b %H:%M")

    lines = [
        f"🧠 <b>INTELLIGENCE BRIEF</b> | {now}",
        f"",
        f"  {icon} Score: <b>{score:+.1f}/10</b> → {sentiment}",
        f"",
    ]

    # Components
    comps = intel.get("components", {})
    if comps:
        lines.append("  <b>SIGNAL COMPONENTS</b>")
        for k, v in sorted(comps.items(), key=lambda x: abs(x[1]), reverse=True)[:6]:
            ci = "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
            lines.append(f"  {ci} {k:12} {v:+.2f}")
        lines.append("")

    # Top news
    bull = intel.get("top_bullish_news", [])
    bear = intel.get("top_bearish_news", [])
    if bull:
        lines.append("  <b>🟢 TOP BULLISH</b>")
        for h in bull[:2]:
            lines.append(f"  • {h.get('headline','')[:70]}")
    if bear:
        lines.append("  <b>🔴 TOP BEARISH</b>")
        for h in bear[:2]:
            lines.append(f"  • {h.get('headline','')[:70]}")

    # Commodity impacts
    impacts = intel.get("commodity_impacts", {})
    if impacts:
        lines += ["", "  <b>🛢️ COMMODITY IMPACT</b>"]
        for sector, impact in list(impacts.items())[:3]:
            lines.append(f"  {impact[:65]}")

    lines += [
        "",
        f"  ⏰ Refreshes every 15 min | 12 modules | 35 feeds",
        f"  Runtime: {intel.get('runtime_sec',0):.1f}s",
    ]
    return "\n".join(lines)


def format_telegram_report() -> str:
    """Full intelligence report for /intelligence Telegram command."""
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text())
            if time.time() - c.get("ts", 0) < _TTL * 4:
                return _format_intel_brief(c.get("data", {}))
    except Exception:
        pass
    return "⚠️ Intelligence not yet loaded — runs at 9:00 AM | Use /sentiment for news"
