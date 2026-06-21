#!/usr/bin/env python3
"""
ARCHITECTURE.md — Two-System Overview

================================
System 1: AUTONOMOUS TRADING SYSTEM (Main)
================================

Entry: main_autonomous.py
└─→ LiveSignalEngine._run_cycle()
    ├─→ Scan all symbols (NIFTY, BANKNIFTY, FINNIFTY, ...)
    ├─→ For each symbol: generate_signal() from signal_engine.py
    │   └─→ Run 57 strategies, vote on direction/score
    │   └─→ Apply confluence gate: N strategies agree
    │   └─→ Return: signal = {symbol, side, strategy, score, ...}
    │
    ├─→ Evaluate candidate: _evaluate_symbol_candidate()
    │   └─→ Check risk gates (position limits, daily loss, correlation)
    │   └─→ Decide: EXECUTE or REJECT
    │
    └─→ Execute execution plan:
        ├─→ Equity path: direct stock/index trade (BUY 100 shares)
        └─→ Option path: ← CALLS THE OPTION SYSTEM
            └─→ OptionChainEngine.select_option()
                └─→ This is where strikes are chosen


================================
System 2: NIFTY OPTIONS SYSTEM (Specialized)
================================

ENTRY POINT: OptionChainEngine.select_option()
  - Called from: live_signal_engine._build_execution_plan() [line 2917]
  - Input: signal (BUY/SELL), confidence, trade_capital, df, option_chain_signal
  - Output: OptionContract {strike, option_type, expiry, dte, style, premium, ...}

WORKFLOW:
  1. _decide_option_type(signal_side, option_chain_signal, df)
     └─→ CE or PE? (momentum override vs signal alignment)
  
  2. _select_strike(spot_price, confidence, signal_side)
     └─→ ATM or OTM? (confidence-based moneyness)
  
  3. _select_expiry(symbol, style, dte_candidate)
     └─→ Today (0-DTE) or weekly or monthly?
  
  4. _get_option_data(symbol, strike, option_type, expiry_date)
     └─→ Fetch real-time premium, Greeks, liquidity
  
  5. Construct OptionContract
     └─→ Return to _build_execution_plan()

EXECUTION:
  - live_signal_engine._build_execution_plan() line ~2960+
  - Broker: Angel API.place_order(symbol="NIFTY23500CE", qty=150, side="BUY", ...)
  - Persistence: signal_log.db + trade_manager


================================
KEY INTEGRATION POINTS
================================

1. Signal Flow:
   Autonomous → generate_signal(NIFTY, pivot_scalping, ...)
              → score=78.5, direction=BUY, confidence=0.78
              → _evaluate_symbol_candidate()
              → _build_execution_plan(signal, option_signal=BUY_CALL, ...)
              → OptionChainEngine.select_option(...)  ← OPTIONS SYSTEM HERE
              → Returns OptionContract(strike=23500, expiry=today, ...)

2. Configuration:
   Autonomous:
     - MIN_CONFLUENCE_SCORE = 2.0 (# strategies that must agree)
     - POST_CONFLUENCE_MIN_SCORE = 3.5 (min score after confluence check)
     - STRATEGY_WEIGHTS (which strategies are trusted more)
   
   Options:
     - PIVOT_SCALPING_OPTION_STOP_0DTE = 0.08 (8% stop on 0-DTE)
     - PIVOT_SCALPING_OPTION_TARGET_RR = 1.6 (risk:reward ratio)
     - PIVOT_SCALPING_MAX_LOTS = 2 (max concurrent positions)
     - PIVOT_SCALPING_CAPITAL = 20000 (dedicated bucket)

3. Logging:
   - signal_log.db: ALL candidates (executed + rejected)
     Columns: symbol, strategy, score, confluence, option_strike, option_dte, ...
   - trade_manager: Executed trades only
   - Performance metrics: win_rate, avg_rrr, drawdown per strategy


================================
WHERE EACH STRIKE COMES FROM
================================

System 1 Decision:
  "Should NIFTY be traded at all today?"
  → Confluence gate: 3+ strategies agree
  → Risk gate: available capital? position limits? correlation?
  → Result: YES → signal passes → send to Option System

System 2 Decision:
  "If NIFTY should trade, which STRIKE?"
  → Confidence: 0.78 (from pivot_scalping signal)
  → Option Type: CE or PE? (from momentum + signal direction)
  → Moneyness: ATM (conf >= 0.85) vs 1OTM (conf >= 0.70) vs 2OTM (conf < 0.70)
  → Expiry: Today (0-DTE scalping) vs Weekly (intraday) vs Monthly (swing)
  → Lots: max 2 for pivot_scalping (PIVOT_SCALPING_MAX_LOTS config)
  → Result: "Trade 2 lots of NIFTY23500CE today at current market"

Why MULTIPLE STRIKES FOR SAME SYMBOL:
  1. Different timestamps → Signal regenerated at different spot prices
  2. Confidence changed → 0.90 (ATM) vs 0.70 (1OTM)
  3. Manual re-entry → After SL hit, human re-enters
  4. Partial fills → 150 qty split into 50+50+50 fills (same strike, different times)


================================
DIAGNOSTICS BY SYSTEM
================================

FOR AUTONOMOUS SYSTEM (Why signals generated):
  → python diag_signal_generation_flow.py --symbol NIFTY
    Shows: Which strategies voted, why some rejected, confluence reasoning

  → python diag.py or diag_scan.py
    Shows: Real-time signal status, gating reasons, active positions

FOR OPTIONS SYSTEM (Why this strike):
  → python diag_option_strike_selection.py
    Shows: Decision logic (confidence → moneyness, expiry selection)

  → python diag_option_strike_audit.py --symbol NIFTY --today
    Shows: Executed strikes, which strategies backed them, premium paid

  → sqlite3 signal_log.db "SELECT symbol, strategy, score, confluence, \
                                   option_strike, option_dte, option_premium \
                            FROM signal_log WHERE executed=1 AND symbol='NIFTY' \
                            ORDER BY signal_date DESC LIMIT 20;"
    Raw: All executed NIFTY option trades with full metadata


================================
TYPICAL SESSION
================================

09:15:00  Autonomous System starts scanning
           → Scans NIFTY, BANKNIFTY, FINNIFTY, ...
           → Generates 100+ candidate signals (most rejected)
           → 5-10 pass all gates (high confluence + risk OK)

09:15:15  Option System processes passing signals
           → Takes BUY signal for NIFTY
           → Confidence = 0.75 → Select 1OTM (23600 CE, not ATM 23500)
           → Style = scalping → Select 0-DTE (today's expiry)
           → Calls Angel API: Place order for 2 lots of NIFTY23600CE
           → Filled at 44.50

09:15:20  Signal logged to signal_log.db
           → option_strike=23600, option_type=CE, option_dte=0, 
             option_style=scalping, option_premium=44.50

09:20:00  Market conditions change
           → New scan: NIFTY BUY signal again (different strategy combo)
           → Confidence = 0.90 (higher!) → Select ATM (23500 CE)
           → Already have 23600 @ 2 lots → New position? 
             NO: Correlation gate blocks (already NIFTY CE)
             → Signal rejected at risk gate

09:45:00  First trade hit target
           → 23600 CE closed at 68.25 (bought 44.50, +24 points)
           → Trade marked executed=1 in signal_log.db


===================================
ANSWERING "WHY MULTIPLE STRIKES"
===================================

If you see:
  NIFTY 23500 CE (2 lots)
  NIFTY 23600 CE (2 lots)
  NIFTY 23700 CE (1 lot)
  
Likely scenarios:
  1. Different signals at different times
     - 09:15 pivot_scalping (conf=0.75) → 1OTM = 23600 CE
     - 09:20 ma_cross (conf=0.90) → ATM = 23500 CE (but rejected by correlation gate)
     - 09:25 rsi2 (conf=0.65) → 2OTM = 23700 CE
  
  2. Manual intervention
     - Human saw correlation gate block the 23500 trade
     - Manually overrode and placed 23500 anyway
     - Then system also placed 23700 via normal flow
  
  3. Partial fills (MOST COMMON)
     - Placed order for 2 lots (150 contracts) of 23500 CE
     - Angel filled: 50 @ 09:15, 50 @ 09:16, 50 @ 09:17
     - signal_log shows 1 row (same strike, same signal)
     - trade_manager shows 3 fills (different timestamps)
     
To distinguish: 
  - Query signal_log.db: 1 row per signal (strike=23500 for all 3 fills)
  - Query trade_manager: 3+ fills (different execution_id, same strike)

===================================
"""
