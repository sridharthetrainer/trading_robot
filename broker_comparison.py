"""
broker_comparison.py — Angel One vs Zerodha decision framework

VERDICT: STAY WITH ANGEL ONE as primary. Add Zerodha ONLY when capital > ₹2L.

DETAILED COMPARISON:
=====================

ANGEL ONE (FREE):
  ✅ SmartAPI: completely free, no monthly fee
  ✅ TOTP auto-login: fully autonomous, zero daily action
  ✅ 5-minute historical candles: free, unlimited calls
  ✅ WebSocket live feed: free with Angel account
  ✅ Option chain: free via SmartAPI
  ✅ Order placement: free
  ✅ Balance & portfolio: free
  ❌ Data quality: occasional gaps, ~150ms latency
  ❌ Historical depth: ~500 days only
  ❌ Tick data: not available
  ❌ Market depth (L2): 5 levels only
  COST: ₹0/month
  AUTONOMY: 100% — TOTP auto-refresh, zero manual steps

ZERODHA KITE CONNECT (PAID):
  ✅ Data quality: best-in-class (15% of NSE volume)
  ✅ Tick data: every trade, not just candles  
  ✅ Historical depth: 2000+ days
  ✅ Latency: ~50ms (3x faster than Angel)
  ✅ Market depth: 20 levels bid/ask
  ✅ WebSocket: bid/ask + OI + volume in one stream
  ✅ Cleaner API: fewer rate limit issues
  ❌ Cost: ₹2,000/month just for API access
  ❌ Daily token: requires daily browser login OR Playwright automation
  ❌ Token automation: requires Playwright (additional complexity)
  ❌ Not autonomous by default: needs extra work to automate
  COST: ₹2,000/month = ₹24,000/year
  AUTONOMY: 85% with Playwright, 0% without it

WHEN TO SWITCH:
  Capital < ₹1L:  Stay Angel One. API fee = 20-30% of capital/year. Not worth it.
  Capital ₹1-2L:  Stay Angel One. Fee = 10-20% of capital. Marginal.
  Capital ₹2-5L:  Consider Zerodha. Fee = 4-10%. Data quality improvement justifiable.
  Capital > ₹5L:  Add Zerodha. Fee = <5%. Tick data + latency advantage meaningful.
  Capital > ₹10L: Both. Angel as fallback, Zerodha as primary.

RECOMMENDATION FOR YOUR SYSTEM:
  Current capital ₹30,000 → STAY WITH ANGEL ONE
  Angel One is free and with TOTP auto-refresh is 100% autonomous.
  Zerodha's ₹2,000/month = 6.6% of your capital — too expensive.
  
  The gap is NOT data quality for signals (both give same 5m candles).
  The gap is tick data and 20-level depth — useful at ₹10L+, not ₹30K.

DATA-ONLY HYBRID (BEST OF BOTH WORLDS, FREE):
  Angel One for TRADING (orders, live PnL)  — free
  Angel One for DATA (5m candles) — free  
  Dhan for DATA BACKUP (free with demat)  — free
  NSE direct for INDICES (always works) — free
  Stooq for GLOBAL (no limit) — free
  = Zero cost, 4 independent data sources, fully autonomous
"""
from __future__ import annotations

RECOMMENDATION = "ANGEL_ONE_PRIMARY"
SWITCH_THRESHOLD_INR = 200000  # ₹2L capital


def should_use_zerodha(current_capital: float) -> dict:
    """Returns decision and reasoning based on current capital."""
    if current_capital < 100000:
        return {
            "use_zerodha": False,
            "reason": f"Capital ₹{current_capital:,.0f} < ₹1L threshold",
            "api_cost_pct": round(24000/current_capital*100, 1),
            "recommendation": "Stay with Angel One free API",
        }
    elif current_capital < 200000:
        return {
            "use_zerodha": False,
            "reason": f"Capital ₹{current_capital:,.0f} borderline",
            "api_cost_pct": round(24000/current_capital*100, 1),
            "recommendation": "Stay Angel One. Focus on strategy improvement first.",
        }
    else:
        return {
            "use_zerodha": True,
            "reason": f"Capital ₹{current_capital:,.0f} justifies ₹2K/month",
            "api_cost_pct": round(24000/current_capital*100, 1),
            "recommendation": "Add Zerodha for tick data + depth. Keep Angel as fallback.",
        }
