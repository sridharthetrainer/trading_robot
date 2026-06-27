#!/usr/bin/env python3
"""
diag_option_strike_selection.py

Trace WHY a specific strike was selected over alternatives for the same symbol.

Shows the option selection logic:
  1. Given signal (BUY_CALL / BUY_PUT), what are the candidate strikes?
  2. What ATM/OTM logic is applied?
  3. Why was THIS strike chosen?

This is the detailed "option chain intelligence" layer that feeds OptionChainEngine.

Usage:
    python diag_option_strike_selection.py --symbol NIFTY --date 2026-06-17
"""


def show_option_selection_logic():
    """Display option strike selection rules."""
    
    output = []
    output.append("\n" + "="*140)
    output.append("OPTION STRIKE SELECTION LOGIC (OptionChainEngine.select_option)")
    output.append("="*140 + "\n")
    
    output.append("""
The option bot selects a strike through this flow:

┌─────────────────────────────────────────────────────────────────────────────┐
│ INPUT: Signal (direction, confidence, trade_capital, signal_side)           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. DECIDE OPTION TYPE (CE vs PE)                                            │
│    Rule: Momentum override OR signal alignment                              │
│                                                                             │
│    if momentum_fast_move() → use OPPOSITE of signal (contrarian reversal)  │
│    else if signal_side == BUY_CALL → CE                                     │
│         elif signal_side == BUY_PUT → PE                                    │
│         else → use price_action (break above/below PDH/PDL)                │
│                                                                             │
│    Examples:                                                                │
│      • BUY signal + fast 5% up move → SELECT PE (fade the move)             │
│      • BUY signal + normal momentum → SELECT CE (follow the trend)          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. SELECT STRIKE (ATM vs OTM)                                               │
│    Rule: Confidence-based moneyness                                         │
│                                                                             │
│    Current Spot = NIFTY close (real-time)                                   │
│    ATM Strike = Round spot to nearest 100                                   │
│                                                                             │
│    if confidence >= 0.85 → ATM Strike (high conviction)                    │
│    elif confidence >= 0.70 → 1 OTM (medium conviction)                     │
│    else → 2 OTM (low confidence, protect with width)                        │
│                                                                             │
│    For BUY_CALL (bullish):                                                 │
│      ATM = NIFTY @ 23500 → 23500 CE (1:1 with index)                       │
│      1 OTM = 23600 CE (100 points OTM)                                      │
│      2 OTM = 23700 CE (200 points OTM)                                      │
│                                                                             │
│    For BUY_PUT (bearish):                                                  │
│      ATM = NIFTY @ 23500 → 23500 PE                                         │
│      1 OTM = 23400 PE (100 points OTM)                                      │
│      2 OTM = 23300 PE (200 points OTM)                                      │
│                                                                             │
│    Examples:                                                                │
│      • High confidence (0.90) BUY_CALL → 23500 CE (ATM)                    │
│      • Medium confidence (0.75) BUY_CALL → 23600 CE (1 OTM, safer)         │
│      • Low confidence (0.60) BUY_PUT → 23300 PE (2 OTM, maximum width)    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. SELECT EXPIRY (trade style determines DTE)                               │
│    Rule: Style determines holding period → expiry selection                 │
│                                                                             │
│    SCALPING (0-DTE):                                                        │
│      → Today's expiry (expires at 3:30 PM)                                  │
│      → Tight 8% stop, 1.6:1 RR, 0-5 min hold                               │
│      → Use PIVOT_SCALPING_OPTION_STOP_0DTE config                          │
│      → Why: High theta decay, tight range, low absolute premium             │
│                                                                             │
│    INTRADAY (1+ DTE):                                                       │
│      → Weekly expiry (Thu or other weekly)                                  │
│      → 15% stop, 1.2:1 RR, 15min-60min hold                                │
│      → Standard intraday risk params                                        │
│      → Why: Balance between liquidity and theta decay                       │
│                                                                             │
│    SWING (5+ DTE):                                                          │
│      → Monthly or far-month expiry                                          │
│      → 20% stop, 1:1 RR, multi-hour/day hold                               │
│      → Lower relative theta, more time value                                │
│      → Why: Hold through structural moves                                   │
│                                                                             │
│    Default: Today's expiry if ≤2 DTE remaining                             │
│             Else: Nearest weekly                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. LOT SIZE & QUANTITY                                                      │
│    Rule: Capital, margin, max position sizing                               │
│                                                                             │
│    Available Capital = Angel API rmsLimit() call (LIVE)                     │
│                       fallback to REAL_CAPITAL from .env                    │
│                                                                             │
│    Lot Size = Angel contract multiplier (default 75 for NIFTY)             │
│    Premium per lot = strike price × 75 × lot count                         │
│                                                                             │
│    Max Lots = min(confidence_lots, position_sizer.max_lots,                │
│                   max(1, available_capital / margin_per_lot))               │
│                                                                             │
│    Examples (Capital = ₹1,00,000):                                         │
│      • NIFTY 23500 CE @ ₹45 × 75 = ₹3,375 per lot (margin ~₹15k)         │
│        → Can do 6 lots max (6 × ₹15k = ₹90k used)                          │
│      • BANKNIFTY 50000 CE @ ₹120 × 40 = ₹4,800 per lot (margin ~₹25k)    │
│        → Can do 3-4 lots max                                                │
│                                                                             │
│    pivot_scalping: max 2 lots (PIVOT_SCALPING_MAX_LOTS config)             │
│    other_strategies: max 4 lots                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ OUTPUT: OptionContract                                                      │
│  {                                                                           │
│    underlying: "NIFTY",                                                     │
│    option_type: "CE",                      ← From step 1                    │
│    strike: 23500,                          ← From step 2                    │
│    expiry_date: date(2026, 6, 17),         ← From step 3                    │
│    expiry_str: "2026-06-17",                                                │
│    symbol: "NIFTY23500CE",     ← Angel-tradeable symbol                     │
│    lot_size: 75,               ← From step 4                                │
│    lots: 2,                    ← From step 4                                │
│    quantity: 150,              ← 2 × 75                                     │
│    premium: 45.50,             ← Fetched from Angel real-time              │
│    spot_price: 23500,          ← Current NIFTY price                       │
│    dte: 0,                     ← Days to expiry                             │
│    style: "scalping",          ← From step 3                                │
│    signal_side: "BUY",         ← From input signal                          │
│    option_side: "BUY",         ← Same as signal                             │
│    strike_type: "ATM",         ← From step 2                                │
│    capital_required: 6825,     ← 150 × 45.50                                │
│    momentum_override: False    ← From step 1                                │
│  }                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
                        EXECUTE: Send to broker
                             (Angel API)
""")
    
    output.append("\n" + "="*140)
    output.append("KEY INSIGHTS FOR DIAGNOSING MULTIPLE STRIKES\n")
    output.append("="*140 + "\n")
    
    insights = [
        ("Different symbols", "NIFTY, BANKNIFTY, FINNIFTY can trade simultaneously. "
                              "Each gets ONE signal/strike at a time. If you see "
                              "3 concurrent positions, likely: NIFTY + BANKNIFTY + FINNIFTY."),
        
        ("Same symbol, different strikes", "Typically happens if:\n"
                                           "  - Signal regenerated due to data refresh (unlikely)\n"
                                           "  - Manual re-entry after SL hit (PIVOT_SCALPING_OPTION_STOP_0DTE)\n"
                                           "  - Confidence changed (e.g., 0.85→0.70, moved ATM→1OTM)\n"
                                           "  - Time decay (0-DTE expired, rolled to next expiry)"),
        
        ("Multiple fills at same strike", "Angel can split order into multiple fills if:\n"
                                          "  - Requested quantity > available market liquidity\n"
                                          "  - Broker splits 150 contracts into 50+50+50 fill parcels\n"
                                          "  - Each fill is a separate execution (same strike, different time)"),
        
        ("Why specific strike selected", "Use these queries to trace:\n"
                                        "  1. Query signal_log.db → confirming_strategies (who voted)\n"
                                        "  2. Check live logs → OptionChainEngine.select_option() output\n"
                                        "  3. Review config → confidence thresholds, max lots"),
        
        ("Debugging tip", "Enable verbose logging:\n"
                         "  - logging.getLogger('live_signal_engine').setLevel(DEBUG)\n"
                         "  - logging.getLogger('option_chain_engine').setLevel(DEBUG)\n"
                         "  - Each strike selection will log: strike reason, confidence, expiry logic"),
    ]
    
    for i, (title, content) in enumerate(insights, 1):
        output.append(f"{i}. {title}:")
        output.append(f"   {content.replace(chr(10), chr(10) + '   ')}\n")
    
    output.append("\n" + "="*140 + "\n")
    
    return "\n".join(output)

def main():
    print(show_option_selection_logic())
    print("\nTo audit executed strikes:")
    print("  python diag_option_strike_audit.py --today --symbol NIFTY")
    print("\nTo trace why candidates passed/failed gates:")
    print("  python diag_signal_generation_flow.py --symbol NIFTY")

if __name__ == "__main__":
    main()
