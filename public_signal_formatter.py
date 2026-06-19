from __future__ import annotations
from ux_engine import get_lot_info  # UX-5 lot sizing
"""
public_signal_formatter.py — Universal Signal Format

All signals are formatted WITHOUT personal fund details.
Every subscriber manages their own capital.
Signal tells WHAT to trade and WHY — not how much.

SEBI IA Rule 2013 compliant:
  - No portfolio management
  - No guaranteed returns
  - Educational signals only
  - Subscriber manages own execution

Format principles (from Zerodha Streak + TradingView):
  - Clear entry/exit levels
  - Explicit risk-reward
  - Time horizon stated
  - Reason in plain English
  - No P&L promises
"""
from datetime import datetime
from typing import Dict, Optional


def format_public_signal(signal: dict, tier: str = "free") -> str:
    """
    Universal signal card — works for any subscriber's capital size.
    No mention of lot sizes, fund size, or personal portfolio.
    Subscriber calculates their own position size.
    """
    symbol    = signal.get("symbol", "?")
    direction = signal.get("direction", "?")
    price     = float(signal.get("price", 0) or 0)
    target    = float(signal.get("target", 0) or 0)
    sl        = float(signal.get("stop_loss", 0) or 0)
    score     = float(signal.get("score", 0) or 0)
    strategy  = signal.get("strategy", "Confluence")
    regime    = signal.get("regime", "TRENDING")
    horizon   = signal.get("horizon", "Intraday").upper()
    vix       = float(signal.get("vix", 0) or 0)
    reasons   = signal.get("reasons", [])
    now       = datetime.now().strftime("%d-%b %H:%M")

    icon    = "🟢" if direction == "BUY" else "🔴"
    conf    = "HIGH CONVICTION" if score >= 7.5 else \
              "MEDIUM" if score >= 6.0 else "SPECULATIVE"
    stars   = "⭐⭐⭐" if score >= 8 else "⭐⭐" if score >= 6.5 else "⭐"

    # Risk/Reward
    rr = 0.0
    if price and sl and target and sl != price:
        rr = abs((target - price) / (price - sl))

    # Percentage move
    target_pct = abs((target - price) / price * 100) if price else 0
    sl_pct     = abs((price - sl) / price * 100) if price else 0

    lines = [
        f"{'─'*38}",
        f"{icon} <b>{direction} SIGNAL — {symbol}</b> {stars}",
        f"{'─'*38}",
        f"  📅 {now}  |  {horizon}",
        f"  🎯 Conviction: {conf}  ({score:.1f}/10)",
        f"",
        f"  💰 Entry:      ₹{price:,.2f}",
        f"  🎯 Target:     ₹{target:,.2f}  (+{target_pct:.1f}%)",
        f"  🛡️ Stop Loss:  ₹{sl:,.2f}  (-{sl_pct:.1f}%)",
    ]

    if rr > 0:
        lines.append(f"  📐 Risk:Reward: 1:{rr:.1f}")

    lines += [
        f"",
        f"  📊 Strategy: {strategy}",
        f"  🌊 Regime:   {regime}",
    ]

    if vix:
        lines.append(f"  📈 India VIX: {vix:.1f}")

    # Position sizing guidance (percentage-based, works for any capital)
    if rr >= 2.0:
        risk_pct = "1.5% of capital"
    elif rr >= 1.5:
        risk_pct = "1.0% of capital"
    else:
        risk_pct = "0.5% of capital"

    lines += [
        f"",
        f"  💡 <b>POSITION SIZING</b>",
        f"  Risk max {risk_pct} on this trade",
        f"  (Adjust for your account size)",
    ]

    # Show reasons for premium tier
    if tier == "premium" and reasons:
        lines += ["", "  <b>📋 SIGNAL REASONS</b>"]
        for r in reasons[:4]:
            lines.append(f"   • {r}")

    lines += [
        f"",
        f"  ⚠️ Educational only | Not SEBI registered advice",
        f"  Set your own stop loss before entry",
        f"{'─'*38}",
    ]
    return "\n".join(lines)


def format_public_morning_brief(data: dict) -> str:
    """
    Morning brief for public channel — market context only.
    No personal fund data. Works for every subscriber.
    """
    now = datetime.now().strftime("%A, %d %b %Y  %H:%M")

    def _gfmt(d, label, decimals=0):
        if not d or not d.get("price"):
            return f"  {label:14} N/A"
        px  = float(d["price"])
        chg = float(d.get("chg", 0))
        icon = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        return f"  {label:14} {px:>10,.{decimals}f}  {icon}{chg:+.2f}%"

    global_data = data.get("global", {})
    vix         = float(data.get("india_vix", 0))
    bias        = float(data.get("bias", 0))
    levels      = data.get("levels", {})
    top_sectors = data.get("top_sectors", [])
    avoid       = data.get("avoid_sectors", [])
    fii_note    = data.get("fii_note", "FII data updating...")

    bias_str  = "🟢 BULLISH" if bias > 0.3 else "🔴 BEARISH" if bias < -0.3 else "⚪ NEUTRAL"
    vix_warn  = "" if vix < 15 else " ⚠️ Elevated" if vix < 22 else " 🚨 HIGH"

    lines = [
        f"🌅 <b>MARKET BRIEF</b>  |  {now}",
        f"{'─'*38}",
        "",
        "  <b>🌍 GLOBAL</b>",
        _gfmt(global_data.get("SP500"),   "S&P 500",    0),
        _gfmt(global_data.get("NASDAQ"),  "NASDAQ",     0),
        _gfmt(global_data.get("NIKKEI"),  "Nikkei",     0),
        _gfmt(global_data.get("DXY"),     "Dollar(DXY)",2),
        _gfmt(global_data.get("GOLD"),    "Gold",       0),
        _gfmt(global_data.get("BRENT"),   "Brent Oil",  1),
        _gfmt(global_data.get("USDINR"),  "USD/INR",    2),
        "",
        f"  <b>🇮🇳 INDIA</b>",
        f"  India VIX:     {vix:.1f}{vix_warn}",
        f"  Market Bias:   {bias_str}",
    ]

    if levels:
        lines += [
            "",
            f"  <b>📐 NIFTY LEVELS</b>",
            f"  R2: {levels.get('R2',0):,.0f}  R1: {levels.get('R1',0):,.0f}",
            f"  PP: {levels.get('PP',0):,.0f}",
            f"  S1: {levels.get('S1',0):,.0f}  S2: {levels.get('S2',0):,.0f}",
        ]

    if top_sectors:
        lines += [
            "",
            f"  <b>🔄 SECTORS</b>",
            f"  Strong:  {', '.join(top_sectors[:3])}",
            f"  Weak:    {', '.join(avoid[:2]) if avoid else 'None'}",
        ]

    lines += [
        "",
        f"  <b>💰 FII FLOW</b>",
        f"  {fii_note}",
        "",
        f"  📡 Signals start 9:15 AM",
        f"  Max {8} signals today (quality filtered)",
        f"{'─'*38}",
        f"  ⚠️ For education only | Manage your own risk",
    ]
    return "\n".join(lines)


def format_eod_summary(stats: dict) -> str:
    """
    End-of-day public summary — no personal fund data.
    Shows signal performance only.
    """
    now = datetime.now().strftime("%d-%b-%Y")
    n_signals  = stats.get("signals_sent", 0)
    n_hit_tgt  = stats.get("targets_hit", 0)
    n_hit_sl   = stats.get("sl_hit", 0)
    avg_score  = stats.get("avg_score", 0)
    best_move  = stats.get("best_move", "N/A")

    hit_rate = n_hit_tgt / n_signals * 100 if n_signals else 0

    return (
        f"📊 <b>EOD SIGNAL SUMMARY</b>  |  {now}\n"
        f"{'─'*38}\n"
        f"  Signals today:  {n_signals}\n"
        f"  Targets hit:    {n_hit_tgt}  ({hit_rate:.0f}%)\n"
        f"  SL triggered:   {n_hit_sl}\n"
        f"  Avg score:      {avg_score:.1f}/10\n"
        f"  Best move:      {best_move}\n"
        f"{'─'*38}\n"
        f"  📡 Tomorrow's brief at 8:30 AM\n"
        f"  ⚠️ Past performance ≠ future results"
    )
