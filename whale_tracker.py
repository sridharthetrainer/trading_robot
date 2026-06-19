"""
whale_tracker.py — Institutional/Whale/Insider Activity Tracker

DATA SOURCES (all free / official):
  NSE:  nseindia.com/api/block-deal
  NSE:  nseindia.com/api/corporate-pledgeData (promoter pledge)
  NSE:  participant_oi.py (FII/DII futures OI)
  NSE:  option chain put/call skew
  SEBI: sebi.gov.in (insider trading disclosures)
  
SIGNALS:
  1. FII Futures OI change — net long/short flip
  2. Unusual OTM options volume (big calls = bullish, big puts = bearish)
  3. Put/Call skew anomaly (PCR < 0.7 = bearish hedge)
  4. Block deal direction (large institution buying/selling)
  5. Promoter pledge change (pledge increase = stress = bearish)
  6. Option unwinding (large OI reduction in puts = bullish)
  7. Max pain deviation (price >> max pain = pullback expected)
  
BOOKS REFERENCE:
  "The Options Playbook" — options flow analysis
  "Market Wizards" — follow smart money
  "Dark Pools" (Patterson) — institutional hidden orders
  FII COT equivalent for Indian markets
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)
_CACHE = Path("whale_cache.json")
_TTL   = 3600  # 1 hour


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com/", timeout=6)
    except Exception:
        pass
    return s


def get_block_deals(days_back: int = 5) -> dict:
    """
    Fetch recent block/bulk deals from NSE.
    Large block purchases by institutions → bullish for that stock.
    """
    try:
        s   = _session()
        url = "https://www.nseindia.com/api/block-deal"
        r   = s.get(url, timeout=10)
        data = r.json()
        deals = data.get("data", [])

        # Summarize by symbol
        summary = {}
        for d in deals:
            sym  = str(d.get("symbol","")).upper()
            qty  = int(d.get("quantity",0) or 0)
            val  = float(d.get("value",0) or 0)
            typ  = str(d.get("buysell","")).upper()
            if not sym:
                continue
            if sym not in summary:
                summary[sym] = {"buy_val":0,"sell_val":0,"net_val":0,"deals":0}
            summary[sym]["deals"] += 1
            if "BUY" in typ or "B" == typ:
                summary[sym]["buy_val"]  += val
                summary[sym]["net_val"]  += val
            else:
                summary[sym]["sell_val"] += val
                summary[sym]["net_val"]  -= val

        # Signal: net buyer / net seller
        signals = {}
        for sym, s_data in summary.items():
            net = s_data["net_val"]
            signals[sym] = {
                "signal":   "BULLISH" if net > 1e6 else "BEARISH" if net < -1e6 else "NEUTRAL",
                "net_val":  round(net/1e7, 2),  # in Cr
                "deals":    s_data["deals"],
                "score_mod": 1.5 if net > 1e6 else (-1.0 if net < -1e6 else 0.0),
            }
        return {"signals": signals, "count": len(deals), "ts": datetime.now().isoformat()}
    except Exception as e:
        logger.debug("block_deals: %s", e)
        return {}


def get_unusual_options_volume(symbol: str = "NIFTY") -> dict:
    """
    Detect unusual options volume vs open interest.
    High volume on OTM calls/puts = institutional positioning.
    
    Returns:
      signal: BULLISH / BEARISH / NEUTRAL
      dominant_side: CE / PE
      otm_call_ratio: unusual call volume / avg
      skew: put IV - call IV (positive = fear/hedging)
    """
    try:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        s   = _session()
        r   = s.get(url, timeout=12)
        data = r.json()
        records = data.get("records",{}).get("data",[])

        spot = float(data.get("records",{}).get("underlyingValue",0) or 0)
        if spot <= 0 or not records:
            return {}

        # Separate strikes: near ATM (±2%), OTM (2-8%), far OTM (>8%)
        atm_ce_oi = atm_pe_oi = 0.0
        otm_ce_vol = otm_pe_vol = 0.0
        atm_ce_vol = atm_pe_vol = 0.0
        total_ce_oi = total_pe_oi = 0.0
        ce_ivs = []; pe_ivs = []

        for rec in records:
            strike = float(rec.get("strikePrice",0) or 0)
            if not strike:
                continue
            dist_pct = abs(strike - spot) / spot

            ce = rec.get("CE",{}) or {}
            pe = rec.get("PE",{}) or {}

            ce_oi  = float(ce.get("openInterest",0) or 0)
            pe_oi  = float(pe.get("openInterest",0) or 0)
            ce_vol = float(ce.get("totalTradedVolume",0) or 0)
            pe_vol = float(pe.get("totalTradedVolume",0) or 0)
            ce_iv  = float(ce.get("impliedVolatility",0) or 0)
            pe_iv  = float(pe.get("impliedVolatility",0) or 0)

            total_ce_oi += ce_oi
            total_pe_oi += pe_oi

            if dist_pct <= 0.02:  # ATM ±2%
                atm_ce_oi  += ce_oi;  atm_pe_oi  += pe_oi
                atm_ce_vol += ce_vol; atm_pe_vol += pe_vol

            if 0.02 < dist_pct <= 0.08:  # OTM 2-8%
                if strike > spot:
                    otm_ce_vol += ce_vol
                else:
                    otm_pe_vol += pe_vol

            if ce_iv > 0: ce_ivs.append(ce_iv)
            if pe_iv > 0: pe_ivs.append(pe_iv)

        # PCR
        pcr = total_pe_oi / max(total_ce_oi, 1)

        # IV skew: put IV - call IV (positive = fear)
        iv_skew = float(np.mean(pe_ivs) - np.mean(ce_ivs)) if ce_ivs and pe_ivs else 0.0

        # Unusual OTM volume
        atm_ce_vol = max(atm_ce_vol, 1)
        atm_pe_vol = max(atm_pe_vol, 1)
        otm_call_ratio = otm_ce_vol / atm_ce_vol
        otm_put_ratio  = otm_pe_vol / atm_pe_vol

        # Signal
        signal = "NEUTRAL"
        if pcr > 1.4 and otm_put_ratio > 1.5:
            signal = "BEARISH"   # heavy put hedging = fear
        elif pcr < 0.7 and otm_call_ratio > 1.5:
            signal = "BULLISH"   # heavy OTM calls = institutional bullish
        elif pcr > 1.3:
            signal = "BULLISH"   # contrarian: too many puts = smart money fade
        elif pcr < 0.7:
            signal = "BEARISH"   # contrarian: too many calls = complacency

        return {
            "symbol":     symbol,
            "pcr":        round(pcr, 3),
            "iv_skew":    round(iv_skew, 2),
            "otm_call_ratio": round(otm_call_ratio, 2),
            "otm_put_ratio":  round(otm_put_ratio, 2),
            "signal":     signal,
            "score_mod":  1.5 if signal == "BULLISH" else (-1.5 if signal == "BEARISH" else 0.0),
            "ts": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug("unusual_options_volume %s: %s", symbol, e)
        return {}


def get_promoter_pledge(symbol: str) -> dict:
    """
    Fetch promoter pledge data from NSE.
    Rising pledge = promoter financial stress = BEARISH.
    Pledge release = confidence restored = BULLISH.
    """
    try:
        s   = _session()
        url = f"https://www.nseindia.com/api/corporate-pledgeData?symbol={symbol}"
        r   = s.get(url, timeout=10)
        data = r.json()
        records = data.get("data",[])

        if len(records) < 2:
            return {"symbol": symbol, "signal": "NEUTRAL", "score_mod": 0.0}

        latest = records[0]
        prev   = records[1]

        latest_pct = float(latest.get("pledgedSharesPct", 0) or 0)
        prev_pct   = float(prev.get("pledgedSharesPct",   0) or 0)
        change     = latest_pct - prev_pct

        if change > 5:
            signal = "BEARISH"; mod = -2.0
        elif change > 2:
            signal = "BEARISH"; mod = -1.0
        elif change < -5:
            signal = "BULLISH"; mod = +1.5  # pledge release
        elif change < -2:
            signal = "BULLISH"; mod = +0.8
        else:
            signal = "NEUTRAL"; mod = 0.0

        return {
            "symbol":    symbol,
            "pledge_pct": round(latest_pct, 2),
            "pledge_chg": round(change, 2),
            "signal":    signal,
            "score_mod": mod,
        }
    except Exception as e:
        logger.debug("promoter_pledge %s: %s", symbol, e)
        return {"symbol": symbol, "signal": "NEUTRAL", "score_mod": 0.0}


def get_fii_futures_net(force: bool = False) -> dict:
    """
    FII futures net position change.
    If FII long > short: BULLISH
    If FII short > long: BEARISH (institutions hedging)
    Also checks: sudden flip in FII futures direction = major signal.
    """
    try:
        from participant_oi import get_participant_data, compute_participant_signal
        pd_data = get_participant_data(force=force)
        fii     = (pd_data or {}).get("FII", {})
        if not fii:
            return {}

        fut_long  = float(fii.get("fut_long",  0) or 0)
        fut_short = float(fii.get("fut_short", 0) or 0)
        net_fut   = fut_long - fut_short
        ratio     = fut_long / max(fut_short, 1)

        # 5-day cumulative FII cash
        from participant_oi import get_cumulative_fii
        cum5 = float(get_cumulative_fii(5))

        signal  = "BULLISH" if ratio > 1.2 else "BEARISH" if ratio < 0.8 else "NEUTRAL"
        cum_sig = "BULLISH" if cum5 > 2000 else "BEARISH" if cum5 < -2000 else "NEUTRAL"

        # Strong signal only when futures AND cash agree
        if signal == cum_sig == "BULLISH":
            mod = 2.0
        elif signal == cum_sig == "BEARISH":
            mod = -2.0
        elif signal == "BULLISH" or cum_sig == "BULLISH":
            mod = 1.0
        elif signal == "BEARISH" or cum_sig == "BEARISH":
            mod = -1.0
        else:
            mod = 0.0

        return {
            "fii_fut_ratio": round(ratio, 3),
            "fii_net_fut":   round(net_fut / 1e7, 2),  # Cr
            "fii_cum5_cash": round(cum5, 0),
            "signal":        signal,
            "cum_signal":    cum_sig,
            "score_mod":     mod,
        }
    except Exception as e:
        logger.debug("fii_futures_net: %s", e)
        return {}


def get_whale_composite_score(symbol: str, for_option: bool = False) -> dict:
    """
    Composite whale/institutional score for a symbol.
    Combines all available signals.

    Used as a modifier in signal_engine.
    Returns score_mod (-3 to +3) and narrative.
    """
    result = {
        "symbol":    symbol,
        "score_mod": 0.0,
        "signals":   [],
        "narrative": "",
    }

    # 1. FII futures
    fii = get_fii_futures_net()
    if fii:
        result["score_mod"] += fii.get("score_mod", 0)
        result["signals"].append(f"FII:{fii.get('signal','?')}")

    # 2. Options flow (for index symbols)
    _INDICES = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"}
    if symbol.upper() in _INDICES or for_option:
        opt = get_unusual_options_volume(symbol.upper() if symbol.upper() in _INDICES else "NIFTY")
        if opt:
            result["score_mod"] += opt.get("score_mod", 0)
            result["signals"].append(f"OI_flow:{opt.get('signal','?')}(PCR={opt.get('pcr',0):.2f})")

    # 3. Block deals (for stocks)
    if symbol.upper() not in _INDICES:
        try:
            deals = get_block_deals()
            sym_signal = deals.get("signals",{}).get(symbol.upper(),{})
            if sym_signal:
                result["score_mod"] += sym_signal.get("score_mod", 0)
                result["signals"].append(f"BlockDeal:{sym_signal.get('signal','?')}")
        except Exception:
            pass

        # 4. Promoter pledge
        try:
            pledge = get_promoter_pledge(symbol)
            if pledge.get("signal") != "NEUTRAL":
                result["score_mod"] += pledge.get("score_mod", 0)
                result["signals"].append(f"Pledge:{pledge.get('signal','?')}({pledge.get('pledge_pct',0):.0f}%)")
        except Exception:
            pass

    # Cap at ±3.0
    result["score_mod"] = round(max(-3.0, min(3.0, result["score_mod"])), 2)
    result["narrative"] = " | ".join(result["signals"]) if result["signals"] else "No whale data"
    return result


# Import numpy here since it's used in functions above
try:
    import numpy as np
except ImportError:
    pass
