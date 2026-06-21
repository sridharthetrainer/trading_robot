#!/usr/bin/env python3
"""
QUICK_REFERENCE.md — Two Systems Explained

==============================================================================
SYSTEM 1: AUTONOMOUS TRADING (General Signal Generation)
==============================================================================

What it does:
  • Scans 50+ symbols (NIFTY, BANKNIFTY, FINNIFTY, individual stocks, etc.)
  • Runs 57 trading strategies on each
  • Votes on DIRECTION (BUY/SELL) and SCORE
  • Filters via gates: confluence, regime, risk limits
  • DECISION: Execute or reject each candidate

Entry: main_autonomous.py
Flow:  Scan → Signal → Gate → Risk Check → Execute

Decision Output: 
  {
    "symbol": "NIFTY",
    "side": "BUY",
    "strategy": "pivot_scalping",
    "score": 78.5,
    "confluence": "HIGH",  ← Do 3+ strategies agree?
    "direction": "BUY",
    "regime": "UPTREND",
    "entry_price": 23500
  }

Key Gates:
  1. Confluence (2+ strategies agree)          ← PASS → Move to next
  2. Score threshold (>3.5)                    ← FAIL → Reject
  3. Risk limits (capital, positions, correlation)
  4. Regime alignment (market mode OK?)
  5. Execution (place order)


==============================================================================
SYSTEM 2: NIFTY OPTIONS (Strike Selection & Option-Specific Logic)
==============================================================================

What it does:
  • Takes BUY/SELL decisions from System 1
  • Converts to SPECIFIC STRIKES (e.g., "23500 CE" not just "BUY")
  • Applies option-specific gates: liquidity, Greeks, DTE rules
  • Manages 0-DTE scalping (tight 8% stops) differently from intraday/swing

Entry: OptionChainEngine.select_option() 
Called from: live_signal_engine._build_execution_plan() [line 2917]

Decision Process:
  1. Option Type:    CE or PE? (momentum override vs signal)
  2. Moneyness:      ATM or 1OTM or 2OTM? (based on confidence)
  3. Expiry:         0-DTE (today) vs 1-week vs 1-month? (based on style)
  4. Lot Size:       1-4 lots (based on capital + margin)

Decision Output:
  {
    "underlying": "NIFTY",
    "option_type": "CE",          ← Step 1: Confidence-based
    "strike": 23500,              ← Step 2: Confidence-based
    "expiry": "2026-06-17",       ← Step 3: Style-based (0-DTE)
    "dte": 0,
    "style": "scalping",
    "lots": 2,                    ← Step 4: Capital-based
    "premium": 45.50,             ← Real-time from Angel API
    "symbol": "NIFTY23500CE"      ← Angel-tradeable contract
  }


==============================================================================
HOW THEY CONNECT
==============================================================================

Timeline:

  09:15:00  System 1 scans all symbols
  ├─ Generates signals for 100+ candidates
  ├─ Filters through gates (confluence, score, risk)
  └─ 5-10 signals PASS all gates

  09:15:10  System 1: "NIFTY BUY signal passed!"
  └─ Sends to System 2: symbol=NIFTY, direction=BUY, confidence=0.78

  09:15:11  System 2: "Which strike for NIFTY?"
  ├─ Check confidence: 0.78 → 1OTM (not ATM)
  ├─ Check style: scalping → 0-DTE (today's expiry)
  ├─ Check capital: Can do 2 lots max
  └─ Output: Buy 2 lots of NIFTY23600CE (1OTM, 0-DTE)

  09:15:12  System 2 places order with Angel
  └─ Result: Filled 2 lots @ ₹44.50

  09:15:13  Both systems log the trade
  ├─ System 1 log: signal_log.db (with signal generation data)
  └─ System 2 log: trade_manager (with option-specific data)

  09:45:00  Trade hits target
  ├─ System 2: Manage stops + target for this specific strike
  └─ System 1: Mark signal as executed in signal_log.db


==============================================================================
WHY MULTIPLE STRIKES FOR SAME SYMBOL
==============================================================================

Scenario: You see NIFTY 23500 CE, NIFTY 23600 CE, NIFTY 23700 CE

Possible Reasons:

1. DIFFERENT SIGNALS AT DIFFERENT TIMES
   09:15  pivot_scalping signal (conf=0.75) → 1OTM = 23600 CE ✓ Executed
   09:20  ma_cross signal (conf=0.90) → ATM = 23500 CE ✗ Blocked (correlation gate)
   09:25  rsi2 signal (conf=0.65) → 2OTM = 23700 CE ✓ Executed

2. PARTIAL FILLS (MOST COMMON IN LIVE TRADING)
   Order: "Buy 2 lots of NIFTY23600CE"
   Fills: 09:15 50 contracts @ 44.50
          09:16 50 contracts @ 44.75
          09:17 50 contracts @ 45.00
   
   signal_log.db: 1 row (same strike = 23600, one signal generated)
   Angel Blotter: 3 separate fills (same strike, different times)

3. MANUAL INTERVENTION
   System rejected a signal → User manually placed it anyway
   Then System generated another signal normally
   Result: Both appear in logs (different entry times, reasons)

4. ROLLING / RE-ENTRY
   Initial: 23500 CE (0-DTE, expires today)
   Time: 15:00 (2.5 hrs before expiry, theta decay extreme)
   System: "0-DTE too risky now, exit and roll to next week"
   Result: Exit 23500 CE, enter 23600 CE (1-week expiry)


==============================================================================
DIAGNOSTIC COMMANDS (By Question)
==============================================================================

"Why did NIFTY get a signal?"
  → python diag_signal_generation_flow.py --symbol NIFTY
     Shows: Which strategies voted, confluence reasoning, why rejected ones failed

"Why this particular strike?"
  → python diag_options_system.py --explain 23500
     Shows: Confidence → moneyness logic (ATM vs OTM)

"What strikes were actually executed today?"
  → python diag_options_system.py --strikes NIFTY
     Shows: All NIFTY option trades with timing and details

"System configuration (stops, targets, lot sizing)?"
  → python diag_options_system.py --config
     Shows: All parameters for both systems

"Full details of a specific trade?"
  → sqlite3 signal_log.db "SELECT * FROM signal_log WHERE \
                                  symbol='NIFTY' AND option_strike=23500 \
                                  AND signal_date='2026-06-17'"
     Raw SQL: 40+ columns including all signal metadata


==============================================================================
KEY DIFFERENCES
==============================================================================

                    System 1 (Autonomous)      System 2 (Options)
───────────────────────────────────────────────────────────────────────────
Input               Market data (OHLCV)        Signal from System 1
Decision            BUY/SELL + score           Which strike + DTE
Scope               All symbols                NIFTY/BANKNIFTY only
Timeline            Real-time scan (1-5 min)   ~1 sec after signal
Exit criteria       Time or stop hit           DTE-aware (8% for 0-DTE)
Risk model          Correlation + daily limit  Strike-specific Greeks
Logging             signal_log.db              signal_log.db + trade_mgr


==============================================================================
MOST COMMON USE CASES
==============================================================================

1. "How many different strikes did I trade today?"
   → python diag_options_system.py --summary

2. "Why wasn't my signal executed?"
   → python diag_signal_generation_flow.py --symbol NIFTY
   Look for "❌ REJECTED CANDIDATES" section + rejection_reason

3. "Show me all the BANKNIFTY trades with their details"
   → python diag_option_strike_audit.py --symbol BANKNIFTY --today

4. "Compare executed vs rejected signals (why do some pass, others don't?)"
   → python diag_signal_generation_flow.py --symbol NIFTY --all-dates

5. "What's the raw data for ML analysis?"
   → sqlite3 signal_log.db
     SELECT symbol, strategy, score, confluence, option_strike, option_dte,
            option_premium, executed, tb_label
     FROM signal_log WHERE signal_date >= date('now', '-7 days')
     ORDER BY id DESC;


==============================================================================
"""

def show_help():
    import __doc__
    # Just print the file content
    pass

if __name__ == "__main__":
    import sys
    with open(__file__, 'r') as f:
        print(f.read())
