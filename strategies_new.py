"""
strategies_new.py

New strategies added per gap analysis:
  1.  RSI-2 Mean Reversion (Larry Connors)
  2.  India VIX Extreme Trigger
  3.  Alligator + Awesome Oscillator (Bill Williams)
  4.  Cross-Sectional Momentum Ranking
  5.  Elder Triple Screen (Alexander Elder)
  6.  FII/DII 3-Day Trend Trigger
  7.  Gap-and-Go (Minervini)
  8.  CCI Trend Confirmation
  9.  Donchian Channel Breakout (live)
 10.  KAMA Trend Filter
 11.  KST Oscillator Crossover (Pring)
 12.  Elder Ray Divergence
 13.  Harmonic Gartley Pattern
 14.  Ultimate Oscillator Divergence
 15.  Aroon Trend Strength Entry
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try: return float(val) if val is not None and not (isinstance(val, float) and np.isnan(val)) else default
    except Exception: return default

def _col(df, name, default=0.0):
    return _safe(df[name].iloc[-1], default) if name in df.columns else default

def _base_signal(side: str, strategy: str, score: float, entry: float,
                 sl: float, target: float, regime: str = "UNKNOWN", **kw) -> dict:
    return {
        "side": side, "strategy": strategy, "score": score,
        "price": entry, "stop_loss": sl, "target": target,
        "regime": regime, "confluence": "MEDIUM", **kw
    }


# ── 1. RSI-2 Mean Reversion (Larry Connors) ──────────────────────────────────

def run_rsi2_mr_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Larry Connors RSI-2:
    BUY  when RSI(2) < 10 AND price above EMA(200)
    SELL when RSI(2) > 90 AND price below EMA(200)
    Exit when RSI(2) crosses 50.
    """
    try:
        from indicators import calculate_rsi, calculate_ema
        if len(df) < 210: return {}
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        rsi2  = calculate_rsi(close, 2)
        ema200= calculate_ema(close, 200)

        r2    = _safe(rsi2.iloc[-1])
        price = _safe(close.iloc[-1])
        e200  = _safe(ema200.iloc[-1])
        atr   = _col(df, "atr", price * 0.005)

        if r2 < 10 and price > e200:
            return _base_signal("BUY", "rsi2_mr", 6.5, price,
                                 price - 1.5 * atr, price + 2.0 * atr, "RANGE",
                                 rsi2_val=r2)
        if r2 > 90 and price < e200:
            return _base_signal("SELL", "rsi2_mr", 6.5, price,
                                 price + 1.5 * atr, price - 2.0 * atr, "RANGE",
                                 rsi2_val=r2)
    except Exception as e:
        logger.debug("rsi2_mr: %s", e)
    return {}


# ── 2. India VIX Extreme Trigger ─────────────────────────────────────────────

def run_vix_extreme_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    When India VIX > 1.4× its 20-day average (extreme fear):
      → Buy Nifty on dip to CPR/VWAP (mean reversion after fear spike)
    When India VIX < 0.7× 20-day avg (complacency):
      → Score boost for premium selling / range strategies
    """
    try:
        vix_now = float(kw.get("vix", 0) or 0)
        if vix_now <= 0:
            import config as _cfg
            vix_now = float(getattr(_cfg, "_LAST_VIX", 0) or 0)
        if vix_now <= 0: return {}

        vix_20d_avg = float(kw.get("vix_20d_avg", 15.0) or 15.0)
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price = _safe(close.iloc[-1])
        atr   = _col(df, "atr", price * 0.005)

        if vix_now > vix_20d_avg * 1.4:
            vwap = _col(df, "vwap", price)
            if price <= vwap * 1.002:
                return _base_signal("BUY", "vix_extreme", 7.0, price,
                                     price - 2 * atr, price + 3 * atr, "FEAR_SPIKE",
                                     vix=vix_now, vix_avg=vix_20d_avg)

        if vix_now < vix_20d_avg * 0.7:
            return {"side": None, "strategy": "vix_extreme",
                    "score": 5.5, "regime": "LOW_VIX_RANGE",
                    "vix": vix_now, "suggestion": "premium_selling"}
    except Exception as e:
        logger.debug("vix_extreme: %s", e)
    return {}


# ── 3. Alligator + Awesome Oscillator (Bill Williams) ────────────────────────

def run_alligator_ao_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Alligator eating (lips > teeth > jaw = BUY, lips < teeth < jaw = SELL)
    + Awesome Oscillator saucer or twin-peaks confirmation.
    Avoid when alligator is sleeping (lines converging).
    """
    try:
        from indicators import calculate_alligator
        if len(df) < 40: return {}
        jaw, teeth, lips = calculate_alligator(df)
        j = _safe(jaw.iloc[-1]); t = _safe(teeth.iloc[-1]); l = _safe(lips.iloc[-1])
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price = _safe(close.iloc[-1])
        atr   = _col(df, "atr", price * 0.005)
        ao    = _col(df, "awesome_oscillator", None)
        if ao is None:
            # compute AO inline if not pre-computed
            high = df["high"] if "high" in df.columns else df.iloc[:, 1]
            low  = df["low"]  if "low"  in df.columns else df.iloc[:, 2]
            median = (pd.to_numeric(high, errors="coerce") +
                      pd.to_numeric(low,  errors="coerce")) / 2.0
            ao = float((median.rolling(5).mean() - median.rolling(34).mean()).iloc[-1])

        sleeping = abs(j - t) < atr * 0.3 and abs(t - l) < atr * 0.3
        if sleeping: return {}

        if l > t > j and ao > 0:
            return _base_signal("BUY", "alligator_ao", 7.0, price,
                                 j - atr, price + 2.5 * atr, "TREND")
        if l < t < j and ao < 0:
            return _base_signal("SELL", "alligator_ao", 7.0, price,
                                 j + atr, price - 2.5 * atr, "TREND")
    except Exception as e:
        logger.debug("alligator_ao: %s", e)
    return {}


# ── 4. Cross-Sectional Momentum Ranking ──────────────────────────────────────

_MOMENTUM_RANK: dict = {}   # {symbol: roc_20d} — updated each scan cycle

def update_momentum_rank(symbol: str, roc_20d: float) -> None:
    _MOMENTUM_RANK[symbol] = roc_20d

def run_cross_momentum_strategy(df: pd.DataFrame, symbol: str = "", **kw) -> dict:
    """
    Cross-sectional momentum: symbols in top tercile of 20d ROC get +1.5 score.
    Symbols in bottom tercile get -1.5 score modifier.
    Does not generate primary signals — returns score adjustment only.
    """
    try:
        from indicators import calculate_roc
        if len(df) < 25: return {}
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        roc   = calculate_roc(close, 20)
        roc20 = _safe(roc.iloc[-1])
        if symbol: update_momentum_rank(symbol, roc20)

        if len(_MOMENTUM_RANK) < 5: return {}
        vals   = sorted(_MOMENTUM_RANK.values())
        p33    = np.percentile(vals, 33)
        p67    = np.percentile(vals, 67)
        if roc20 > p67:
            return {"side": None, "strategy": "cross_momentum",
                    "score_modifier": 1.5, "regime": "MOMENTUM_TOP"}
        if roc20 < p33:
            return {"side": None, "strategy": "cross_momentum",
                    "score_modifier": -1.5, "regime": "MOMENTUM_BOTTOM"}
    except Exception as e:
        logger.debug("cross_momentum: %s", e)
    return {}


# ── 5. Elder Triple Screen ────────────────────────────────────────────────────

def run_elder_triple_screen_strategy(df: pd.DataFrame, df_htf: pd.DataFrame = None, **kw) -> dict:
    """
    Screen 1: Weekly/daily MACD histogram direction (from df_htf or df tail)
    Screen 2: Daily/hourly stochastic oversold/overbought against trend
    Screen 3: 5-min momentum entry (breakout of last 2 bars high/low)
    """
    try:
        from indicators import calculate_macd, calculate_stochastic
        if len(df) < 50: return {}

        close  = df["close"]  if "close"  in df.columns else df.iloc[:, 3]
        high   = df["high"]   if "high"   in df.columns else df.iloc[:, 1]
        low    = df["low"]    if "low"    in df.columns else df.iloc[:, 2]
        price  = _safe(close.iloc[-1])
        atr    = _col(df, "atr", price * 0.005)

        # Screen 1: trend from HTF MACD histogram
        htf_df = df_htf if df_htf is not None and len(df_htf) >= 30 else df.tail(60)
        htf_close = htf_df["close"] if "close" in htf_df.columns else htf_df.iloc[:, 3]
        _, _, htf_hist = calculate_macd(htf_close)
        trend_up   = _safe(htf_hist.iloc[-1]) > 0
        trend_down = _safe(htf_hist.iloc[-1]) < 0

        # Screen 2: stochastic pullback against trend
        stoch_k, _ = calculate_stochastic(df)
        sk = _safe(stoch_k.iloc[-1])

        # Screen 3: trailing buy/sell stop
        entry_high = _safe(pd.to_numeric(high, errors="coerce").iloc[-2])
        entry_low  = _safe(pd.to_numeric(low,  errors="coerce").iloc[-2])

        if trend_up and sk < 30:
            return _base_signal("BUY", "elder_triple_screen", 7.5, entry_high + 0.05,
                                 entry_low - atr, entry_high + 2.5 * atr, "TREND",
                                 stoch_k=sk)
        if trend_down and sk > 70:
            return _base_signal("SELL", "elder_triple_screen", 7.5, entry_low - 0.05,
                                 entry_high + atr, entry_low - 2.5 * atr, "TREND",
                                 stoch_k=sk)
    except Exception as e:
        logger.debug("elder_triple_screen: %s", e)
    return {}


# ── 6. FII/DII 3-Day Trend Trigger ───────────────────────────────────────────

def run_fii_dii_trend_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    FII net futures long for 3 consecutive days → BUY confirmation.
    FII net futures short for 3 consecutive days → SELL confirmation.
    Uses cached fii_futures_net from participant_oi.
    """
    try:
        from participant_oi import get_participant_data
        data = get_participant_data()
        if not data: return {}
        fii_net = data.get("fii_futures_net", [])
        if len(fii_net) < 3: return {}
        last3 = fii_net[-3:]

        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price = _safe(close.iloc[-1])
        atr   = _col(df, "atr", price * 0.005)

        if all(v > 500 for v in last3):   # FII buying 3 days straight
            return _base_signal("BUY", "fii_dii_trend", 7.0, price,
                                 price - 2 * atr, price + 3 * atr, "INSTITUTIONAL_BULL",
                                 fii_net_3d=sum(last3))
        if all(v < -500 for v in last3):  # FII selling 3 days straight
            return _base_signal("SELL", "fii_dii_trend", 7.0, price,
                                 price + 2 * atr, price - 3 * atr, "INSTITUTIONAL_BEAR",
                                 fii_net_3d=sum(last3))
    except Exception as e:
        logger.debug("fii_dii_trend: %s", e)
    return {}


# ── 7. Gap-and-Go ─────────────────────────────────────────────────────────────

def run_gap_and_go_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Gap-and-Go (Minervini / Steve Burns):
    Opening gap > 0.8% with vol > 1.5× avg → buy breakout of first candle high.
    Different from gap-fill: rides the gap direction, not fades it.
    """
    try:
        import datetime as _dt
        if len(df) < 20: return {}
        now_h = _dt.datetime.now().hour
        now_m = _dt.datetime.now().minute
        if not (now_h == 9 and 20 <= now_m <= 45):  # only first 25 min
            return {}

        close  = df["close"]  if "close"  in df.columns else df.iloc[:, 3]
        high   = df["high"]   if "high"   in df.columns else df.iloc[:, 1]
        low    = df["low"]    if "low"    in df.columns else df.iloc[:, 2]
        volume = df["volume"] if "volume" in df.columns else None

        prev_close  = _safe(close.iloc[-2])
        first_high  = _safe(pd.to_numeric(high,  errors="coerce").iloc[-1])
        first_low   = _safe(pd.to_numeric(low,   errors="coerce").iloc[-1])
        open_price  = _safe(df["open"].iloc[-1] if "open" in df.columns else close.iloc[-1])
        price       = _safe(close.iloc[-1])
        atr         = _col(df, "atr", price * 0.005)

        gap_pct = (open_price - prev_close) / (prev_close or 1) * 100
        vol_ok  = True
        if volume is not None:
            vol_avg = pd.to_numeric(volume, errors="coerce").rolling(20).mean().iloc[-1]
            vol_now = pd.to_numeric(volume, errors="coerce").iloc[-1]
            vol_ok  = _safe(vol_now) > _safe(vol_avg) * 1.5

        if gap_pct > 0.8 and vol_ok and price >= first_high * 0.999:
            return _base_signal("BUY", "gap_and_go", 7.5, first_high,
                                 first_low, first_high + 2 * atr, "TREND",
                                 gap_pct=gap_pct)
        if gap_pct < -0.8 and vol_ok and price <= first_low * 1.001:
            return _base_signal("SELL", "gap_and_go", 7.5, first_low,
                                 first_high, first_low - 2 * atr, "TREND",
                                 gap_pct=gap_pct)
    except Exception as e:
        logger.debug("gap_and_go: %s", e)
    return {}


# ── 8. CCI Trend Confirmation ─────────────────────────────────────────────────

def run_cci_trend_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    CCI > +100 + ADX > 20 = trend continuation BUY.
    CCI < -100 + ADX > 20 = trend continuation SELL.
    CCI crossing zero with rising ADX = early entry.
    """
    try:
        from indicators import calculate_cci
        if len(df) < 25: return {}
        cci = calculate_cci(df) if "cci" not in df.columns else df["cci"]
        c   = _safe(cci.iloc[-1])
        cp  = _safe(cci.iloc[-2]) if len(cci) > 2 else 0.0
        adx = _col(df, "adx", 0)
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price = _safe(close.iloc[-1])
        atr   = _col(df, "atr", price * 0.005)

        if c > 100 and adx > 20:
            return _base_signal("BUY", "cci_trend", 6.0, price,
                                 price - 1.5 * atr, price + 2.5 * atr, "TREND", cci=c)
        if c < -100 and adx > 20:
            return _base_signal("SELL", "cci_trend", 6.0, price,
                                 price + 1.5 * atr, price - 2.5 * atr, "TREND", cci=c)
        # Zero-cross entry
        if cp < 0 < c and adx > 15:
            return _base_signal("BUY", "cci_zero_cross", 5.5, price,
                                 price - atr, price + 2 * atr, "TREND", cci=c)
        if cp > 0 > c and adx > 15:
            return _base_signal("SELL", "cci_zero_cross", 5.5, price,
                                 price + atr, price - 2 * atr, "TREND", cci=c)
    except Exception as e:
        logger.debug("cci_trend: %s", e)
    return {}


# ── 9. Donchian Channel Breakout (live) ──────────────────────────────────────

def run_donchian_breakout_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Classic Donchian 20-period channel breakout.
    Price touches upper band = BUY; lower band = SELL.
    Confirmed with volume ratio > 1.2.
    """
    try:
        from indicators import calculate_donchian_channel
        if len(df) < 25: return {}
        dc_upper, dc_lower, dc_mid = calculate_donchian_channel(df, 20)
        close  = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price  = _safe(close.iloc[-1])
        upper  = _safe(dc_upper.iloc[-1])
        lower  = _safe(dc_lower.iloc[-1])
        atr    = _col(df, "atr", price * 0.005)
        vol_r  = _col(df, "volume_ratio", 1.0)

        if price >= upper * 0.998 and vol_r > 1.2:
            return _base_signal("BUY", "donchian_breakout", 6.5, price,
                                 _safe(dc_mid.iloc[-1]), upper + 1.5 * atr, "BREAKOUT")
        if price <= lower * 1.002 and vol_r > 1.2:
            return _base_signal("SELL", "donchian_breakout", 6.5, price,
                                 _safe(dc_mid.iloc[-1]), lower - 1.5 * atr, "BREAKOUT")
    except Exception as e:
        logger.debug("donchian_breakout: %s", e)
    return {}


# ── 10. KAMA Trend Filter Strategy ───────────────────────────────────────────

def run_kama_trend_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    KAMA adapts to market efficiency. When close crosses above KAMA with ADX>20:
    BUY. When close crosses below KAMA: SELL.
    Particularly useful in regime-variable NSE intraday.
    """
    try:
        from indicators import calculate_kama
        if len(df) < 30: return {}
        close  = df["close"] if "close" in df.columns else df.iloc[:, 3]
        close_s = pd.to_numeric(close, errors="coerce")
        kama   = df["kama"] if "kama" in df.columns else calculate_kama(close_s)
        price  = _safe(close_s.iloc[-1])
        prev_c = _safe(close_s.iloc[-2])
        k      = _safe(kama.iloc[-1])
        pk     = _safe(kama.iloc[-2]) if len(kama) > 2 else k
        atr    = _col(df, "atr", price * 0.005)
        adx    = _col(df, "adx", 0)

        if prev_c <= pk and price > k and adx > 20:
            return _base_signal("BUY", "kama_trend", 6.5, price,
                                 k - atr, price + 2 * atr, "TREND", kama=k)
        if prev_c >= pk and price < k and adx > 20:
            return _base_signal("SELL", "kama_trend", 6.5, price,
                                 k + atr, price - 2 * atr, "TREND", kama=k)
    except Exception as e:
        logger.debug("kama_trend: %s", e)
    return {}


# ── 11. KST Oscillator Crossover (Pring) ─────────────────────────────────────

def run_kst_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Martin Pring KST: bullish when KST crosses above signal with positive slope.
    Bearish when KST crosses below signal with negative slope.
    Best used as confirmation for trend entries.
    """
    try:
        from indicators import calculate_kst
        if len(df) < 50: return {}
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        if "kst" in df.columns:
            kst_line = df["kst"]; kst_sig = df["kst_signal"]
        else:
            kst_line, kst_sig = calculate_kst(pd.to_numeric(close, errors="coerce"))

        k  = _safe(kst_line.iloc[-1]); kp = _safe(kst_line.iloc[-2]) if len(kst_line) > 2 else k
        s  = _safe(kst_sig.iloc[-1]);  sp = _safe(kst_sig.iloc[-2])  if len(kst_sig)  > 2 else s
        price = _safe(pd.to_numeric(close, errors="coerce").iloc[-1])
        atr   = _col(df, "atr", price * 0.005)

        if kp < sp and k > s and k > 0:
            return _base_signal("BUY", "kst", 6.0, price,
                                 price - 1.5 * atr, price + 2.5 * atr, "TREND", kst=k)
        if kp > sp and k < s and k < 0:
            return _base_signal("SELL", "kst", 6.0, price,
                                 price + 1.5 * atr, price - 2.5 * atr, "TREND", kst=k)
    except Exception as e:
        logger.debug("kst: %s", e)
    return {}


# ── 12. Elder Ray Divergence ──────────────────────────────────────────────────

def run_elder_ray_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Elder Ray: Bear Power < 0 but rising (bullish divergence) + price above EMA(13) = BUY.
    Bull Power > 0 but falling (bearish divergence) + price below EMA(13) = SELL.
    Completes the Holy Grail setup as Elder intended.
    """
    try:
        from indicators import calculate_elder_ray
        if len(df) < 20: return {}
        bull_p, bear_p = (
            (df["bull_power"], df["bear_power"])
            if "bull_power" in df.columns
            else calculate_elder_ray(df)
        )
        close  = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price  = _safe(pd.to_numeric(close, errors="coerce").iloc[-1])
        atr    = _col(df, "atr", price * 0.005)
        ema13  = _col(df, "ema_fast", price)
        bear   = _safe(bear_p.iloc[-1]); bear_p2 = _safe(bear_p.iloc[-2]) if len(bear_p) > 2 else bear
        bull   = _safe(bull_p.iloc[-1]); bull_p2 = _safe(bull_p.iloc[-2]) if len(bull_p) > 2 else bull

        if bear < 0 and bear > bear_p2 and price > ema13:
            return _base_signal("BUY", "elder_ray", 6.5, price,
                                 price - 1.5 * atr, price + 2.5 * atr, "TREND",
                                 bear_power=bear)
        if bull > 0 and bull < bull_p2 and price < ema13:
            return _base_signal("SELL", "elder_ray", 6.5, price,
                                 price + 1.5 * atr, price - 2.5 * atr, "TREND",
                                 bull_power=bull)
    except Exception as e:
        logger.debug("elder_ray: %s", e)
    return {}


# ── 13. Harmonic Gartley Pattern ─────────────────────────────────────────────

def run_harmonic_gartley_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Gartley 222: XA-AB-BC-CD structure at Fibonacci PRZ.
    AB = 0.618×XA (±5%), CD ends at 0.786×XA from X.
    Entry at D, stop beyond X, target 0.618 retracement of AD.
    """
    try:
        from indicators import detect_swing_highs_lows
        if len(df) < 50: return {}
        swings = detect_swing_highs_lows(df, lookback=5)
        if swings is None or len(swings) < 8: return {}
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price = _safe(pd.to_numeric(close, errors="coerce").iloc[-1])
        atr   = _col(df, "atr", price * 0.005)

        # Extract last 4 alternating swing points
        highs = swings[swings.get("swing_high", pd.Series()).eq(1)].tail(3) if "swing_high" in swings.columns else pd.DataFrame()
        lows  = swings[swings.get("swing_low",  pd.Series()).eq(1)].tail(3) if "swing_low"  in swings.columns else pd.DataFrame()
        if len(highs) < 2 or len(lows) < 2: return {}

        # Bullish Gartley: X low, A high, B low, C high, D low
        try:
            X = _safe(lows.iloc[-2]["low"]  if "low"  in lows.columns  else lows.iloc[-2, 2])
            A = _safe(highs.iloc[-2]["high"] if "high" in highs.columns else highs.iloc[-2, 1])
            B = _safe(lows.iloc[-1]["low"]   if "low"  in lows.columns  else lows.iloc[-1, 2])
            D = price
            XA = A - X
            if XA <= 0: return {}
            AB_ratio = (A - B) / XA
            D_ratio  = (A - D) / XA

            if 0.55 < AB_ratio < 0.68 and 0.73 < D_ratio < 0.83:
                target1 = D + 0.382 * (A - D)
                target2 = D + 0.618 * (A - D)
                return _base_signal("BUY", "harmonic_gartley", 7.5, price,
                                     X - atr, target1, "HARMONIC",
                                     pattern="gartley_bullish",
                                     ab_ratio=round(AB_ratio, 3),
                                     target2=target2)
        except Exception:
            pass
    except Exception as e:
        logger.debug("harmonic_gartley: %s", e)
    return {}


# ── 14. Ultimate Oscillator Divergence ───────────────────────────────────────

def run_uo_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Larry Williams Ultimate Oscillator:
    UO < 30 and price making lower low but UO making higher low = bullish divergence.
    UO > 70 = overbought sell signal.
    """
    try:
        from indicators import calculate_ultimate_oscillator
        if len(df) < 35: return {}
        uo = df["uo"] if "uo" in df.columns else calculate_ultimate_oscillator(df)
        u  = _safe(uo.iloc[-1]); u2 = _safe(uo.iloc[-5]) if len(uo) > 5 else u
        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        close_s = pd.to_numeric(close, errors="coerce")
        price = _safe(close_s.iloc[-1]); price5 = _safe(close_s.iloc[-5]) if len(close_s) > 5 else price
        atr = _col(df, "atr", price * 0.005)

        # Bullish divergence: price lower low, UO higher low
        if price < price5 and u > u2 and u < 40:
            return _base_signal("BUY", "uo_divergence", 6.5, price,
                                 price - 1.5 * atr, price + 2.5 * atr, "RANGE", uo=u)
        if u > 70:
            return _base_signal("SELL", "uo_overbought", 6.0, price,
                                 price + atr, price - 2 * atr, "RANGE", uo=u)
        if u < 30:
            return _base_signal("BUY", "uo_oversold", 6.0, price,
                                 price - atr, price + 2 * atr, "RANGE", uo=u)
    except Exception as e:
        logger.debug("uo_strategy: %s", e)
    return {}


# ── 15. Aroon Trend Strength Entry ───────────────────────────────────────────

def run_aroon_strategy(df: pd.DataFrame, **kw) -> dict:
    """
    Aroon Up > 70 and Aroon Down < 30 = strong uptrend. BUY on dips.
    Aroon Down > 70 and Aroon Up < 30 = strong downtrend. SELL on rallies.
    Aroon cross = early trend change signal.
    """
    try:
        from indicators import calculate_aroon
        if len(df) < 30: return {}
        if "aroon_up" in df.columns:
            au = _safe(df["aroon_up"].iloc[-1])
            ad = _safe(df["aroon_down"].iloc[-1])
            ao = _safe(df["aroon_osc"].iloc[-1])
        else:
            au_s, ad_s, ao_s = calculate_aroon(df)
            au = _safe(au_s.iloc[-1]); ad = _safe(ad_s.iloc[-1]); ao = _safe(ao_s.iloc[-1])

        close = df["close"] if "close" in df.columns else df.iloc[:, 3]
        price = _safe(pd.to_numeric(close, errors="coerce").iloc[-1])
        atr   = _col(df, "atr", price * 0.005)
        vwap  = _col(df, "vwap", price)

        if au > 70 and ad < 30 and price <= vwap * 1.002:
            return _base_signal("BUY", "aroon_trend", 6.5, price,
                                 price - 1.5 * atr, price + 2.5 * atr, "TREND",
                                 aroon_up=au, aroon_down=ad)
        if ad > 70 and au < 30 and price >= vwap * 0.998:
            return _base_signal("SELL", "aroon_trend", 6.5, price,
                                 price + 1.5 * atr, price - 2.5 * atr, "TREND",
                                 aroon_up=au, aroon_down=ad)
    except Exception as e:
        logger.debug("aroon_trend: %s", e)
    return {}
