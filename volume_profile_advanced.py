"""
volume_profile_advanced.py — Institutional Volume Profile Engine

Implements everything from @Tradewrite's methodology:

1. VRVP  — Visible Range Volume Profile (full session)
2. SVP   — Session Volume Profile (current day)
3. HVN   — High Volume Nodes (institutional order clusters)
4. LVN   — Low Volume Nodes (price acceleration zones)
5. POC   — Point of Control alignment across sessions
6. Value Area — 70% of volume zone (institutional range)
7. Failed Auction — price enters value area and gets rejected
8. EMA Cloud (20/50) + Supply/Demand filter (Tradewrite method)
9. Global Macro monitor — GIFT Nifty, US data, geopolitical proxy

Core insight from posts:
  "Price bounces because INSTITUTIONAL ORDERS are sitting there.
   Volume shows you WHERE those orders are."
  "Draw levels ONCE from POC + HVN alignment. They work forever."
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CORE: Build Volume Profile from OHLCV data
# ─────────────────────────────────────────────────────────────────────────────
def build_volume_profile(
    df: pd.DataFrame,
    n_bins: int = 100,
) -> Dict:
    """
    Build full Volume Profile from OHLCV data.
    Returns POC, Value Area, HVN, LVN, and raw profile.

    n_bins = 100 mimics TradingView VRVP row_size=100.
    """
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 5 or "volume" not in df_c.columns:
            return {}

        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        c = df_c["close"].values
        v = df_c["volume"].values

        price_min = float(np.min(l))
        price_max = float(np.max(h))
        if price_max <= price_min: return {}

        bin_size = (price_max - price_min) / n_bins
        bins     = np.linspace(price_min, price_max, n_bins + 1)
        vol_profile = np.zeros(n_bins)

        # Distribute each bar's volume across price bins it touched
        for i in range(len(c)):
            bar_lo = float(l[i]); bar_hi = float(h[i]); bar_vol = float(v[i])
            if bar_hi <= bar_lo or bar_vol <= 0: continue
            bar_range = bar_hi - bar_lo
            for b in range(n_bins):
                bin_lo = bins[b]; bin_hi = bins[b+1]
                overlap = max(0, min(bar_hi, bin_hi) - max(bar_lo, bin_lo))
                vol_profile[b] += bar_vol * (overlap / bar_range)

        total_vol = float(np.sum(vol_profile))
        if total_vol <= 0: return {}

        # POC = bin with highest volume
        poc_idx  = int(np.argmax(vol_profile))
        poc_price= float(bins[poc_idx] + bin_size / 2)

        # Value Area = 70% of total volume centred on POC
        va_vol_target = total_vol * 0.70
        va_lo_idx = poc_idx; va_hi_idx = poc_idx
        va_vol = float(vol_profile[poc_idx])
        while va_vol < va_vol_target:
            expand_lo = vol_profile[va_lo_idx - 1] if va_lo_idx > 0 else 0
            expand_hi = vol_profile[va_hi_idx + 1] if va_hi_idx < n_bins - 1 else 0
            if expand_hi >= expand_lo and va_hi_idx < n_bins - 1:
                va_hi_idx += 1; va_vol += float(vol_profile[va_hi_idx])
            elif va_lo_idx > 0:
                va_lo_idx -= 1; va_vol += float(vol_profile[va_lo_idx])
            else: break

        vah = float(bins[va_hi_idx + 1])  # Value Area High
        val = float(bins[va_lo_idx])       # Value Area Low

        # High Volume Nodes (HVN) — above 150% of average
        avg_vol_bin = total_vol / n_bins
        hvn_indices = [i for i,v in enumerate(vol_profile) if v > avg_vol_bin * 1.5]
        hvn_prices  = [float(bins[i] + bin_size/2) for i in hvn_indices]

        # Low Volume Nodes (LVN) — below 30% of average (price accelerates through)
        lvn_indices = [i for i,v in enumerate(vol_profile)
                       if v < avg_vol_bin * 0.3 and v > 0]
        lvn_prices  = [float(bins[i] + bin_size/2) for i in lvn_indices]

        return {
            "poc":         poc_price,
            "vah":         vah,
            "val":         val,
            "hvn":         sorted(hvn_prices),
            "lvn":         sorted(lvn_prices),
            "profile":     vol_profile.tolist(),
            "bins":        bins.tolist(),
            "total_vol":   total_vol,
            "bin_size":    bin_size,
            "price_min":   price_min,
            "price_max":   price_max,
        }
    except Exception as e:
        logger.debug("build_volume_profile: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 1: HVN/LVN + POC Zone Strategy
# ─────────────────────────────────────────────────────────────────────────────
def run_vrvp_zone_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    VRVP Zone Strategy — institutional supply/demand via volume nodes.

    BUY signals:
      - Price at/near HVN (institutional support) after decline
      - Price at VAL (Value Area Low) — 70% vol zone bottom
      - Price crossing POC upward with volume

    SELL signals:
      - Price at/near HVN overhead (institutional resistance)
      - Price at VAH (Value Area High) — hitting top of vol zone
      - Price crossing POC downward with volume

    LVN = price will move FAST through these (acceleration zones).
    """
    empty = {"strategy": "vrvp_zone", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20 or "volume" not in df_c.columns: return empty

        vp   = build_volume_profile(df_c, n_bins=100)
        if not vp: return empty

        price   = float(df_c["close"].iloc[-1])
        prev    = float(df_c["close"].iloc[-2])
        poc     = vp["poc"]; vah = vp["vah"]; val = vp["val"]
        hvn     = vp["hvn"]; lvn = vp["lvn"]
        tol     = vp["bin_size"] * 3  # tolerance = 3 bins

        buy_score = sell_score = 0.0
        signal_detail = ""

        # At Value Area Low — institutional demand zone
        if abs(price - val) <= tol and price >= val:
            buy_score += 3.5
            signal_detail = f"At VAL {val:.0f}"

        # At Value Area High — institutional supply zone
        if abs(price - vah) <= tol and price <= vah:
            sell_score += 3.5
            signal_detail = f"At VAH {vah:.0f}"

        # POC cross — most significant level
        if prev < poc <= price:  # price crossed POC upward
            buy_score += 4.0
            signal_detail = f"POC cross up {poc:.0f}"
        elif prev > poc >= price:  # price crossed POC downward
            sell_score += 4.0
            signal_detail = f"POC cross down {poc:.0f}"

        # Near HVN (support/resistance from institutional orders)
        for hvn_p in hvn:
            if abs(price - hvn_p) <= tol:
                if price >= hvn_p and prev < hvn_p:  # bouncing off HVN
                    buy_score += 2.5
                    signal_detail = f"HVN bounce {hvn_p:.0f}"
                elif price <= hvn_p and prev > hvn_p:  # rejected at HVN
                    sell_score += 2.5
                    signal_detail = f"HVN rejection {hvn_p:.0f}"

        # Near LVN — fast move expected (acceleration)
        for lvn_p in lvn:
            if abs(price - lvn_p) <= tol:
                if price > prev:  # moving up through LVN
                    buy_score += 1.5
                elif price < prev:
                    sell_score += 1.5

        if buy_score >= 3.0 and buy_score > sell_score:
            return {"strategy": "vrvp_zone", "score": round(buy_score, 2),
                    "direction": "BUY", "side": "BUY",
                    "poc": round(poc, 2), "vah": round(vah, 2), "val": round(val, 2),
                    "detail": signal_detail}
        if sell_score >= 3.0:
            return {"strategy": "vrvp_zone", "score": round(sell_score, 2),
                    "direction": "SELL", "side": "SELL",
                    "poc": round(poc, 2), "vah": round(vah, 2), "val": round(val, 2),
                    "detail": signal_detail}
    except Exception as e: logger.debug("vrvp_zone: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 2: Failed Auction (Tradewrite concept)
# ─────────────────────────────────────────────────────────────────────────────
def run_failed_auction_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Failed Auction — price enters Value Area but fails to follow through.

    When price breaks into the Value Area and then reverses quickly,
    it signals institutional rejection — they didn't let it stay there.

    BUY: Price breaks below VAL → fails → reverses back above VAL
         (Bears tried to break support, couldn't = bullish signal)
    SELL: Price breaks above VAH → fails → reverses back below VAH
         (Bulls tried to break resistance, couldn't = bearish signal)

    This is one of the highest-probability Market Profile setups.
    """
    empty = {"strategy": "failed_auction", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 10 or "volume" not in df_c.columns: return empty

        vp = build_volume_profile(df_c, n_bins=100)
        if not vp: return empty

        c   = df_c["close"].values
        l   = df_c["low"].values  if "low"  in df_c.columns else c
        h   = df_c["high"].values if "high" in df_c.columns else c
        vah = vp["vah"]; val = vp["val"]
        tol = vp["bin_size"] * 2

        # Look back 3 bars for failed auction pattern
        if len(c) < 4: return empty

        # Failed bearish auction: price dipped below VAL then closed back above
        dipped_below = any(float(l[-i]) < val - tol for i in range(1, 4))
        now_above    = float(c[-1]) > val

        # Failed bullish auction: price spiked above VAH then closed back below
        spiked_above = any(float(h[-i]) > vah + tol for i in range(1, 4))
        now_below    = float(c[-1]) < vah

        if dipped_below and now_above and float(c[-1]) > float(c[-2]):
            return {"strategy": "failed_auction", "score": 5.0,
                    "direction": "BUY", "side": "BUY",
                    "val": round(val, 2), "detail": "Failed bearish auction at VAL"}
        if spiked_above and now_below and float(c[-1]) < float(c[-2]):
            return {"strategy": "failed_auction", "score": 5.0,
                    "direction": "SELL", "side": "SELL",
                    "vah": round(vah, 2), "detail": "Failed bullish auction at VAH"}
    except Exception as e: logger.debug("failed_auction: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 3: EMA Cloud (20/50) + Supply/Demand Filter (Tradewrite method)
# ─────────────────────────────────────────────────────────────────────────────
def run_ema_cloud_sd_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    EMA Cloud (20/50) + Supply/Demand Filter — @Tradewrite system.

    Setup (direct from posts):
      EMA Cloud 20/50 → shows direction (cloud = trend)
      Supply/Demand levels → show WHERE to enter

    Rule: "Cloud flip only matters if price is INSIDE supply/demand level."
    Without that filter: noise. With it: high-probability trade.

    BUY:  Price above cloud (20 > 50) AND price at demand zone (VAL/HVN)
    SELL: Price below cloud (20 < 50) AND price at supply zone (VAH/HVN)

    "Cloud for direction. Levels for entry."
    """
    empty = {"strategy": "ema_cloud_sd", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 55: return empty
        c = df_c["close"].values

        # EMA 20 and 50
        def ema(arr, n):
            k = 2.0/(n+1); e = float(arr[0])
            for v in arr[1:]: e = float(v)*k + e*(1-k)
            return e

        ema20 = ema(c[-25:], 20); ema50 = ema(c[-60:], 50)
        price  = float(c[-1]);    prev  = float(c[-2])

        # Cloud direction
        bull_cloud = ema20 > ema50  # price above cloud
        bear_cloud = ema20 < ema50

        # Just flipped (cloud flip = momentum shift)
        prev_ema20 = ema(c[-26:-1], 20); prev_ema50 = ema(c[-61:-1], 50)
        just_flipped_bull = prev_ema20 <= prev_ema50 and ema20 > ema50
        just_flipped_bear = prev_ema20 >= prev_ema50 and ema20 < ema50

        # Build volume profile to get supply/demand zones
        vp  = build_volume_profile(df_c, n_bins=80)
        at_demand = at_supply = False
        tol = 0.003 * price  # 0.3% tolerance

        if vp:
            val = vp["val"]; vah = vp["vah"]; hvn = vp["hvn"]
            at_demand = abs(price - val) <= tol or any(abs(price-h) <= tol for h in hvn if h < price)
            at_supply = abs(price - vah) <= tol or any(abs(price-h) <= tol for h in hvn if h > price)

        # TRADEWRITE RULE: Cloud flip + price at S/D zone
        if just_flipped_bull and at_demand:
            return {"strategy": "ema_cloud_sd", "score": 5.5,
                    "direction": "BUY", "side": "BUY",
                    "ema20": round(ema20, 2), "ema50": round(ema50, 2),
                    "detail": "Cloud flip bullish at demand zone"}
        if just_flipped_bear and at_supply:
            return {"strategy": "ema_cloud_sd", "score": 5.5,
                    "direction": "SELL", "side": "SELL",
                    "ema20": round(ema20, 2), "ema50": round(ema50, 2),
                    "detail": "Cloud flip bearish at supply zone"}
        # Continuation: cloud aligned + at zone
        if bull_cloud and at_demand and price > prev:
            return {"strategy": "ema_cloud_sd", "score": 3.5,
                    "direction": "BUY", "side": "BUY",
                    "detail": "Bullish cloud + demand zone entry"}
        if bear_cloud and at_supply and price < prev:
            return {"strategy": "ema_cloud_sd", "score": 3.5,
                    "direction": "SELL", "side": "SELL",
                    "detail": "Bearish cloud + supply zone entry"}
    except Exception as e: logger.debug("ema_cloud_sd: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL MACRO MONITOR — GIFT Nifty + US Events + Geopolitical proxy
# ─────────────────────────────────────────────────────────────────────────────
def get_global_macro_score(
    symbol:         str   = "NIFTY",
    use_cache_mins: int   = 30,
) -> Dict:
    """
    Global macro score modifier.

    Tracks (all free sources):
    1. GIFT Nifty via yfinance (^NSEI pre-market gap)
    2. S&P 500 futures direction (ES1! proxy via ^GSPC)
    3. India VIX level
    4. Crude oil (Trump/Iran proxy)
    5. US 10Y yield (risk-on/off)
    6. USD/INR

    Returns score_mod (-3.0 to +3.0) and direction bias.
    """
    import time
    _cache = getattr(get_global_macro_score, '_cache', {})
    _ts    = getattr(get_global_macro_score, '_ts', 0)
    if time.time() - _ts < use_cache_mins * 60 and _cache:
        return _cache

    score_mod = 0.0
    details   = []
    data      = {}

    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        tickers = {
            "spy":    "^GSPC",    # S&P 500 (US market direction)
            "crude":  "CL=F",     # Crude oil (geopolitical proxy)
            "usdinr": "INRUSD=X", # USD/INR
            "us10y":  "^TNX",     # US 10Y yield
        }
        for key, ticker in tickers.items():
            try:
                df = yf.download(ticker, period="2d", interval="1d",
                                 progress=False, auto_adjust=True)
                if df is None or len(df) < 2: continue
                c = df["Close"]
                if hasattr(c, "columns"): c = c.iloc[:, 0]
                pct = (float(c.iloc[-1]) - float(c.iloc[-2])) / float(c.iloc[-2]) * 100
                data[key] = pct
            except Exception: pass

        # S&P direction → Nifty follows ~80% of time
        spy_pct = data.get("spy", 0)
        if spy_pct > 1.5:
            score_mod += 1.5; details.append(f"S&P +{spy_pct:.1f}% 🟢")
        elif spy_pct > 0.5:
            score_mod += 0.8; details.append(f"S&P +{spy_pct:.1f}%")
        elif spy_pct < -1.5:
            score_mod -= 1.5; details.append(f"S&P {spy_pct:.1f}% 🔴")
        elif spy_pct < -0.5:
            score_mod -= 0.8; details.append(f"S&P {spy_pct:.1f}%")

        # Crude oil — inverse for India (higher crude = bad for Nifty)
        crude_pct = data.get("crude", 0)
        if crude_pct < -3:  # oil crash like Iran news = bullish India
            score_mod += 1.0; details.append(f"Crude {crude_pct:.1f}% 🟢")
        elif crude_pct > 3:
            score_mod -= 0.8; details.append(f"Crude +{crude_pct:.1f}% 🔴")

        # USD/INR — INRUSD is inverse, rupee weakening = bearish
        usdinr = data.get("usdinr", 0)
        if usdinr < -0.3:   # rupee strengthening
            score_mod += 0.5; details.append("INR strong")
        elif usdinr > 0.3:
            score_mod -= 0.5; details.append("INR weak")

        # US 10Y yield — rising yield = risk off
        yield_pct = data.get("us10y", 0)
        if yield_pct > 3:   # yield spiking
            score_mod -= 0.5; details.append("Yields spiking 🔴")
        elif yield_pct < -3:
            score_mod += 0.5; details.append("Yields falling 🟢")

    except Exception as e:
        logger.debug("global_macro: %s", e)

    result = {
        "score_mod":  round(max(-3.0, min(3.0, score_mod)), 2),
        "bias":       "BULLISH" if score_mod > 0.5 else "BEARISH" if score_mod < -0.5 else "NEUTRAL",
        "details":    details,
        "data":       data,
    }
    get_global_macro_score._cache = result
    get_global_macro_score._ts    = __import__("time").time()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# TRUMP SENTIMENT FILTER — keyword-based market direction
# ─────────────────────────────────────────────────────────────────────────────
def get_president_sentiment_score() -> Dict:
    """
    President/geopolitical sentiment proxy — monitors news headlines for presidential/geopolitical keywords.
    
    As the post notes: 'President says 'Beautiful' → BUY, Disaster → SELL'
    
    Checks free RSS feeds from Reuters/Bloomberg for presidential/geopolitical keywords.
    Cached 15 minutes to avoid excessive API calls.
    
    Returns score_mod (-2.0 to +2.0) for signal adjustment.
    
    BULLISH keywords: peace, deal, agreement, growth, progress, record
    BEARISH keywords: disaster, tariff, war, sanctions, crisis, collapse
    """
    import time
    _c = getattr(get_president_sentiment_score, '_cache', {})
    _t = getattr(get_president_sentiment_score, '_ts', 0)
    if time.time() - _t < 900 and _c:  # 15 min cache
        return _c

    bullish_words = {
        "peace", "deal", "agreement", "ceasefire", "negotiate", "progress",
        "growth", "rally", "record", "surplus", "reform", "boost",
        "recovery", "stimulus", "invest", "approve", "historic"
    }
    bearish_words = {
        "disaster", "tariff", "war", "sanctions", "attack", "strike",
        "crisis", "collapse", "recession", "inflation", "default",
        "conflict", "tension", "protest", "ban", "restrict", "dump"
    }

    score = 0.0
    details = []

    try:
        import urllib.request
        # Google News RSS for Trump headlines (free, no API key)
        # Search both Indian PM + US President + global geopolitical news
        urls = [
            "https://news.google.com/rss/search?q=India+PM+economy+market&hl=en-IN&gl=IN&ceid=IN:en",
            "https://news.google.com/rss/search?q=geopolitical+market+India&hl=en&gl=US&ceid=US:en",
        ]
        content = ""
        for url in urls:
            try:
                req2 = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req2, timeout=4) as r2:
                    content += r2.read().decode("utf-8", errors="ignore").lower()
            except Exception: pass
        url = ""  # already fetched above
        # content already fetched above in multi-url loop
        if not content: raise ValueError("no content")

        # Score based on keyword frequency in last 5 headlines
        import re
        titles = re.findall(r'<title>(.*?)</title>', content)[:6]
        for title in titles[1:6]:  # skip first (feed title)
            for w in bullish_words:
                if w in title:
                    score += 0.4; details.append(f"'{w}' in headline")
            for w in bearish_words:
                if w in title:
                    score -= 0.4; details.append(f"'{w}' in headline")
    except Exception as e:
        logger.debug("president_sentiment: %s", e)

    score = max(-2.0, min(2.0, score))
    result = {
        "score_mod": round(score, 2),
        "bias":      "BULLISH" if score > 0.3 else "BEARISH" if score < -0.3 else "NEUTRAL",
        "details":   details[:3],
    }
    get_president_sentiment_score._cache = result
    get_president_sentiment_score._ts    = time.time()
    return result


def get_stt_breakeven_points(symbol: str = "NIFTY", price: float = 22700) -> Dict:
    """
    Calculate exact breakeven in points after April 2026 STT changes.
    
    Use this to set minimum target per trade — if expected profit < breakeven,
    trade is not worth taking regardless of signal score.
    """
    lot_sizes = {
        "NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 65,
        "MIDCPNIFTY": 120, "SENSEX": 20,
    }
    lot = lot_sizes.get(symbol.upper(), 1)
    tv  = price * lot  # trade value

    # April 2026 rates
    brok  = 2 * 20      # ₹20 × 2 orders
    stt   = tv * 0.0002  # 0.02% futures sell side
    exch  = tv * 2 * 0.0000195
    sebi  = tv * 2 * 0.000001
    gst   = (brok + exch) * 0.18
    stamp = tv * 0.00003
    total = brok + stt + exch + sebi + gst + stamp
    pts   = total / lot  # breakeven in index points

    return {
        "symbol":          symbol,
        "lot_size":        lot,
        "total_charges":   round(total, 2),
        "breakeven_pts":   round(pts, 1),
        "min_target_pts":  round(pts * 2, 1),  # need 2:1 R:R minimum
        "note":            f"Post April 2026: need {pts*2:.0f} pts minimum for 2:1 R:R",
    }
