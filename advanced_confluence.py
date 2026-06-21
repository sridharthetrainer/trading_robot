"""
advanced_confluence.py — 5 High-Value Missing Strategies

Built from:
  SMC: Inner Circle Trader (ICT) 2.0 concepts
  VWAP Bands: Brian Shannon "Technical Analysis Using Multiple Timeframes"
  Heikin Ashi: Steve Nison "Beyond Candlesticks"
  ORB 2-min: Jeff Cooper "Hit and Run Trading"
  CANSLIM: William O'Neil "How to Make Money in Stocks"
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SMART MONEY CONCEPTS (SMC / ICT 2.0)
# ─────────────────────────────────────────────────────────────────────────────
def run_smc_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Smart Money Concepts — the most influential institutional trading framework.
    
    Concepts:
      Change of Character (ChoCh) — first sign of reversal
      Break of Structure (BOS)    — trend continuation confirmed
      Order Block (OB)            — last bearish/bullish candle before impulse
      Fair Value Gap (FVG)        — imbalance zone price returns to fill
      Liquidity Pool              — where retail stops cluster (just above highs/lows)
      
    ENTRY:
      BUY:  ChoCh up + price returns to bullish OB + FVG fill = high prob long
      SELL: ChoCh down + price returns to bearish OB + FVG fill = high prob short
    """
    empty = {"strategy":"smc","score":0.0,"direction":None,"side":None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 30:
            return empty

        highs  = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        lows   = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        closes = df_c["close"].values
        n      = len(closes)

        # ── Swing highs and lows ───────────────────────────────────────────
        sh = [i for i in range(2,n-2) if highs[i]>highs[i-1] and highs[i]>highs[i+1]]
        sl = [i for i in range(2,n-2) if lows[i]<lows[i-1]  and lows[i]<lows[i+1]]

        if len(sh) < 2 or len(sl) < 2:
            return empty

        # ── Change of Character (ChoCh) ────────────────────────────────────
        # Bullish ChoCh: downtrend (LL, LH) then price breaks ABOVE last LH
        last_sh    = highs[sh[-1]]
        last_sl    = lows[sl[-1]]
        prev_sh    = highs[sh[-2]] if len(sh)>1 else last_sh
        price      = float(closes[-1])

        bullish_choch = (last_sh > prev_sh) and (price > last_sh)   # HL → HH
        bearish_choch = (last_sl < lows[sl[-2] if len(sl)>1 else sl[-1]]) and (price < last_sl)

        # ── Fair Value Gap (FVG) ───────────────────────────────────────────
        # Bullish FVG: candle[i-2].high < candle[i].low
        bull_fvg = highs[-3] < lows[-1] if n>=3 else False
        bear_fvg = lows[-3] > highs[-1] if n>=3 else False

        # ── Order Block (last bearish candle before bullish impulse) ───────
        # Simplified: look for engulfing setup
        bull_ob = (closes[-2] < lows[-3]) and (closes[-1] > highs[-2])  # bullish engulf
        bear_ob = (closes[-2] > highs[-3]) and (closes[-1] < lows[-2])  # bearish engulf

        # ── Liquidity sweep ────────────────────────────────────────────────
        recent_high = float(np.max(highs[-20:-3])) if n>20 else float(np.max(highs[:-3]))
        recent_low  = float(np.min(lows[-20:-3]))  if n>20 else float(np.min(lows[:-3]))
        bull_sweep  = float(lows[-2]) < recent_low  and float(closes[-1]) > recent_low
        bear_sweep  = float(highs[-2]) > recent_high and float(closes[-1]) < recent_high

        # ── Score ──────────────────────────────────────────────────────────
        buy_score  = sum([bullish_choch*2.5, bull_fvg*1.5, bull_ob*2.0, bull_sweep*3.0])
        sell_score = sum([bearish_choch*2.5, bear_fvg*1.5, bear_ob*2.0, bear_sweep*3.0])

        if buy_score >= 3.0 and buy_score > sell_score:
            detail = " + ".join(filter(None,[
                "ChoCh↑" if bullish_choch else "",
                "FVG↑"   if bull_fvg else "",
                "OB↑"    if bull_ob else "",
                "Sweep↑" if bull_sweep else ""
            ]))
            return {"strategy":"smc","score":round(buy_score,2),
                    "direction":"BUY","side":"BUY","smc_detail":detail}
        if sell_score >= 3.0:
            return {"strategy":"smc","score":round(sell_score,2),
                    "direction":"SELL","side":"SELL"}
    except Exception as e:
        logger.debug("smc: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 2. VWAP BANDS STRATEGY
# ─────────────────────────────────────────────────────────────────────────────
def run_vwap_bands_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    VWAP Bands — standard deviation bands around VWAP.
    
    Used by every institutional prop desk for intraday mean reversion.
    
    Logic:
      1st band (±1σ): Normal range — fade moves back to VWAP
      2nd band (±2σ): Extended — high probability mean reversion
      3rd band (±3σ): Extreme — very strong mean reversion signal
      
    ENTRY:
      BUY:  Price touches -2σ band + RSI oversold + volume spike
      SELL: Price touches +2σ band + RSI overbought + volume spike
    """
    empty = {"strategy":"vwap_bands","score":0.0,"direction":None,"side":None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20 or "volume" not in df_c.columns:
            return empty

        closes = df_c["close"].values
        highs  = df_c["high"].values  if "high"  in df_c.columns else closes
        lows   = df_c["low"].values   if "low"   in df_c.columns else closes
        vols   = df_c["volume"].values
        n      = len(closes)

        # ── Intraday VWAP ─────────────────────────────────────────────────
        typical = (highs + lows + closes) / 3
        cum_vol = np.cumsum(vols)
        cum_tpv = np.cumsum(typical * vols)
        vwap    = cum_tpv / (cum_vol + 1e-9)
        vwap_v  = float(vwap[-1])

        # Standard deviation from VWAP
        sq_dev  = (typical - vwap) ** 2
        cum_sqdev = np.cumsum(sq_dev * vols)
        variance  = cum_sqdev / (cum_vol + 1e-9)
        vwap_std  = float(np.sqrt(np.abs(variance[-1])))

        if vwap_std < 1:
            return empty

        # Bands
        b1u = vwap_v + 1*vwap_std; b1d = vwap_v - 1*vwap_std
        b2u = vwap_v + 2*vwap_std; b2d = vwap_v - 2*vwap_std
        b3u = vwap_v + 3*vwap_std; b3d = vwap_v - 3*vwap_std

        price = float(closes[-1])

        # RSI (14)
        diffs = np.diff(closes[-15:])
        gains = np.where(diffs>0, diffs, 0)
        losses= np.where(diffs<0,-diffs, 0)
        rs    = (np.mean(gains)+1e-9) / (np.mean(losses)+1e-9)
        rsi   = 100 - 100/(1+rs)

        # Volume spike
        vol_ratio = float(vols[-1]) / (float(np.mean(vols[-20:])) + 1e-9)

        # Score
        buy_score = sell_score = 0.0

        if price < b3d:   # extreme oversold
            buy_score = 6.0 + (rsi < 30)*1.0 + (vol_ratio>1.5)*0.5
        elif price < b2d: # oversold
            buy_score = 4.0 + (rsi < 40)*1.0 + (vol_ratio>1.2)*0.5
        elif price < b1d and rsi < 45: # mild
            buy_score = 2.5

        if price > b3u:   # extreme overbought
            sell_score = 6.0 + (rsi > 70)*1.0 + (vol_ratio>1.5)*0.5
        elif price > b2u: # overbought
            sell_score = 4.0 + (rsi > 60)*1.0 + (vol_ratio>1.2)*0.5
        elif price > b1u and rsi > 55:
            sell_score = 2.5

        if buy_score >= 2.5:
            return {"strategy":"vwap_bands","score":round(buy_score,2),
                    "direction":"BUY","side":"BUY",
                    "vwap":round(vwap_v,2),"band":round(b2d,2),"rsi":round(rsi,1)}
        if sell_score >= 2.5:
            return {"strategy":"vwap_bands","score":round(sell_score,2),
                    "direction":"SELL","side":"SELL",
                    "vwap":round(vwap_v,2),"band":round(b2u,2),"rsi":round(rsi,1)}
    except Exception as e:
        logger.debug("vwap_bands: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 3. HEIKIN ASHI TREND STRATEGY
# ─────────────────────────────────────────────────────────────────────────────
def run_heikin_ashi_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Heikin Ashi — smoothed candles reduce noise dramatically.
    
    HA formula:
      HA_Close = (O+H+L+C)/4
      HA_Open  = (prev_HA_Open + prev_HA_Close)/2
      HA_High  = max(H, HA_Open, HA_Close)
      HA_Low   = min(L, HA_Open, HA_Close)
      
    Strong signal: 8+ consecutive same-colour HA candles with no lower shadows (BUY)
    or no upper shadows (SELL).
    """
    empty = {"strategy":"heikin_ashi","score":0.0,"direction":None,"side":None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 15:
            return empty

        o = df_c["open"].values  if "open"  in df_c.columns else df_c["close"].values
        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        c = df_c["close"].values

        # Compute Heikin Ashi
        ha_c = (o + h + l + c) / 4
        ha_o = np.zeros(len(c))
        ha_o[0] = (o[0] + c[0]) / 2
        for i in range(1, len(c)):
            ha_o[i] = (ha_o[i-1] + ha_c[i-1]) / 2
        ha_h = np.maximum(h, np.maximum(ha_o, ha_c))
        ha_l = np.minimum(l, np.minimum(ha_o, ha_c))

        # Count consecutive bullish/bearish HA candles
        is_bull = ha_c > ha_o  # bullish HA candle

        consec_bull = consec_bear = 0
        for i in range(len(is_bull)-1, -1, -1):
            if is_bull[i]:
                if consec_bear == 0: consec_bull += 1
                else: break
            else:
                if consec_bull == 0: consec_bear += 1
                else: break

        # Strong signal: 5+ consecutive candles
        # No lower shadow (bullish) or no upper shadow (bearish) = extra strength
        no_lower_shadow = float(ha_l[-1]) >= float(ha_o[-1]) * 0.999
        no_upper_shadow = float(ha_h[-1]) <= float(ha_o[-1]) * 1.001

        buy_score  = 0.0
        sell_score = 0.0

        if consec_bull >= 8:
            buy_score = 5.0 + no_lower_shadow*1.5
        elif consec_bull >= 5:
            buy_score = 3.5 + no_lower_shadow*1.0
        elif consec_bull >= 3:
            buy_score = 2.0

        if consec_bear >= 8:
            sell_score = 5.0 + no_upper_shadow*1.5
        elif consec_bear >= 5:
            sell_score = 3.5 + no_upper_shadow*1.0
        elif consec_bear >= 3:
            sell_score = 2.0

        if buy_score >= 2.0:
            return {"strategy":"heikin_ashi","score":round(buy_score,2),
                    "direction":"BUY","side":"BUY","consec":consec_bull}
        if sell_score >= 2.0:
            return {"strategy":"heikin_ashi","score":round(sell_score,2),
                    "direction":"SELL","side":"SELL","consec":consec_bear}
    except Exception as e:
        logger.debug("heikin_ashi: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 4. OPENING RANGE BREAKOUT 2-MINUTE
# ─────────────────────────────────────────────────────────────────────────────
def run_orb_2min_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    2-Minute Opening Range Breakout — Jeff Cooper "Hit and Run Trading".
    
    The first 2 minutes (9:15-9:17) establish the range.
    Breakout above high or below low = directional signal.
    
    Works best on:
      - Volatile stocks with pre-market news
      - Index futures on event days
      - Expiry day morning session
      
    Confirmation: Volume 2× average on breakout bar.
    """
    empty = {"strategy":"orb_2min","score":0.0,"direction":None,"side":None}
    try:
        from datetime import datetime, time as dtime
        now = datetime.now().time()
        # Only valid in first 15 min of market (9:15-9:30)
        if not (dtime(9,17) <= now <= dtime(9,30)):
            return empty

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 3:
            return empty

        # Use first 2 bars as the opening range
        orb_high = float(df_c["high"].iloc[:2].max() if "high" in df_c.columns
                         else df_c["close"].iloc[:2].max())
        orb_low  = float(df_c["low"].iloc[:2].min()  if "low"  in df_c.columns
                         else df_c["close"].iloc[:2].min())
        orb_range = orb_high - orb_low
        if orb_range <= 0:
            return empty

        current = float(df_c["close"].iloc[-1])
        cur_high = float(df_c["high"].iloc[-1]) if "high" in df_c.columns else current
        cur_low  = float(df_c["low"].iloc[-1])  if "low"  in df_c.columns else current

        # Volume confirmation
        vol_ratio = 1.0
        if "volume" in df_c.columns and len(df_c) >= 5:
            cur_vol = float(df_c["volume"].iloc[-1])
            avg_vol = float(df_c["volume"].iloc[:-1].mean())
            vol_ratio = cur_vol / max(avg_vol, 1)

        # Breakout
        if cur_high > orb_high and current > orb_high:
            score = 3.5 + (vol_ratio > 2.0)*1.5 + (vol_ratio > 1.5)*0.5
            return {"strategy":"orb_2min","score":round(score,2),
                    "direction":"BUY","side":"BUY",
                    "orb_high":round(orb_high,2),"orb_low":round(orb_low,2)}
        if cur_low < orb_low and current < orb_low:
            score = 3.5 + (vol_ratio > 2.0)*1.5 + (vol_ratio > 1.5)*0.5
            return {"strategy":"orb_2min","score":round(score,2),
                    "direction":"SELL","side":"SELL",
                    "orb_high":round(orb_high,2),"orb_low":round(orb_low,2)}
    except Exception as e:
        logger.debug("orb_2min: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 5. CANSLIM FUNDAMENTALS FILTER (for stocks, not indices)
# ─────────────────────────────────────────────────────────────────────────────
def run_canslim_filter(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    CANSLIM filter — William O'Neil "How to Make Money in Stocks"
    
    Technical proxy (no fundamental data needed):
      C = Current earnings proxy: 52-week RS > 70
      A = Annual growth proxy: price > 200MA (institutional accumulation)
      N = New highs: price within 15% of 52-week high
      S = Supply/demand: volume expansion on up days
      L = Leader: RS (Relative Strength) > 80th percentile in sector
      I = Institutional sponsorship: delivery % > 40% (via BhavCopy)
      M = Market direction: NIFTY in uptrend (use regime engine)
      
    This is a FILTER not a signal — reduces false entries on weak stocks.
    Returns positive score modifier (+2 to +4) for strong CANSLIM stocks.
    Returns negative modifier (-2) for weak stocks.
    """
    empty = {"strategy":"canslim","score":0.0,"direction":None,"side":None}
    try:
        _INDICES = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"}
        if symbol.upper() in _INDICES:
            return empty  # CANSLIM is for stocks, not indices

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 50:
            return empty

        closes = df_c["close"].values
        vols   = df_c["volume"].values if "volume" in df_c.columns else np.ones(len(closes))
        price  = float(closes[-1])

        score_mod = 0.0

        # N: Near 52-week high (or at least 3-month high)
        high_52w = float(np.max(closes[-min(252,len(closes)):]))
        if price >= high_52w * 0.85:  # within 15% of 52w high
            score_mod += 1.0

        # L: Price above 200-day MA (institutional interest)
        ma200 = float(np.mean(closes[-min(200,len(closes)):])) if len(closes) >= 40 else price
        if price > ma200:
            score_mod += 0.5

        # S: Volume expanding on up days
        recent_up   = [i for i in range(1,min(11,len(closes))) if closes[-i] > closes[-i-1]]
        recent_down = [i for i in range(1,min(11,len(closes))) if closes[-i] < closes[-i-1]]
        avg_up_vol  = float(np.mean([vols[-i] for i in recent_up]))   if recent_up else 0
        avg_dn_vol  = float(np.mean([vols[-i] for i in recent_down])) if recent_down else 1
        if avg_up_vol > avg_dn_vol * 1.2:
            score_mod += 1.0
        elif avg_up_vol < avg_dn_vol * 0.8:
            score_mod -= 1.0

        # C: Momentum (price above 50MA — quarterly trend)
        ma50 = float(np.mean(closes[-min(50,len(closes)):])) if len(closes) >= 20 else price
        if price > ma50 * 1.03:  # 3% above 50MA
            score_mod += 0.5

        # Return as directional score (only positive = BUY filter)
        if score_mod >= 2.0:
            return {"strategy":"canslim","score":round(score_mod,2),
                    "direction":"BUY","side":"BUY","canslim_score":round(score_mod,2)}
        if score_mod <= -1.0:
            return {"strategy":"canslim","score":round(abs(score_mod),2),
                    "direction":"SELL","side":"SELL"}
    except Exception as e:
        logger.debug("canslim: %s", e)
    return empty
