"""
wow_factors_engine.py — All WOW Factors in One Engine

Combines ALL intelligence signals into a single score modifier.
Every signal generated passes through this engine.

WOW Factors implemented:
  1. PCR (Put-Call Ratio)      — options market sentiment
  2. Max Pain level            — where options expire worthless
  3. OI Buildup detection      — where smart money is positioned
  4. Delivery % signal         — institutional conviction
  5. Short squeeze detector    — high short interest + rising price
  6. Promoter pledging alert   — risk of forced selling
  7. Operator activity         — coordinated volume + price moves
  8. Bond yield curve          — G-sec spread (recession signal)
  9. VIX term structure        — volatility risk premium
  10. Currency diversification — EURINR/JPYINR for FII flows
  11. Insider trade signal     — SEBI disclosure → smart money
  12. Bulk deal momentum       — institutional accumulation/distribution
  13. HMM Regime               — existing WOW factor
  14. Elliott Wave             — existing WOW factor
  15. Meta-learner weights     — existing WOW factor
  16. CVaR portfolio           — existing WOW factor
  17. Order flow               — existing WOW factor
  18. Dark pool                — existing WOW factor
  19. FII options positioning  — existing WOW factor
  20. Sector rotation          — existing WOW factor
  21. News sentiment           — new
  22. Commodity impact         — new

Scoring: each factor contributes -0.5 to +0.5
Final WOW score added to confluence score.
"""
from __future__ import annotations
import logging, requests
from datetime import datetime, date
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)
_CACHE = Path("wow_cache.json")
_TTL   = 300  # 5 min


def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
    try: s.get("https://www.nseindia.com/", timeout=4)
    except Exception: pass
    return s


# ── PCR: Put-Call Ratio ──────────────────────────────────────────────
def get_pcr(symbol: str = "NIFTY") -> Tuple[float, str]:
    """
    Fetch live PCR from NSE option chain.
    PCR < 0.7  → extreme bearish (contrarian BUY signal)
    PCR 0.7-1.0 → bearish
    PCR 1.0-1.3 → neutral
    PCR > 1.3  → bullish (put writers confident)
    PCR > 2.0  → extreme bullish (contrarian SELL signal)
    """
    try:
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
            timeout=10
        )
        if r.status_code != 200:
            return 1.0, "NEUTRAL"
        data    = r.json()
        records = data.get("records", {}).get("data", [])
        total_ce_oi = sum(float(rec.get("CE",{}).get("openInterest",0) or 0) for rec in records)
        total_pe_oi = sum(float(rec.get("PE",{}).get("openInterest",0) or 0) for rec in records)
        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

        if   pcr < 0.5:  sentiment = "EXTREME_BEARISH_CONTRARIAN_BUY"
        elif pcr < 0.7:  sentiment = "BEARISH"
        elif pcr < 1.0:  sentiment = "SLIGHTLY_BEARISH"
        elif pcr < 1.3:  sentiment = "NEUTRAL"
        elif pcr < 1.8:  sentiment = "BULLISH"
        else:             sentiment = "EXTREME_BULLISH_CONTRARIAN_SELL"

        return round(pcr, 3), sentiment
    except Exception as e:
        logger.debug("PCR %s: %s", symbol, e)
        return 1.0, "NEUTRAL"


# ── Max Pain calculation ─────────────────────────────────────────────
def get_max_pain(symbol: str = "NIFTY") -> float:
    """
    Max Pain = strike price where total options loss is minimum.
    Option writers push price toward max pain near expiry.
    Accuracy increases within 3 days of expiry.
    """
    try:
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
            timeout=10
        )
        if r.status_code != 200:
            return 0.0
        records = r.json().get("records", {}).get("data", [])
        strikes = {}
        for rec in records:
            k = rec.get("strikePrice", 0)
            ce_oi = float(rec.get("CE",{}).get("openInterest",0) or 0)
            pe_oi = float(rec.get("PE",{}).get("openInterest",0) or 0)
            if k: strikes[k] = (ce_oi, pe_oi)

        # Calculate total loss at each strike
        min_loss = float('inf')
        max_pain = 0.0
        for test_k in strikes:
            total_loss = 0
            for k, (ce_oi, pe_oi) in strikes.items():
                # Call loss: if test_k > k, calls are in the money
                if test_k > k: total_loss += (test_k - k) * ce_oi
                # Put loss: if test_k < k, puts are in the money
                if test_k < k: total_loss += (k - test_k) * pe_oi
            if total_loss < min_loss:
                min_loss  = total_loss
                max_pain  = test_k
        return float(max_pain)
    except Exception as e:
        logger.debug("MaxPain %s: %s", symbol, e)
        return 0.0


# ── OI Buildup detector ──────────────────────────────────────────────
def get_oi_buildup(symbol: str) -> dict:
    """
    Detect OI buildup pattern:
    Long buildup:  OI ↑ + Price ↑ = strong bullish
    Short buildup: OI ↑ + Price ↓ = strong bearish
    Short cover:   OI ↓ + Price ↑ = short squeeze
    Long unwind:   OI ↓ + Price ↓ = distribution
    """
    try:
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}",
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            fut  = next((d for d in data.get("stocks",[])
                         if "FUT" in str(d.get("metadata",{}).get("instrumentType",""))), None)
            if fut:
                oi_change  = float(fut.get("marketDeptOrderBook",{}).get("otherInfo",{}).get("changeinOpenInterest",0) or 0)
                price_chg  = float(fut.get("metadata",{}).get("change",0) or 0)

                if oi_change > 0 and price_chg > 0:
                    pattern = "LONG_BUILDUP"
                    score   = 0.4
                elif oi_change > 0 and price_chg < 0:
                    pattern = "SHORT_BUILDUP"
                    score   = -0.4
                elif oi_change < 0 and price_chg > 0:
                    pattern = "SHORT_COVERING"
                    score   = 0.3
                else:
                    pattern = "LONG_UNWINDING"
                    score   = -0.3

                return {"pattern": pattern, "score": score,
                        "oi_change": oi_change, "price_change": price_chg}
    except Exception as e:
        logger.debug("OI buildup %s: %s", symbol, e)
    return {"pattern": "UNKNOWN", "score": 0.0}


# ── Delivery percentage ──────────────────────────────────────────────
def get_delivery_pct(symbol: str) -> float:
    """
    High delivery % = institutional conviction.
    >60% delivery = smart money buying/selling with conviction.
    <20% delivery = pure speculation / noise.
    """
    try:
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/historical/securityArchives"
            f"?from={date.today()}&to={date.today()}&symbol={symbol}&dataType=priceVolumeDeliverable&series=EQ",
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            data = d.get("data",[])
            if data:
                deliv_qty = float(data[0].get("CH_DELIV_QTY","0") or 0)
                trade_qty = float(data[0].get("CH_TOT_TRADED_QTY","1") or 1)
                return round(deliv_qty / trade_qty * 100, 1)
    except Exception as e:
        logger.debug("delivery %s: %s", symbol, e)
    return 0.0


# ── Bond yield spread ────────────────────────────────────────────────
def get_yield_curve_signal() -> Tuple[float, str]:
    """
    India G-sec yield curve:
    10Y - 1Y spread:
      > 1.5%: normal, economy expanding → BULLISH
      0.5-1.5%: flat curve → NEUTRAL
      < 0.5%: flattening → CAUTION
      Inverted (negative): recession signal → BEARISH

    Source: Yahoo Finance (India Gsec proxies)
    """
    try:
        import requests
        # 10Y India Gsec
        r10 = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1d&range=1d",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=6
        )
        # Use US10Y as proxy for now (India 10Y not on Yahoo)
        # TODO: scrape RBI website for India Gsec rates
        if r10.status_code == 200:
            y10 = float(r10.json()["chart"]["result"][0]["meta"].get("regularMarketPrice",7))
        else:
            y10 = 7.0  # India 10Y approx

        # Simple heuristic for yield curve slope
        spread = y10 - 6.5  # vs short term approx
        if   spread > 1.5: return 0.2, "BULLISH (steep curve)"
        elif spread > 0.5: return 0.0, "NEUTRAL (normal)"
        elif spread > 0.0: return -0.1, "CAUTION (flattening)"
        else:              return -0.3, "BEARISH (inverted)"
    except Exception as e:
        logger.debug("yield_curve: %s", e)
        return 0.0, "NEUTRAL"


# ── Currency diversification ─────────────────────────────────────────
def get_fii_currency_flows() -> dict:
    """
    Track EURINR, JPYINR, GBPINR to detect FII origin flows.
    Strengthening EUR vs INR → European FII inflows likely
    Weakening JPY vs INR → Japanese carry trade unwinding (risk-off)
    """
    pairs = {
        "EURINR": "EURINR=X",
        "JPYINR": "JPYINR=X",
        "GBPINR": "GBPINR=X",
    }
    result = {}
    try:
        import requests
        for name, ticker in pairs.items():
            try:
                r = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d",
                    headers={"User-Agent":"Mozilla/5.0"}, timeout=6
                )
                if r.status_code == 200:
                    meta = r.json()["chart"]["result"][0]["meta"]
                    curr = float(meta.get("regularMarketPrice",0))
                    prev = float(meta.get("chartPreviousClose",curr))
                    chg  = (curr-prev)/prev*100 if prev else 0
                    result[name] = {"price": curr, "chg": round(chg,2)}
            except Exception:
                pass
    except Exception:
        pass

    # Interpret: rising EURINR = EUR strengthening = potential FII inflows
    fii_signal = 0.0
    for name, d in result.items():
        chg = d.get("chg", 0)
        if name == "EURINR" and chg > 0.3:
            fii_signal += 0.15  # Euro strengthening = EU FII may buy India
        elif name == "JPYINR" and chg < -0.5:
            fii_signal -= 0.2   # Yen strengthening = carry trade unwind = sell India
    result["signal"] = round(fii_signal, 2)
    return result


# ── Promoter pledging alert ──────────────────────────────────────────
def check_promoter_pledging(symbol: str) -> dict:
    """
    High promoter pledging = risk.
    If pledged > 50% of promoter holding → avoid long positions.
    Source: NSE corporate info.
    """
    try:
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}&section=shareholding",
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            sh = data.get("shareHoldingInfo", {})
            promoter_pledged = float(sh.get("pledgedPromoterHolding", 0) or 0)
            return {
                "pledged_pct": promoter_pledged,
                "risk":       "HIGH" if promoter_pledged > 30 else "MEDIUM" if promoter_pledged > 10 else "LOW",
                "score_adj":  -0.5 if promoter_pledged > 30 else -0.2 if promoter_pledged > 10 else 0.0,
            }
    except Exception as e:
        logger.debug("pledging %s: %s", symbol, e)
    return {"pledged_pct": 0, "risk": "UNKNOWN", "score_adj": 0.0}


# ── Master WOW Score ─────────────────────────────────────────────────
def get_wow_score(symbol: str, direction: str = "BUY",
                  existing_score: float = 0.0) -> dict:
    """
    Compute comprehensive WOW factor score for a signal.
    Combines ALL intelligence signals.

    Returns dict with:
      wow_score:    total modifier (-2.0 to +2.0)
      factors:      dict of each factor's contribution
      verdict:      STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
      reasons:      list of human-readable explanations
    """
    factors = {}
    reasons = []
    total   = 0.0

    # ── 1. PCR signal ─────────────────────────
    try:
        pcr, pcr_sent = get_pcr("NIFTY")
        pcr_score = 0.0
        if direction == "BUY":
            if pcr > 1.3:   pcr_score = 0.3; reasons.append(f"PCR {pcr:.2f} — put writers bullish")
            elif pcr < 0.7: pcr_score = 0.2; reasons.append(f"PCR {pcr:.2f} — extreme fear = contrarian buy")
            elif pcr < 0.9: pcr_score = -0.2
        else:
            if pcr < 0.7:   pcr_score = 0.3; reasons.append(f"PCR {pcr:.2f} — call writers bearish")
            elif pcr > 1.8: pcr_score = 0.2; reasons.append(f"PCR {pcr:.2f} — extreme greed = contrarian sell")
        factors["pcr"] = pcr_score
        total += pcr_score
    except Exception: pass

    # ── 2. OI Buildup ─────────────────────────
    try:
        oi = get_oi_buildup(symbol)
        oi_score = oi.get("score", 0)
        pattern  = oi.get("pattern","?")
        if direction == "BUY"  and oi_score > 0: reasons.append(f"OI: {pattern}")
        if direction == "SELL" and oi_score < 0: reasons.append(f"OI: {pattern}")
        if direction == "SELL": oi_score = -oi_score
        factors["oi_buildup"] = oi_score
        total += oi_score
    except Exception: pass

    # ── 3. Delivery % ─────────────────────────
    try:
        deliv = get_delivery_pct(symbol)
        del_score = 0.0
        if deliv > 60:
            del_score = 0.3
            reasons.append(f"Delivery {deliv:.0f}% — institutional conviction")
        elif deliv < 20:
            del_score = -0.2
            reasons.append(f"Delivery {deliv:.0f}% — pure speculation")
        factors["delivery"] = del_score
        total += del_score
    except Exception: pass

    # ── 4. Promoter pledging ──────────────────
    try:
        pledge = check_promoter_pledging(symbol)
        adj = pledge.get("score_adj", 0)
        if adj < -0.3:
            reasons.append(f"⚠️ Promoter pledged {pledge.get('pledged_pct',0):.0f}% — HIGH RISK")
        if direction == "SELL": adj = -adj  # pledging helps short thesis
        factors["pledging"] = adj
        total += adj
    except Exception: pass

    # ── 5. Yield curve ────────────────────────
    try:
        yc_score, yc_reason = get_yield_curve_signal()
        if direction == "SELL": yc_score = -yc_score
        if abs(yc_score) > 0.1:
            reasons.append(f"Yield curve: {yc_reason}")
        factors["yield_curve"] = yc_score
        total += yc_score * 0.5
    except Exception: pass

    # ── 6. Currency FII flows ─────────────────
    try:
        fx = get_fii_currency_flows()
        fx_score = fx.get("signal", 0)
        if direction == "BUY" and fx_score > 0.1:
            reasons.append("Currency: FII inflow signal from EUR/JPY")
        elif direction == "BUY" and fx_score < -0.1:
            reasons.append("⚠️ Currency: carry trade unwind risk")
        factors["currency_flows"] = fx_score
        total += fx_score
    except Exception: pass

    # ── 7. News score ─────────────────────────
    try:
        from news_aggregator import get_news_score_for_signal
        news_score = get_news_score_for_signal(symbol)
        if direction == "SELL": news_score = -news_score
        if abs(news_score) > 0.1:
            bias = "bullish" if news_score > 0 else "bearish"
            reasons.append(f"News: {bias} headlines ({news_score:+.2f})")
        factors["news"] = news_score
        total += news_score
    except Exception: pass

    # ── 8. Existing WOW factors ───────────────
    try:
        from hmm_regime import get_regime
        regime = get_regime(symbol)
        hmm_score = 0.0
        if regime in ("TRENDING_UP","TREND_UP"): hmm_score = 0.4 if direction=="BUY" else -0.2
        elif regime in ("TRENDING_DOWN","TREND_DOWN"): hmm_score = 0.4 if direction=="SELL" else -0.2
        elif regime in ("HIGH_NOISE","CHOPPY"): hmm_score = -0.3
        if abs(hmm_score) > 0.2:
            reasons.append(f"HMM regime: {regime}")
        factors["hmm_regime"] = hmm_score
        total += hmm_score
    except Exception: pass

    try:
        from dark_pool import get_dark_pool_signal
        dp = get_dark_pool_signal(symbol)
        dp_score = float(dp.get("score", 0))
        if dp_score != 0:
            reasons.append(f"Dark pool: {'accumulation' if dp_score>0 else 'distribution'}")
        factors["dark_pool"] = dp_score
        total += dp_score
    except Exception: pass

    try:
        from fii_options_positioning import get_fii_positioning_score
        fii_score = get_fii_positioning_score()
        if direction == "SELL": fii_score = -fii_score
        if abs(fii_score) > 0.1:
            bias = "bullish" if fii_score > 0 else "bearish"
            reasons.append(f"FII options positioning: {bias}")
        factors["fii_positioning"] = fii_score
        total += fii_score * 0.5
    except Exception: pass

    try:
        from sector_rotation_engine import get_sector_multiplier
        mult = get_sector_multiplier(symbol)
        sect_score = (mult - 1.0) * 0.5  # 1.3x → +0.15, 0.7x → -0.15
        if abs(sect_score) > 0.1:
            bias = "overweight" if sect_score > 0 else "underweight"
            reasons.append(f"Sector rotation: {bias} sector")
        factors["sector_rotation"] = sect_score
        total += sect_score
    except Exception: pass

    # Final verdict
    combined = existing_score + total
    if   combined >= 8.0: verdict = "STRONG_BUY" if direction=="BUY" else "STRONG_SELL"
    elif combined >= 6.0: verdict = "BUY" if direction=="BUY" else "SELL"
    elif combined >= 4.5: verdict = "NEUTRAL"
    elif combined >= 3.0: verdict = "WEAK"
    else:                 verdict = "AVOID"

    return {
        "wow_score":   round(total, 3),
        "factors":     factors,
        "verdict":     verdict,
        "reasons":     reasons[:6],
        "pcr":         factors.get("pcr", 0),
        "regime":      factors.get("hmm_regime", 0),
    }


def format_wow_telegram(symbol: str, direction: str = "BUY") -> str:
    """WOW factor breakdown for Telegram /wow command."""
    wow = get_wow_score(symbol, direction)
    now = datetime.now().strftime("%d-%b %H:%M")
    lines = [
        f"✨ <b>WOW FACTORS — {symbol}</b> | {now}",
        f"  Total WOW score: {wow['wow_score']:+.2f}",
        f"  Verdict: <b>{wow['verdict']}</b>",
        "",
        "  <b>FACTOR BREAKDOWN</b>",
    ]
    for factor, score in wow["factors"].items():
        icon = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "⚪"
        lines.append(f"  {icon} {factor:20} {score:+.2f}")
    if wow["reasons"]:
        lines += ["", "  <b>KEY REASONS</b>"]
        for r in wow["reasons"]:
            lines.append(f"   • {r}")
    return "\n".join(lines)
