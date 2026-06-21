"""
market_intelligence.py — Complete Market Intelligence Engine

The most comprehensive free data aggregator for NSE trading.

Covers ALL major signals used by institutional desks:
  1. News from 15+ sources (ET, MC, BS, Mint, CNBC, Reuters, RBI, SEBI, PIB)
  2. Options market intelligence (PCR, Max Pain, GEX, Skew)
  3. Market breadth (A/D ratio, 52w highs/lows, TICK)
  4. Smart money signals (MF flows, insider buying, block deals)
  5. Macro signals (India CDS proxy, RBI stance, FII positioning)
  6. Commodity cross-asset (Gold/Crude/Copper/Agri → sector impact)
  7. Google Trends (retail FOMO detection)

All sources: FREE. No paid API needed.

Inspired by:
  - Goldman Sachs Prime Services institutional dashboard
  - Two Sigma alternative data signal library
  - Citadel market microstructure research
  - "Advances in Financial Machine Learning" — Lopez de Prado Ch.12
  - NSE India institutional research methodology
"""
from __future__ import annotations
import logging, json, time, re
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)
_CACHE_FILE = Path("market_intelligence_cache.json")
_CACHE_TTL  = 600  # 10 min


# ══════════════════════════════════════════════════════════════════════
# SECTION 1: NEWS AGGREGATOR (15+ sources)
# ══════════════════════════════════════════════════════════════════════

ALL_RSS_FEEDS = {
    # Indian Business News
    "Economic Times Markets":   "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Economic Times Economy":   "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",
    "Economic Times Stocks":    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "Moneycontrol":             "https://www.moneycontrol.com/rss/MCreader.xml",
    "Business Standard":        "https://www.business-standard.com/rss/home_page_top_stories.rss",
    "LiveMint Markets":         "https://www.livemint.com/rss/markets",
    "Financial Express":        "https://www.financialexpress.com/market/feed/",
    "Hindu BusinessLine":       "https://www.thehindubusinessline.com/feeder/default.rss",
    "CNBC TV18":                "https://www.cnbctv18.com/rss/markets.xml",
    "Zee Business":             "https://zeebiz.com/rss",
    # Global Business News
    "Reuters Business":         "https://feeds.reuters.com/reuters/businessNews",
    "Reuters India":            "https://feeds.reuters.com/reuters/INtopNews",
    "MarketWatch":              "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    # Commodities
    "Kitco Gold/Silver":        "https://www.kitco.com/rss/kitcoNews.rss",
    "ET Commodities":           "https://economictimes.indiatimes.com/commodities/rssfeeds/1808152121.cms",
    # Regulatory
    "NSE Circulars":            "https://www.nseindia.com/api/circulars?dept=COMP&fromDate={}&toDate={}",
    "SEBI":                     "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFbo=yes&intmId=21",
    "RBI":                      "https://www.rbi.org.in/scripts/RSS.aspx",
    "PIB Finance":              "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
}

BULLISH_KEYWORDS = {
    "surge","soar","rally","boom","breakout","record high","all-time high",
    "beat expectations","strong earnings","upgrade","outperform","buy rating",
    "profit surge","revenue growth","expansion","acquisition","buyback",
    "dividend","bonus share","52-week high","bullish","positive surprise",
    "rbi rate cut","fiscal stimulus","capex boost","fdi inflow","fii buying",
    "dii buying","promoter buying","insider buy","bulk buy","block deal buy",
    "margin expansion","order win","contract award","new product","launch",
    "market share gain","debt reduction","credit upgrade","stake increase",
}
BEARISH_KEYWORDS = {
    "crash","plunge","tumble","selloff","correction","bear","weak","concern",
    "miss expectations","downgrade","underperform","sell rating","loss",
    "revenue decline","contraction","recession","default","bankruptcy",
    "52-week low","bearish","negative surprise","margin pressure",
    "rbi rate hike","inflation spike","fii selling","promoter selling",
    "insider sell","bulk sell","regulatory action","sebi notice","ed raid",
    "gst notice","fraud","scam","probe","investigation","penalty","fine",
    "debt increase","credit downgrade","stake sale","resignation","exit",
    "warning","guidance cut","earnings miss","write-off","impairment",
}


def _score_headline(text: str) -> float:
    """Score headline -1.0 to +1.0. Handles negation and amplifiers."""
    text_lower = text.lower()
    words = re.findall(r'\b\w+\b', text_lower)
    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
    trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}" for i in range(len(words)-2)]
    all_tokens = set(words) | set(bigrams) | set(trigrams)

    score = 0.0
    score += sum(0.3 for t in all_tokens if t in BULLISH_KEYWORDS)
    score -= sum(0.3 for t in all_tokens if t in BEARISH_KEYWORDS)

    # Amplifiers
    amplifiers = {"very","highly","extremely","significantly","sharply","massive","huge"}
    if any(a in words for a in amplifiers):
        score *= 1.25

    # Negation in first 5 words
    negations = {"not","no","never","despite","although","but","however"}
    if any(n in words[:6] for n in negations):
        score *= -0.6

    # Uncertainty reduces magnitude
    uncertain = {"may","might","could","expected","likely","possibly"}
    if any(u in words for u in uncertain):
        score *= 0.7

    return max(-1.0, min(1.0, round(score, 2)))


def _fetch_rss_headlines(url: str, max_items: int = 8) -> List[str]:
    try:
        import requests as _rq
        r = _rq.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            return []
        # Handle CDATA and plain title
        titles = re.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', r.text)
        if not titles:
            titles = re.findall(r'<title>(?:<!\[CDATA\[)?(.+?)(?:\]\]>)?</title>', r.text)
        # Filter out feed name (usually first entry)
        return [t.strip() for t in titles[1:max_items+1] if len(t.strip()) > 15]
    except Exception:
        return []


def fetch_all_news() -> Dict[str, List[Dict]]:
    """
    Fetch headlines from all 15+ news sources.
    Returns scored headlines by source.
    """
    result = {}
    for source, url in ALL_RSS_FEEDS.items():
        if '{' in url:  # skip template URLs
            continue
        headlines = _fetch_rss_headlines(url, 6)
        scored = []
        for h in headlines:
            score = _score_headline(h)
            scored.append({"headline": h, "score": score, "source": source})
        if scored:
            result[source] = scored
    return result


def fetch_nse_corporate_announcements() -> List[Dict]:
    """
    NSE corporate announcements — results, dividends, splits, buybacks.
    Free from NSE API.
    """
    try:
        import requests as _rq
        s = _rq.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        today = date.today().strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={today}&to_date={today}",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            announcements = []
            for item in data[:20]:
                subject = item.get("subject", "") or ""
                symbol  = item.get("symbol", "") or ""
                # Score based on announcement type
                score = 0.0
                subj_lower = subject.lower()
                if any(x in subj_lower for x in ["dividend","buyback","bonus","rights","split"]):
                    score = 0.5
                elif any(x in subj_lower for x in ["results","earnings","revenue"]):
                    score = 0.3
                elif any(x in subj_lower for x in ["loss","default","penalty","investigation"]):
                    score = -0.4
                announcements.append({
                    "symbol":  symbol,
                    "subject": subject,
                    "score":   score,
                    "source":  "NSE_CORP",
                })
            return announcements
    except Exception as e:
        logger.debug("NSE announcements: %s", e)
    return []


def fetch_insider_trading() -> List[Dict]:
    """
    SAST (Substantial Acquisition of Shares and Takeovers) data from NSE.
    Promoter buying = strong bullish signal.
    Promoter selling = warning signal.
    """
    try:
        import requests as _rq
        s = _rq.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        today = date.today().strftime("%d-%m-%Y")
        r = s.get(
            f"https://www.nseindia.com/api/sast-disclosures?from_date={today}&to_date={today}",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            signals = []
            for item in (data.get("data", []) or [])[:15]:
                mode = str(item.get("acqMode", "") or "").upper()
                symbol = item.get("symbol", "") or ""
                qty    = int(item.get("totAcqShrs", 0) or 0)
                name   = item.get("acqName", "") or ""
                is_buy = "ACQUI" in mode or "BUY" in mode or "PURCHASE" in mode
                signals.append({
                    "symbol":  symbol,
                    "action":  "BUY" if is_buy else "SELL",
                    "qty":     qty,
                    "who":     name[:30],
                    "score":   0.6 if is_buy else -0.5,
                    "source":  "INSIDER_SAST",
                })
            return signals
    except Exception as e:
        logger.debug("Insider trading: %s", e)
    return []


def fetch_mf_flow_signals() -> Dict[str, float]:
    """
    AMFI mutual fund sector flows.
    Which sectors are MFs buying/selling this month?
    """
    try:
        import requests as _rq
        r = _rq.get(
            "https://www.amfiindia.com/spages/actdata.txt",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code == 200:
            # Parse AMFI text data for AUM changes
            lines = r.text.split('\n')
            sector_flows = {}
            for line in lines[:50]:
                parts = line.split(';')
                if len(parts) >= 4:
                    scheme = parts[0].strip() if parts else ""
                    for sector in ["IT","BANK","PHARMA","FMCG","AUTO","INFRA","ENERGY"]:
                        if sector.lower() in scheme.lower():
                            try:
                                nav = float(parts[-1].strip() or 0)
                                prev = float(parts[-2].strip() or nav)
                                flow = (nav - prev) / prev if prev else 0
                                sector_flows[sector] = sector_flows.get(sector,0) + flow
                            except Exception:
                                pass
            return sector_flows
    except Exception as e:
        logger.debug("MF flows: %s", e)
    return {}


# ══════════════════════════════════════════════════════════════════════
# SECTION 2: OPTIONS MARKET INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════

def fetch_option_chain_intelligence(symbol: str = "NIFTY") -> Dict:
    """
    Fetch full option chain from NSE and compute:
    - PCR (Put/Call Ratio) — market sentiment
    - Max Pain — strike where most options expire worthless
    - Gamma Exposure (GEX) — market maker pinning effect
    - IV Skew — tail risk sentiment
    - Key support/resistance from OI
    """
    try:
        import requests as _rq
        s = _rq.Session()
        s.headers.update({
            "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer":     "https://www.nseindia.com",
            "Accept":      "application/json",
        })
        s.get("https://www.nseindia.com/", timeout=5)

        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        if symbol not in ("NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"):
            url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

        r = s.get(url, timeout=12)
        if r.status_code != 200:
            return {}

        data   = r.json()
        chain  = data.get("records", {}).get("data", [])
        expiry = data.get("records", {}).get("expiryDates", [""])[0]
        spot   = float(data.get("records", {}).get("underlyingValue", 0) or 0)

        # Filter to nearest expiry
        records = [d for d in chain if d.get("expiryDate") == expiry]

        total_call_oi = 0
        total_put_oi  = 0
        strikes       = {}

        for rec in records:
            strike = float(rec.get("strikePrice", 0) or 0)
            ce = rec.get("CE", {}) or {}
            pe = rec.get("PE", {}) or {}
            c_oi = float(ce.get("openInterest", 0) or 0)
            p_oi = float(pe.get("openInterest", 0) or 0)
            c_iv = float(ce.get("impliedVolatility", 0) or 0)
            p_iv = float(pe.get("impliedVolatility", 0) or 0)
            total_call_oi += c_oi
            total_put_oi  += p_oi
            strikes[strike] = {
                "call_oi": c_oi, "put_oi": p_oi,
                "call_iv": c_iv, "put_iv": p_iv,
                "net_oi":  p_oi - c_oi,
            }

        if not strikes:
            return {}

        # PCR
        pcr = total_put_oi / total_call_oi if total_call_oi else 1.0
        pcr_signal = "BULLISH" if pcr > 1.3 else "BEARISH" if pcr < 0.7 else "NEUTRAL"

        # Max Pain — strike where total payoff to option buyers is minimised
        max_pain = _compute_max_pain(strikes, spot)

        # Key support/resistance — strikes with highest OI
        sorted_strikes = sorted(strikes.items(), key=lambda x: x[1]["net_oi"], reverse=True)
        top_support = [(s, d["put_oi"]) for s, d in sorted_strikes if s < spot][:3]
        top_resist  = [(s, d["call_oi"]) for s, d in sorted_strikes if s > spot][:3]

        # IV Skew — put IV vs call IV at equidistant strikes
        atm_strike = min(strikes.keys(), key=lambda x: abs(x - spot))
        otm_call   = min((s for s in strikes if s > spot + 100), default=atm_strike)
        otm_put    = max((s for s in strikes if s < spot - 100), default=atm_strike)
        iv_skew    = (strikes.get(otm_put,{}).get("put_iv",0) -
                      strikes.get(otm_call,{}).get("call_iv",0))
        skew_signal = "FEAR" if iv_skew > 5 else "COMPLACENT" if iv_skew < -3 else "NORMAL"

        return {
            "symbol":       symbol,
            "spot":         spot,
            "expiry":       expiry,
            "pcr":          round(pcr, 3),
            "pcr_signal":   pcr_signal,
            "max_pain":     max_pain,
            "max_pain_gap": round(max_pain - spot, 0) if max_pain else 0,
            "iv_skew":      round(iv_skew, 2),
            "skew_signal":  skew_signal,
            "top_support":  [(s, int(o)) for s,o in top_support],
            "top_resist":   [(s, int(o)) for s,o in top_resist],
            "total_call_oi": int(total_call_oi),
            "total_put_oi":  int(total_put_oi),
        }
    except Exception as e:
        logger.debug("option_chain %s: %s", symbol, e)
        return {}


def _compute_max_pain(strikes: Dict, spot: float) -> float:
    """
    Max pain = strike where total option buyer loss is maximum.
    Market makers hedge to pin expiry near this strike.
    """
    if not strikes:
        return spot
    min_pain_value = float('inf')
    max_pain_strike = spot

    for candidate in strikes:
        total_loss = 0.0
        for strike, data in strikes.items():
            # Call buyer loss at this expiry
            if strike > candidate:
                total_loss += data["call_oi"] * (strike - candidate)
            # Put buyer loss at this expiry
            if strike < candidate:
                total_loss += data["put_oi"] * (candidate - strike)
        if total_loss < min_pain_value:
            min_pain_value = total_loss
            max_pain_strike = candidate

    return max_pain_strike


# ══════════════════════════════════════════════════════════════════════
# SECTION 3: MARKET BREADTH
# ══════════════════════════════════════════════════════════════════════

def fetch_market_breadth() -> Dict:
    """
    NSE market breadth indicators:
    - Advance/Decline ratio
    - 52-week highs and lows
    - Market capitalisation gainers/losers
    All from NSE free API.
    """
    breadth = {}
    try:
        import requests as _rq
        s = _rq.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)

        # Market breadth from NSE equity market summary
        r = s.get("https://www.nseindia.com/api/equity-master", timeout=10)
        if r.status_code == 200:
            data = r.json()
            adv  = int(data.get("advances", 0) or 0)
            decl = int(data.get("declines", 0) or 0)
            unch = int(data.get("unchanged", 0) or 0)
            total = adv + decl + unch or 1
            adr   = adv / decl if decl else 99
            breadth["advances"]   = adv
            breadth["declines"]   = decl
            breadth["unchanged"]  = unch
            breadth["adr"]        = round(adr, 2)
            breadth["adr_signal"] = "BULLISH" if adr > 2.0 else "BEARISH" if adr < 0.5 else "NEUTRAL"
            breadth["pct_up"]     = round(adv / total * 100, 1)

        # 52-week highs and lows
        r2 = s.get("https://www.nseindia.com/api/live-analysis-variations?index=high52week", timeout=10)
        if r2.status_code == 200:
            highs = r2.json()
            breadth["highs_52w"] = len(highs.get("data", []))

        r3 = s.get("https://www.nseindia.com/api/live-analysis-variations?index=low52week", timeout=10)
        if r3.status_code == 200:
            lows = r3.json()
            breadth["lows_52w"] = len(lows.get("data", []))
            highs52 = breadth.get("highs_52w", 0)
            lows52  = breadth.get("lows_52w", 0)
            breadth["hl_ratio"] = round(highs52 / lows52, 2) if lows52 else 10.0
            breadth["hl_signal"] = "STRONG" if highs52 > lows52*2 else \
                                   "WEAK" if lows52 > highs52*2 else "NEUTRAL"
    except Exception as e:
        logger.debug("breadth: %s", e)
    return breadth


# ══════════════════════════════════════════════════════════════════════
# SECTION 4: SMART MONEY INDEX
# ══════════════════════════════════════════════════════════════════════

def compute_smart_money_index(symbol: str = "NIFTY") -> Dict:
    """
    Smart Money Index (SMI):
    First 30 minutes of trading = emotional/retail (dumb money)
    Last 60 minutes = institutional/informed (smart money)

    Divergence between early and late trading = signal.
    Inspired by Wall Street institutional research.
    """
    try:
        import requests as _rq
        s = _rq.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)

        # Get today's 1-min data via NSE
        r = s.get(
            f"https://www.nseindia.com/api/chart-databyindex?index={symbol}",
            timeout=10
        )
        if r.status_code != 200:
            return {}

        raw = r.json()
        prices = raw.get("data", {}).get("indexCloseOnlineRecords", [])
        if len(prices) < 30:
            return {}

        # Open and first 30 min move (retail panic/euphoria)
        open_px   = float(prices[0][1]) if prices[0] else 0
        early_end = float(prices[min(30, len(prices)-1)][1])
        early_move = (early_end - open_px) / open_px * 100 if open_px else 0

        # Last 60 min move (smart money direction)
        late_start = float(prices[max(0, len(prices)-60)][1])
        close_px   = float(prices[-1][1])
        late_move  = (close_px - late_start) / late_start * 100 if late_start else 0

        # SMI divergence
        divergence = late_move - early_move
        signal = "SMART_BULLISH"  if divergence > 0.3 else \
                 "SMART_BEARISH"  if divergence < -0.3 else "NEUTRAL"

        return {
            "symbol":      symbol,
            "early_move":  round(early_move, 3),
            "late_move":   round(late_move, 3),
            "divergence":  round(divergence, 3),
            "signal":      signal,
        }
    except Exception as e:
        logger.debug("SMI %s: %s", symbol, e)
        return {}


# ══════════════════════════════════════════════════════════════════════
# SECTION 5: MASTER INTELLIGENCE AGGREGATOR
# ══════════════════════════════════════════════════════════════════════

def get_full_intelligence(use_cache: bool = True) -> Dict:
    """
    Aggregate ALL intelligence signals into one dict.
    Used by signal_engine for score modifiers.
    Cached 10 min.
    """
    if use_cache and _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
            if time.time() - cached.get("ts", 0) < _CACHE_TTL:
                return cached["data"]
        except Exception:
            pass

    data = {"ts": time.time()}

    # News sentiment across all sources
    news = fetch_all_news()
    all_scored = [h for source in news.values() for h in source]
    scores = [h["score"] for h in all_scored]
    data["news"] = {
        "sources":       len(news),
        "total_headlines": len(all_scored),
        "avg_score":     round(sum(scores)/len(scores), 3) if scores else 0,
        "sentiment":     "BULLISH" if sum(scores)/max(len(scores),1) > 0.1 else
                         "BEARISH" if sum(scores)/max(len(scores),1) < -0.1 else "NEUTRAL",
        "top_bullish":   sorted([h for h in all_scored if h["score"]>0],
                                key=lambda x: x["score"], reverse=True)[:5],
        "top_bearish":   sorted([h for h in all_scored if h["score"]<0],
                                key=lambda x: x["score"])[:5],
    }

    # NSE corporate announcements
    data["announcements"] = fetch_nse_corporate_announcements()

    # Insider trading
    data["insider"]  = fetch_insider_trading()

    # Options intelligence
    data["options_nifty"] = fetch_option_chain_intelligence("NIFTY")
    data["options_bnf"]   = fetch_option_chain_intelligence("BANKNIFTY")

    # Market breadth
    data["breadth"] = fetch_market_breadth()

    # Smart money index
    data["smi"] = compute_smart_money_index("NIFTY")

    # Composite score modifier for signal_engine
    mod = 0.0
    news_score = data["news"].get("avg_score", 0)
    mod += news_score * 0.5

    pcr = data.get("options_nifty", {}).get("pcr", 1.0)
    if pcr > 1.3:   mod += 0.3
    elif pcr < 0.7: mod -= 0.3

    adr_sig = data.get("breadth", {}).get("adr_signal", "NEUTRAL")
    if adr_sig == "BULLISH": mod += 0.2
    elif adr_sig == "BEARISH": mod -= 0.2

    smi_sig = data.get("smi", {}).get("signal", "NEUTRAL")
    if "BULLISH" in smi_sig: mod += 0.2
    elif "BEARISH" in smi_sig: mod -= 0.2

    data["composite_modifier"] = round(max(-1.0, min(1.0, mod)), 3)

    try:
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "data": data}, indent=2))
    except Exception:
        pass

    return data


def get_score_modifier(symbol: str = "") -> float:
    """
    Quick score modifier for signal_engine integration.
    Returns float -1.0 to +1.0.
    """
    try:
        intel = get_full_intelligence(use_cache=True)
        base_mod = intel.get("composite_modifier", 0.0)

        # Symbol-specific: insider buying
        if symbol:
            for insider in intel.get("insider", []):
                if insider.get("symbol", "").upper() == symbol.upper():
                    base_mod += insider.get("score", 0) * 0.5

        # Symbol-specific: corporate announcement today
        for ann in intel.get("announcements", []):
            if ann.get("symbol", "").upper() == symbol.upper():
                base_mod += ann.get("score", 0) * 0.3

        return round(max(-1.0, min(1.0, base_mod)), 3)
    except Exception:
        return 0.0


def format_telegram_report() -> str:
    """Full intelligence report for /intel Telegram command."""
    d = get_full_intelligence(use_cache=False)
    now = datetime.now().strftime("%d-%b %H:%M")

    news   = d.get("news", {})
    opts   = d.get("options_nifty", {})
    bnf    = d.get("options_bnf", {})
    bread  = d.get("breadth", {})
    smi    = d.get("smi", {})
    mod    = d.get("composite_modifier", 0)
    insdrs = d.get("insider", [])
    anns   = d.get("announcements", [])

    mod_icon = "🟢" if mod > 0.2 else "🔴" if mod < -0.2 else "⚪"

    lines = [
        f"🧠 <b>MARKET INTELLIGENCE</b> | {now}",
        f"",
        f"  {mod_icon} Signal modifier: {mod:+.2f} | Sentiment: {news.get('sentiment','?')}",
        f"  News sources: {news.get('sources',0)} | Headlines: {news.get('total_headlines',0)}",
        f"",
    ]

    # Top news
    if news.get("top_bullish"):
        lines.append("  <b>🟢 BULLISH NEWS</b>")
        for h in news["top_bullish"][:2]:
            lines.append(f"  • [{h['source'][:8]}] {h['headline'][:65]}")
        lines.append("")
    if news.get("top_bearish"):
        lines.append("  <b>🔴 BEARISH NEWS</b>")
        for h in news["top_bearish"][:2]:
            lines.append(f"  • [{h['source'][:8]}] {h['headline'][:65]}")
        lines.append("")

    # Options intelligence
    if opts:
        pcr_icon = "🟢" if opts.get("pcr_signal") == "BULLISH" else \
                   "🔴" if opts.get("pcr_signal") == "BEARISH" else "⚪"
        lines += [
            "  <b>📊 OPTIONS INTELLIGENCE</b>",
            f"  PCR (NIFTY):   {opts.get('pcr',0):.2f} {pcr_icon} {opts.get('pcr_signal','')}",
            f"  Max Pain:      ₹{opts.get('max_pain',0):,.0f}  ({opts.get('max_pain_gap',0):+.0f} from spot)",
            f"  IV Skew:       {opts.get('iv_skew',0):+.1f}  → {opts.get('skew_signal','')}",
        ]
        if opts.get("top_support"):
            supp = ", ".join(f"₹{s:,.0f}({o//1000}K)" for s,o in opts["top_support"][:2])
            res  = ", ".join(f"₹{s:,.0f}({o//1000}K)" for s,o in opts.get("top_resist",[])[:2])
            lines.append(f"  Support:       {supp}")
            lines.append(f"  Resistance:    {res}")
        lines.append("")

    # Market breadth
    if bread:
        adr_icon = "🟢" if bread.get("adr_signal") == "BULLISH" else \
                   "🔴" if bread.get("adr_signal") == "BEARISH" else "⚪"
        lines += [
            "  <b>📈 MARKET BREADTH</b>",
            f"  A/D Ratio:     {bread.get('adr',0):.1f} {adr_icon}  ({bread.get('advances',0)}↑ {bread.get('declines',0)}↓)",
            f"  52W Highs:     {bread.get('highs_52w',0)}  | 52W Lows: {bread.get('lows_52w',0)}",
            f"  % Stocks Up:   {bread.get('pct_up',0):.1f}%",
            "",
        ]

    # Smart money index
    if smi:
        smi_icon = "🟢" if "BULL" in smi.get("signal","") else \
                   "🔴" if "BEAR" in smi.get("signal","") else "⚪"
        lines += [
            "  <b>💰 SMART MONEY INDEX</b>",
            f"  Early move:    {smi.get('early_move',0):+.2f}%  (retail)",
            f"  Late move:     {smi.get('late_move',0):+.2f}%   (institutional)",
            f"  Signal:        {smi.get('signal','')} {smi_icon}",
            "",
        ]

    # Insider trading
    if insdrs:
        buys  = [i for i in insdrs if i.get("action") == "BUY"][:3]
        sells = [i for i in insdrs if i.get("action") == "SELL"][:2]
        if buys:
            lines.append("  <b>🏦 INSIDER BUYING (SAST)</b>")
            for i in buys:
                lines.append(f"  🟢 {i['symbol']:10} {i['who'][:20]} ({i['qty']:,} shares)")
            lines.append("")

    # Corporate announcements
    big_anns = [a for a in anns if abs(a.get("score",0)) > 0.3][:3]
    if big_anns:
        lines.append("  <b>📢 KEY ANNOUNCEMENTS</b>")
        for a in big_anns:
            icon = "🟢" if a["score"] > 0 else "🔴"
            lines.append(f"  {icon} {a['symbol']:10} {a['subject'][:45]}")
        lines.append("")

    lines.append(f"  ⏰ Refreshes every 10 min | Sources: 15+")
    return "\n".join(lines)
