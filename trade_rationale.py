"""
trade_rationale.py — Auto-generate SAHI-style trade rationale

Every trade alert gets 3 human-readable bullet points explaining WHY
the system is taking the trade — exactly like SAHI Research alerts.

Sources:
  - Agreeing strategies → 1-2 technical reasons
  - Score modifiers (FII, VIX, OI, price structure) → 1 institutional reason
  - RSI context → bear/bull strength line
  - Pattern if detected → chart pattern line
"""
from __future__ import annotations
from typing import List, Optional


# ── Rationale templates per strategy ─────────────────────────────────────────

_STRATEGY_RATIONALE = {
    "BUY": {
        "trend":             "Stock in uptrend — holding above short-term EMA, buying on dips",
        "breakout":          "Breaking out above key resistance with volume confirmation",
        "ma_cross":          "Short-term EMA crossed above long-term EMA — bullish crossover confirmed",
        "supertrend_mtf":    "Supertrend bullish on multiple timeframes — aligned direction",
        "ichimoku":          "Price above Ichimoku cloud — Tenkan/Kijun bullish cross",
        "orb":               "Strong Opening Range Breakout — momentum from the open",
        "morning_momentum":  "Strong first-bar momentum — institutional buying at open",
        "vwap_reversion":    "Oversold pullback to VWAP — high-probability mean reversion",
        "rsi_divergence":    "Bullish RSI divergence — price at new low but RSI higher low",
        "gap_fill":          "Gap fill setup — support holding after gap open",
        "mean_reversion":    "RSI oversold at lower Bollinger Band — bounce setup",
        "price_structure":   "Holding above PDH key level — price structure intact",
        "order_block":       "Demand zone (order block) holding — institutional buyers active",
        "liquidity_sweep":   "Stop hunt below key low complete — smart money long entry",
        "smc":               "Change of Character (ChoCh) confirmed — structural reversal",
        "cpr":               "Trading above CPR — bullish bias for today's session",
        "pivot_boss":        "Bouncing from weekly pivot support with volume",
        "holy_grail":        "ADX pullback to EMA in strong uptrend — Larry Williams setup",
        "williams_r":        "Williams %%R recovering from oversold — momentum turning",
        "ttm_squeeze":       "TTM Squeeze momentum release — compressed range breaking up",
        "weinstein_stage":   "Stage 2 uptrend confirmed — breakout above base on volume",
        "failed_breakout":   "Failed breakdown reversed — bears trapped below support",
        "vwap_bands":        "Price at -2σ VWAP band — extreme oversold, high R:R",
        "heikin_ashi":       "Consecutive bullish Heikin Ashi candles — smooth uptrend",
        "canslim":           "Near 52-week high with expanding volume — institutional accumulation",
        "hour_orb":          "Hourly opening range breakout — intraday momentum continuation",
        "institutional_scalp": "Volume imbalance at VWAP — institutional direction confirmed",
        "market_structure":  "Break of Structure (BOS) higher — trend continuation signal",
        "vpoc_magnet":       "Price pulled towards Volume POC — magnet effect in play",
        "stat_arb":          "Statistical deviation from mean — high-probability reversion",
        "expiry_scalp":      "0DTE expiry momentum — put writers defending key level",
    },
    "SELL": {
        "trend":             "Stock in downtrend — rejection from short-term EMA on every bounce",
        "breakout":          "Breakdown below key support with volume — bearish confirmation",
        "ma_cross":          "Short-term EMA crossed below long-term EMA — bearish crossover",
        "supertrend_mtf":    "Supertrend bearish on multiple timeframes — selling rallies",
        "ichimoku":          "Price below Ichimoku cloud — Kijun line acting as resistance",
        "orb":               "Opening Range Breakdown — distribution from the open",
        "morning_momentum":  "Weak first-bar — institutional selling at open",
        "vwap_reversion":    "Overbought at VWAP resistance — mean reversion lower",
        "rsi_divergence":    "Bearish RSI divergence — price at new high, RSI lower high",
        "gap_fill":          "Resistance gap above acting as magnet — downside target",
        "mean_reversion":    "RSI overbought at upper Bollinger Band — fade the move",
        "price_structure":   "Rejection from PDH/PWH key resistance — structure broken",
        "order_block":       "Supply zone (order block) resistance — institutional sellers",
        "liquidity_sweep":   "Stop hunt above key high complete — institutional trap",
        "smc":               "Bearish Change of Character — market structure shift lower",
        "cpr":               "Trading below CPR — bearish bias for today's session",
        "pivot_boss":        "Rejection from weekly pivot resistance with volume",
        "holy_grail":        "ADX pullback to EMA in downtrend — Larry Williams short",
        "williams_r":        "Williams %%R at overbought — momentum exhaustion",
        "ttm_squeeze":       "TTM Squeeze fired bearish — momentum release to downside",
        "weinstein_stage":   "Stage 4 decline — breakdown below base, distribution phase",
        "failed_breakout":   "Failed breakout — bulls trapped above resistance, reversal",
        "vwap_bands":        "Price at +2σ VWAP band — extreme overbought, mean reversion",
        "heikin_ashi":       "Consecutive bearish Heikin Ashi candles — smooth downtrend",
        "hour_orb":          "Hourly opening range breakdown — intraday momentum lower",
        "institutional_scalp": "Volume selling at VWAP — institutional distribution",
        "market_structure":  "Break of Structure (BOS) lower — downtrend continuation",
        "vpoc_magnet":       "VPOC below acting as magnet — selling pressure dominant",
        "expiry_scalp":      "0DTE expiry momentum — call writers defending resistance",
    }
}

# ── Momentum/RSI context lines ────────────────────────────────────────────────

def _rsi_line(side: str, rsi: float = 0.0) -> str:
    if side == "BUY":
        if rsi and rsi < 30:
            return f"RSI deeply oversold ({rsi:.0f}) — bulls regaining control"
        elif rsi and rsi < 45:
            return f"RSI quoting at {rsi:.0f} — oversold bounce, bears losing grip"
        return "RSI recovering from oversold — momentum turning bullish"
    else:
        if rsi and rsi > 70:
            return f"RSI overbought ({rsi:.0f}) — bulls overextended, reversal due"
        elif rsi and rsi > 55:
            return f"RSI quoting around {rsi:.0f} — bears in control, further downside likely"
        return "RSI below 40 — bears in complete control of momentum"


# ── Institutional modifier lines ──────────────────────────────────────────────

def _institutional_line(
    side:       str,
    fii_bias:   str  = "",
    whale_mod:  float = 0.0,
    vix:        float = 0.0,
    oi_dir:     str  = "",
    pcr:        float = 0.0,
    sr_ctx:     str  = "",
) -> Optional[str]:
    lines = []

    if fii_bias:
        fb = fii_bias.lower()
        if side == "BUY" and "bull" in fb:
            lines.append("FII flows net bullish — institutional demand supporting")
        elif side == "SELL" and "bear" in fb:
            lines.append("FII flows net bearish — institutions distributing")

    if abs(whale_mod) >= 1.5:
        if whale_mod > 0:
            lines.append("Whale OI positions building on call side — smart money bullish")
        else:
            lines.append("Whale OI positions building on put side — smart money bearish")

    if oi_dir and oi_dir != "NEUTRAL":
        if side == "BUY" and oi_dir == "BULLISH":
            lines.append("Put writers active — OI data confirms support")
        elif side == "SELL" and oi_dir == "BEARISH":
            lines.append("Call writers dominant — OI data confirms resistance")

    if pcr:
        if side == "BUY" and pcr >= 1.2:
            lines.append(f"PCR at {pcr:.2f} — put writers defending, bullish OI structure")
        elif side == "SELL" and pcr <= 0.8:
            lines.append(f"PCR at {pcr:.2f} — call writers capping rally, bearish OI")

    if sr_ctx:
        lines.append(sr_ctx[:80])

    return lines[0] if lines else None


# ── Main function ─────────────────────────────────────────────────────────────

def build_rationale(
    symbol:             str,
    side:               str,        # "BUY" or "SELL"
    agreeing_strategies: List[str] = None,
    score:              float = 0.0,
    confluence:         str   = "SINGLE",
    rsi:                float = 0.0,
    fii_bias:           str   = "",
    whale_mod:          float = 0.0,
    oi_direction:       str   = "",
    pcr:                float = 0.0,
    sr_ctx:             str   = "",
    vix:                float = 0.0,
    regime:             str   = "",
    entry_price:        float = 0.0,
    stop_loss:          float = 0.0,
    target_price:       float = 0.0,
    **kwargs,
) -> List[str]:
    """
    Build 3 rationale bullet points for a trade.
    Returns list of strings — each is one bullet.
    """
    side_u    = side.upper()
    strategies = agreeing_strategies or []
    templates  = _STRATEGY_RATIONALE.get(side_u, {})
    bullets    = []

    # ── Bullet 1: Primary strategy rationale ─────────────────────────────────
    for strat in strategies[:3]:
        line = templates.get(strat, "")
        if line:
            bullets.append(line)
            break
    if not bullets and strategies:
        bullets.append(f"Multiple technical indicators aligned ({', '.join(strategies[:2])})")

    # ── Bullet 2: Secondary pattern or structure ──────────────────────────────
    added_pattern = False
    for strat in strategies[1:4]:
        if strat != strategies[0]:
            line = templates.get(strat, "")
            if line and line != bullets[0]:
                bullets.append(line)
                added_pattern = True
                break

    if not added_pattern:
        # Use regime context
        if regime == "STRONG_TREND":
            bullets.append("Strong trend regime — strategy weights favoring momentum plays")
        elif regime == "BREAKOUT":
            bullets.append("Breakout regime — volume expansion confirms directional move")
        elif regime == "HIGH_VOL":
            bullets.append("High volatility regime — wider stops, defined-risk setup")
        elif regime == "MEAN_REVERT":
            bullets.append("Mean reversion regime — fade the extreme, target VWAP")
        elif side_u == "SELL":
            bullets.append("Stock witnessing hurdle on every bounce — sellers in control")
        else:
            bullets.append("Stock seeing support on every dip — buyers absorbing supply")

    # ── Bullet 3: RSI + institutional ─────────────────────────────────────────
    inst_line = _institutional_line(side_u, fii_bias, whale_mod, vix, oi_direction, pcr, sr_ctx)
    if inst_line:
        bullets.append(inst_line)
    else:
        bullets.append(_rsi_line(side_u, rsi))

    return bullets[:3]


def format_sahi_alert(
    symbol:       str,
    side:         str,
    entry_low:    float,
    entry_high:   float,
    stop_loss:    float,
    target:       float,
    rationale:    List[str],
    paper:        bool   = True,
    trade_id:     str    = "",
    score:        float  = 0.0,
    confluence:   str    = "",
    exchange:     str    = "NSE",
    lot_size:     int    = 1,
) -> str:
    """
    Format trade alert in SAHI Research style.
    """
    from datetime import datetime
    side_u    = side.upper()
    action    = "BUY" if side_u == "BUY" else "SELL"
    mode_tag  = "PAPER" if paper else "🔴 LIVE"
    date_str  = datetime.now().strftime("%-d %b")
    conf_tag  = f"[{confluence}]" if confluence and confluence != "SINGLE" else ""
    score_tag = f"Score {score:.1f}" if score else ""

    entry_str = (f"₹{entry_low:,.2f}-{entry_high:,.2f}"
                 if entry_high and abs(entry_high - entry_low) > 0.1
                 else f"₹{entry_low:,.2f}")

    lines = [
        f"🚨 <b>TRADE ALERT : {date_str}</b> 🚨",
        f"",
        f"<b>{action} {symbol} @ {entry_str}</b>",
        f"SL {stop_loss:,.2f}  |  TGT {target:,.2f}",
        f"",
        f"<b>Rationale:</b>",
    ]

    for i, point in enumerate(rationale[:3], 1):
        lines.append(f"  {'•' if i > 0 else '★'}  {point}")

    lines += [
        f"",
        f"  📐 R:R {abs(target-entry_low)/max(abs(entry_low-stop_loss),1):.1f}x  "
        f"{conf_tag}  {score_tag}  [{mode_tag}]",
    ]
    if trade_id:
        lines.append(f"  🔖 {trade_id}")

    return "\n".join(lines)
