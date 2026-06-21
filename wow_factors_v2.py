"""
wow_factors_v2.py — Extended WOW Factor Engine v2.0

NEW WOW factors (beyond the original 7):

WOW #8:  Unusual Options Activity Detector
         - Tracks sudden OI surge in specific strikes
         - 3x normal volume on calls = informed buying signal
         - Inspired by "Dark Pools" — Scott Patterson

WOW #9:  Promoter Confidence Index
         - Promoter buying = highest conviction signal
         - Pledging increase = financial stress signal
         - Inspired by insider trading research (Seyhun, 1986)

WOW #10: Smart Money vs Dumb Money Divergence
         - When FII buys but retail sells = contrarian BUY
         - When FII sells but retail buys = contrarian SELL
         - Inspired by Sentimentrader's SentimenTrader model

WOW #11: Intermarket Divergence Signal
         - NIFTY up but BANKNIFTY down = weak rally (sell signal)
         - NIFTY down but IT/Pharma up = defensive rotation (cautious)
         - Inspired by John Murphy "Intermarket Analysis"

WOW #12: Earnings Surprise Momentum
         - Stock beat estimates last 3 quarters = bullish bias
         - Consistent misses = bearish bias
         - Inspired by "What Works on Wall Street" — James O'Shaughnessy

WOW #13: F&O Rollover Pressure
         - High cost of carry = bullish (longs rolling forward)
         - Negative cost of carry = bearish (shorts rolling)
         - Basis tracking: futures vs spot premium

WOW #14: Momentum Quality Filter
         - Price momentum + volume confirmation = valid signal
         - Price momentum without volume = suspect signal
         - Inspired by "Momentum" — Gary Antonacci

WOW #15: Regime-Adjusted Volatility Targeting
         - Scale position size to maintain constant portfolio vol
         - Target: 15% annual portfolio volatility
         - Inspired by AQR Capital vol-targeting research
"""
from __future__ import annotations
import logging
import math

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# WOW #8: UNUSUAL OPTIONS ACTIVITY
# ══════════════════════════════════════════════════════════════

def detect_unusual_options_activity(symbol: str = "NIFTY") -> dict:
    """
    Detect unusual options activity — OI surge, call/put skew.
    Informed money leaves traces in options before big moves.
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)

        r = s.get(
            f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}",
            timeout=10
        )
        if r.status_code != 200:
            # Try index
            r = s.get(
                f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
                timeout=10
            )

        if r.status_code != 200:
            return {"signal": "NEUTRAL", "reason": "API unavailable"}

        data = r.json().get("records", {})
        spot = float(data.get("underlyingValue", 0) or 0)
        options = data.get("data", [])

        # ATM ± 5 strikes analysis
        call_oi_surge = []
        put_oi_surge  = []
        total_call_oi = 0
        total_put_oi  = 0

        for opt in options:
            ce = opt.get("CE", {})
            pe = opt.get("PE", {})
            strike = float(opt.get("strikePrice", 0) or 0)

            # Near-money only (within 3%)
            if spot and abs(strike - spot) / spot > 0.03:
                continue

            if ce:
                oi   = float(ce.get("openInterest", 0) or 0)
                chg  = float(ce.get("changeinOpenInterest", 0) or 0)
                vol  = float(ce.get("totalTradedVolume", 0) or 0)
                total_call_oi += oi
                if chg > 0 and oi > 0 and chg / oi > 0.3:  # 30% OI surge
                    call_oi_surge.append({"strike": strike, "oi_chg_pct": chg/oi*100})

            if pe:
                oi   = float(pe.get("openInterest", 0) or 0)
                chg  = float(pe.get("changeinOpenInterest", 0) or 0)
                total_put_oi += oi
                if chg > 0 and oi > 0 and chg / oi > 0.3:
                    put_oi_surge.append({"strike": strike, "oi_chg_pct": chg/oi*100})

        # PCR
        pcr = total_put_oi / total_call_oi if total_call_oi else 1.0

        signal = "NEUTRAL"
        reason = f"PCR={pcr:.2f}"
        score_adj = 0.0

        if call_oi_surge and not put_oi_surge:
            signal = "BULLISH"
            reason = f"Unusual CALL buying at {[s['strike'] for s in call_oi_surge[:2]]}"
            score_adj = 0.8
        elif put_oi_surge and not call_oi_surge:
            signal = "BEARISH"
            reason = f"Unusual PUT buying at {[s['strike'] for s in put_oi_surge[:2]]}"
            score_adj = -0.8
        elif pcr > 1.5:
            signal = "CONTRARIAN_BULLISH"  # too many puts = market likely to rally
            reason = f"PCR {pcr:.2f} — extremely oversold"
            score_adj = 0.5
        elif pcr < 0.7:
            signal = "CONTRARIAN_BEARISH"  # too many calls = crowded long
            reason = f"PCR {pcr:.2f} — overcrowded longs"
            score_adj = -0.5

        return {
            "signal":      signal,
            "reason":      reason,
            "pcr":         round(pcr, 3),
            "call_surges": len(call_oi_surge),
            "put_surges":  len(put_oi_surge),
            "score_adj":   score_adj,
        }
    except Exception as e:
        logger.debug("unusual_options: %s", e)
        return {"signal": "NEUTRAL", "reason": "Unavailable", "score_adj": 0.0}


# ══════════════════════════════════════════════════════════════
# WOW #9: PROMOTER CONFIDENCE INDEX
# ══════════════════════════════════════════════════════════════

def get_promoter_confidence(symbol: str) -> dict:
    """
    Promoter buying = highest conviction bullish signal.
    Promoter pledging increase = financial stress.
    """
    try:
        from omnisource_news_engine import get_omnisource_intelligence
        intel = get_omnisource_intelligence()
        insider = intel.get("insider_trades", [])

        sym_insider = [t for t in insider
                       if t.get("symbol","").upper() == symbol.upper()
                       and "PROM" in t.get("person","").upper()]

        if not sym_insider:
            return {"signal": "NEUTRAL", "score_adj": 0.0}

        buys  = sum(1 for t in sym_insider if t.get("direction") == "BUY")
        sells = sum(1 for t in sym_insider if t.get("direction") == "SELL")

        if buys > sells:
            return {
                "signal":   "BULLISH",
                "reason":   f"Promoter bought {buys}x in last 30 days",
                "score_adj": min(1.5, buys * 0.5),
            }
        elif sells > buys:
            return {
                "signal":   "BEARISH",
                "reason":   f"Promoter sold {sells}x in last 30 days",
                "score_adj": max(-1.5, -sells * 0.5),
            }
    except Exception as e:
        logger.debug("promoter_confidence: %s", e)
    return {"signal": "NEUTRAL", "score_adj": 0.0}


# ══════════════════════════════════════════════════════════════
# WOW #10: SMART MONEY vs DUMB MONEY DIVERGENCE
# ══════════════════════════════════════════════════════════════

def smart_dumb_money_divergence() -> dict:
    """
    FII = smart money. Retail DII = dumb money (short term).
    Divergence = high-value signal.
    Inspired by Sentimentrader.com methodology.
    """
    try:
        from fii_data_fetcher import get_fii_history
        hist = get_fii_history(5)
        if hist is None or len(hist) < 3:
            return {"signal": "NEUTRAL", "score_adj": 0.0}

        fii_net = hist["fii_net"].sum() if "fii_net" in hist.columns else 0
        dii_net = hist["dii_net"].sum() if "dii_net" in hist.columns else 0

        # Divergence patterns
        if fii_net > 1000 and dii_net < -500:
            return {
                "signal":   "STRONG_BULLISH",
                "reason":   f"FII buying ₹{fii_net:,.0f}Cr while DII sells — smart money in",
                "score_adj": 1.2,
            }
        elif fii_net < -1000 and dii_net > 500:
            return {
                "signal":   "STRONG_BEARISH",
                "reason":   f"FII selling ₹{abs(fii_net):,.0f}Cr while DII buys — smart money out",
                "score_adj": -1.2,
            }
        elif fii_net > 500 and dii_net > 500:
            return {
                "signal":   "BULLISH",
                "reason":   "Both FII + DII buying — strong consensus",
                "score_adj": 0.8,
            }
        elif fii_net < -500 and dii_net < -500:
            return {
                "signal":   "BEARISH",
                "reason":   "Both FII + DII selling — strong selling pressure",
                "score_adj": -0.8,
            }
    except Exception as e:
        logger.debug("smart_dumb: %s", e)
    return {"signal": "NEUTRAL", "score_adj": 0.0}


# ══════════════════════════════════════════════════════════════
# WOW #11: INTERMARKET DIVERGENCE
# ══════════════════════════════════════════════════════════════

def intermarket_divergence() -> dict:
    """
    NIFTY vs BANKNIFTY vs IT vs PHARMA divergence.
    Divergence = warning signal. Confirmation = conviction signal.
    John Murphy "Intermarket Analysis" framework.
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)

        indices = {}
        for idx in r.json().get("data", []):
            name = str(idx.get("index","")).upper()
            chg  = float(idx.get("percentChange", 0) or 0)
            for key in ["NIFTY 50","NIFTY BANK","NIFTY IT","NIFTY PHARMA","NIFTY METAL"]:
                if key in name:
                    indices[key] = chg

        if not indices:
            return {"signal": "NEUTRAL", "score_adj": 0.0}

        nifty = indices.get("NIFTY 50", 0)
        bank  = indices.get("NIFTY BANK", 0)
        it    = indices.get("NIFTY IT", 0)
        pharma= indices.get("NIFTY PHARMA", 0)

        signals = []
        score_adj = 0.0

        # NIFTY up but BANKNIFTY down = weak rally
        if nifty > 0.3 and bank < -0.2:
            signals.append("NIFTY rising but BANK falling — WEAK rally")
            score_adj -= 0.5

        # Both up = confirmed rally
        elif nifty > 0.3 and bank > 0.3:
            signals.append("NIFTY + BANK both rising — STRONG rally")
            score_adj += 0.6

        # Defensive rotation = cautious
        if pharma > 1.0 and nifty < 0:
            signals.append("Defensive rotation to Pharma — risk-off")
            score_adj -= 0.4

        # IT + NIFTY up = tech-led bull = bullish
        if it > 0.5 and nifty > 0:
            signals.append("IT leading NIFTY — tech bull, positive")
            score_adj += 0.3

        overall = "BULLISH" if score_adj > 0.3 else "BEARISH" if score_adj < -0.3 else "NEUTRAL"
        return {
            "signal":   overall,
            "reason":   "; ".join(signals) if signals else "No divergence",
            "indices":  indices,
            "score_adj": round(score_adj, 2),
        }
    except Exception as e:
        logger.debug("intermarket: %s", e)
    return {"signal": "NEUTRAL", "score_adj": 0.0}


# ══════════════════════════════════════════════════════════════
# WOW #14: MOMENTUM QUALITY FILTER
# ══════════════════════════════════════════════════════════════

def momentum_quality_filter(df_ohlcv, direction: str) -> dict:
    """
    Validate momentum with volume confirmation.
    Price momentum without volume = weak signal.
    Price + volume momentum = strong signal.
    Gary Antonacci "Momentum" + O'Neil CANSLIM Volume rule.
    """
    try:
        if df_ohlcv is None or len(df_ohlcv) < 20:
            return {"valid": True, "quality": "UNKNOWN", "score_adj": 0.0}

        df = df_ohlcv.copy()
        df.columns = [c.lower() for c in df.columns]

        close  = df["close"].values
        volume = df["volume"].values if "volume" in df.columns else None

        # Price momentum (5-day)
        if len(close) >= 5:
            price_mom = (close[-1] - close[-5]) / close[-5] * 100
        else:
            price_mom = 0

        quality = "AVERAGE"
        score_adj = 0.0

        if volume is not None and len(volume) >= 20:
            avg_vol = sum(volume[-20:-1]) / 19
            today_vol = volume[-1]
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1.0

            # High volume confirms momentum
            if direction == "BUY" and price_mom > 0 and vol_ratio > 1.5:
                quality = "HIGH"
                score_adj = 0.5
                reason = f"Price +{price_mom:.1f}% on {vol_ratio:.1f}x avg volume"
            elif direction == "BUY" and price_mom > 0 and vol_ratio < 0.8:
                quality = "LOW"
                score_adj = -0.3
                reason = f"Price +{price_mom:.1f}% but LOW volume ({vol_ratio:.1f}x)"
            elif direction == "SELL" and price_mom < 0 and vol_ratio > 1.5:
                quality = "HIGH"
                score_adj = 0.5
                reason = f"Price {price_mom:.1f}% on {vol_ratio:.1f}x avg volume"
            else:
                reason = f"Volume ratio: {vol_ratio:.1f}x"
        else:
            reason = "No volume data"

        return {
            "valid":     True,
            "quality":   quality,
            "reason":    reason,
            "score_adj": score_adj,
            "price_mom": round(price_mom, 2),
        }
    except Exception as e:
        logger.debug("momentum_quality: %s", e)
    return {"valid": True, "quality": "UNKNOWN", "score_adj": 0.0}


# ══════════════════════════════════════════════════════════════
# WOW #15: VOLATILITY-TARGETED POSITION SIZING
# ══════════════════════════════════════════════════════════════

def volatility_targeted_size(
        symbol: str,
        base_capital: float,
        df_ohlcv,
        target_vol: float = 0.15) -> dict:
    """
    Scale position size to maintain constant portfolio volatility.
    Target: 15% annual volatility (institutional standard).
    AQR Capital vol-targeting methodology.

    Formula: position_size = (target_vol / realized_vol) × base_capital
    """
    try:
        if df_ohlcv is None or len(df_ohlcv) < 20:
            return {"size": base_capital * 0.05, "vol_scalar": 1.0}

        df = df_ohlcv.copy()
        df.columns = [c.lower() for c in df.columns]
        closes = df["close"].values

        # Daily returns
        rets = [(closes[i] - closes[i-1]) / closes[i-1]
                for i in range(1, len(closes))]

        # 20-day realized volatility (annualised)
        recent = rets[-20:]
        mean_r = sum(recent) / len(recent)
        variance = sum((r - mean_r)**2 for r in recent) / max(len(recent)-1, 1)
        realized_vol = math.sqrt(variance) * math.sqrt(252)

        if realized_vol <= 0:
            return {"size": base_capital * 0.05, "vol_scalar": 1.0}

        # Vol scalar
        vol_scalar = target_vol / realized_vol
        vol_scalar = max(0.3, min(3.0, vol_scalar))  # clamp 0.3x-3x

        # Position size (max 10% of capital per trade)
        pos_size = base_capital * 0.10 * vol_scalar
        pos_size = min(pos_size, base_capital * 0.15)  # hard cap 15%
        pos_size = max(pos_size, base_capital * 0.02)  # min 2%

        return {
            "size":         round(pos_size, 2),
            "vol_scalar":   round(vol_scalar, 3),
            "realized_vol": round(realized_vol * 100, 1),
            "target_vol":   round(target_vol * 100, 1),
            "note":         (f"High vol ({realized_vol*100:.0f}%) — reduced size"
                             if vol_scalar < 0.8 else
                             f"Low vol ({realized_vol*100:.0f}%) — increased size"
                             if vol_scalar > 1.5 else
                             f"Normal vol ({realized_vol*100:.0f}%)")
        }
    except Exception as e:
        logger.debug("vol_targeting: %s", e)
    return {"size": base_capital * 0.05, "vol_scalar": 1.0}


# ══════════════════════════════════════════════════════════════
# MASTER WOW SCORER
# ══════════════════════════════════════════════════════════════

def get_all_wow_scores(symbol: str, direction: str,
                       df_ohlcv=None, capital: float = 26964) -> dict:
    """
    Run all WOW factors and return aggregated score adjustment.
    Positive = adds to signal score. Negative = reduces.
    """
    results = {}
    total_adj = 0.0

    # WOW #8: Unusual options
    uoa = detect_unusual_options_activity(symbol if symbol in
          {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"} else "NIFTY")
    results["unusual_options"] = uoa
    total_adj += uoa.get("score_adj", 0) * 0.5

    # WOW #9: Promoter confidence
    pc = get_promoter_confidence(symbol)
    results["promoter"] = pc
    total_adj += pc.get("score_adj", 0)

    # WOW #10: Smart/dumb money
    sd = smart_dumb_money_divergence()
    results["smart_dumb_money"] = sd
    total_adj += sd.get("score_adj", 0) * 0.6

    # WOW #11: Intermarket
    im = intermarket_divergence()
    results["intermarket"] = im
    total_adj += im.get("score_adj", 0) * 0.7

    # WOW #14: Momentum quality
    if df_ohlcv is not None:
        mq = momentum_quality_filter(df_ohlcv, direction)
        results["momentum_quality"] = mq
        total_adj += mq.get("score_adj", 0)

    # WOW #15: Vol targeting
    if df_ohlcv is not None:
        vt = volatility_targeted_size(symbol, capital, df_ohlcv)
        results["vol_targeting"] = vt
        # No score adj — this affects size only

    results["total_wow_adj"] = round(max(-3.0, min(3.0, total_adj)), 2)
    results["symbol"] = symbol
    results["direction"] = direction

    return results


def format_wow_telegram(symbol: str = "NIFTY") -> str:
    """WOW factor report for /wow Telegram command."""
    from datetime import datetime as _dt
    now = _dt.now().strftime("%d-%b %H:%M")
    wow = get_all_wow_scores(symbol, "BUY")
    adj = wow.get("total_wow_adj", 0)
    icon = "🟢" if adj > 0.3 else "🔴" if adj < -0.3 else "⚪"

    lines = [
        f"⚡ <b>WOW FACTORS v2</b> | {symbol} | {now}",
        f"  {icon} Total adjustment: <b>{adj:+.2f}</b>",
        "",
        f"  <b>WOW #8  Unusual Options</b>",
        f"  {wow.get('unusual_options',{}).get('signal','?')} — "
        f"{wow.get('unusual_options',{}).get('reason','')[:50]}",
        f"  PCR: {wow.get('unusual_options',{}).get('pcr',0):.2f}",
        "",
        f"  <b>WOW #9  Promoter Confidence</b>",
        f"  {wow.get('promoter',{}).get('signal','NEUTRAL')} — "
        f"{wow.get('promoter',{}).get('reason','No recent activity')[:50]}",
        "",
        f"  <b>WOW #10 Smart/Dumb Money</b>",
        f"  {wow.get('smart_dumb_money',{}).get('signal','?')} — "
        f"{wow.get('smart_dumb_money',{}).get('reason','')[:50]}",
        "",
        f"  <b>WOW #11 Intermarket</b>",
        f"  {wow.get('intermarket',{}).get('signal','?')} — "
        f"{wow.get('intermarket',{}).get('reason','')[:50]}",
        "",
        f"  <b>WOW #14 Momentum Quality</b>",
        f"  {wow.get('momentum_quality',{}).get('quality','?')} — "
        f"{wow.get('momentum_quality',{}).get('reason','Run with symbol data')[:50]}",
        "",
        f"  Total score boost: {adj:+.2f} applied to all signals",
        f"  ⏰ Refreshes per scan cycle",
    ]
    return "\n".join(lines)
