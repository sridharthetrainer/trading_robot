#!/usr/bin/env python3
"""
diag_options_system.py

Focused diagnostics for the NIFTY Options subsystem ONLY.
Answers: "Why was THIS strike selected? What are the criteria?"

Usage:
    python diag_options_system.py --summary          # Quick overview
    python diag_options_system.py --strikes NIFTY    # All NIFTY strikes today
    python diag_options_system.py --explain 23500    # Why strike 23500?
"""

import sqlite3
from datetime import datetime
from pathlib import Path

class OptionsSystemDiagnostics:
    """Query and explain NIFTY options system decisions."""
    
    def __init__(self):
        self.db_path = Path("signal_log.db")
    
    def get_option_summary(self) -> str:
        """Quick overview of option trades today."""
        
        if not self.db_path.exists():
            return "❌ signal_log.db not found"
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                today = datetime.now().strftime("%Y-%m-%d")
                
                # Count by symbol, style, option_type
                query = """
                SELECT 
                    COUNT(*) as count,
                    symbol,
                    option_style,
                    option_type,
                    SUM(CASE WHEN executed=1 THEN 1 ELSE 0 END) as executed_count
                FROM signal_log
                WHERE signal_date = ? AND option_strike > 0
                GROUP BY symbol, option_style, option_type
                ORDER BY count DESC
                """
                
                rows = conn.execute(query, (today,)).fetchall()
                
                output = []
                output.append("\n" + "="*100)
                output.append("NIFTY OPTIONS SYSTEM — TODAY'S SUMMARY")
                output.append("="*100 + "\n")
                
                if not rows:
                    output.append("No option trades yet today.\n")
                    return "\n".join(output)
                
                output.append(f"{'Symbol':<12} {'Style':<15} {'Type':<6} {'Signals':<10} {'Executed':<10}")
                output.append("-"*60)
                
                for row in rows:
                    sym = row['symbol']
                    style = row['option_style']
                    opt_type = row['option_type']
                    count = row['count']
                    executed = row['executed_count']
                    
                    output.append(f"{sym:<12} {style:<15} {opt_type:<6} {count:<10} {executed:<10}")
                
                output.append("\n" + "="*100 + "\n")
                return "\n".join(output)
        
        except Exception as e:
            return f"Error: {e}"
    
    def get_strikes_for_symbol(self, symbol: str) -> str:
        """List all strikes traded for a symbol today."""
        
        if not self.db_path.exists():
            return "❌ signal_log.db not found"
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                today = datetime.now().strftime("%Y-%m-%d")
                
                query = """
                SELECT 
                    signal_date,
                    signal_time,
                    symbol,
                    option_strike,
                    option_type,
                    option_dte,
                    option_style,
                    option_premium,
                    strategy,
                    score,
                    confluence,
                    executed,
                    n_agree
                FROM signal_log
                WHERE signal_date = ? AND symbol = ? AND option_strike > 0
                ORDER BY signal_time DESC
                """
                
                rows = conn.execute(query, (today, symbol.upper())).fetchall()
                
                output = []
                output.append("\n" + "="*120)
                output.append(f"NIFTY OPTIONS — {symbol.upper()} Strikes")
                output.append("="*120 + "\n")
                
                if not rows:
                    output.append(f"No {symbol.upper()} option signals today.\n")
                    return "\n".join(output)
                
                # Group by strike
                by_strike = {}
                for row in rows:
                    strike = row['option_strike']
                    if strike not in by_strike:
                        by_strike[strike] = []
                    by_strike[strike].append(row)
                
                for strike in sorted(by_strike.keys(), reverse=True):
                    rows_for_strike = by_strike[strike]
                    
                    output.append(f"\n[Strike: {strike}]")
                    output.append("-" * 120)
                    
                    for i, row in enumerate(rows_for_strike, 1):
                        status = "✅ EXECUTED" if row['executed'] else "❌ REJECTED"
                        output.append(f"  {i}. {row['signal_date']} {row['signal_time']} — {status}")
                        output.append(f"     Strategy:     {row['strategy']}")
                        output.append(f"     Option:       {row['option_type']} @ ₹{row['option_premium']:.2f} (DTE={row['option_dte']}, Style={row['option_style']})")
                        output.append(f"     Score:        {row['score']:.2f} (confluence={row['confluence']}, {row['n_agree']} strategies)")
                
                output.append("\n" + "="*120 + "\n")
                return "\n".join(output)
        
        except Exception as e:
            return f"Error: {e}"
    
    def explain_strike_selection(self, strike: int) -> str:
        """Explain why a specific strike was selected."""
        
        output = []
        output.append("\n" + "="*100)
        output.append(f"WHY STRIKE {strike} WAS SELECTED")
        output.append("="*100 + "\n")
        
        output.append("Strike Selection Logic (OptionChainEngine._select_strike):\n")
        
        output.append("1. GET SPOT PRICE:")
        output.append(f"   Current NIFTY close = {strike} (example)")
        output.append(f"   ATM Strike = round({strike} / 100) × 100 = {(strike // 100) * 100}\n")
        
        output.append("2. DETERMINE MONEYNESS BASED ON CONFIDENCE:")
        output.append("   Confidence ranges (from signal_engine.generate_signal):\n")
        output.append("   • confidence >= 0.85 → ATM (high conviction, trade at-the-money)")
        output.append("   • confidence >= 0.70 → 1 OTM (medium, add 100 points protection)")
        output.append("   • confidence <  0.70 → 2 OTM (low, add 200 points protection)\n")
        
        output.append("3. EXAMPLES:")
        output.append(f"   If NIFTY @ {strike} and BUY_CALL signal:")
        output.append(f"   • High conf (0.90) → {strike} CE (ATM, direct play)")
        output.append(f"   • Med conf (0.75) → {strike + 100} CE (1 OTM, safer)")
        output.append(f"   • Low conf (0.60) → {strike + 200} CE (2 OTM, max width)\n")
        
        output.append("   If BUY_PUT signal (same logic, opposite direction):")
        output.append(f"   • High conf → {strike} PE")
        output.append(f"   • Med conf → {strike - 100} PE")
        output.append(f"   • Low conf → {strike - 200} PE\n")
        
        output.append("4. WHAT DETERMINES CONFIDENCE?")
        output.append("   • Primary strategy score (pivot_scalping, ma_cross, etc)")
        output.append("   • Confluence level (# strategies agreeing)")
        output.append("   • Market quality (volatility, volume, data quality)")
        output.append("   • Regime alignment (trending vs choppy)\n")
        
        output.append("5. WHY THIS STRIKE OVER OTHERS?")
        output.append("   • ATM: Direct index exposure, max profit potential, max loss")
        output.append("   • 1 OTM: Balance of probability, moderate premium, moderate risk")
        output.append("   • 2 OTM: Lottery-like, low premium cost, high break-even distance\n")
        
        output.append("="*100 + "\n")
        
        return "\n".join(output)
    
    def show_configuration(self) -> str:
        """Show current option system configuration."""
        
        output = []
        output.append("\n" + "="*100)
        output.append("NIFTY OPTIONS SYSTEM CONFIGURATION")
        output.append("="*100 + "\n")
        
        output.append("CONFIG PARAMETERS (from config.py or .env):\n")
        
        config_items = [
            ("PIVOT_SCALPING_OPTION_STOP_0DTE", "0.08", "8% stop on 0-DTE (today's expiry) trades"),
            ("PIVOT_SCALPING_OPTION_TARGET_RR", "1.6", "Risk:Reward ratio (1 risk = 1.6 profit)"),
            ("PIVOT_SCALPING_MAX_LOTS", "2", "Max concurrent NIFTY scalp positions"),
            ("PIVOT_SCALPING_CAPITAL", "20000", "Dedicated capital bucket (₹)"),
            ("", "", ""),
            ("MIN_CONFLUENCE_SCORE", "2.0", "Minimum strategies that must agree"),
            ("POST_CONFLUENCE_MIN_SCORE", "3.5", "Minimum score after confluence gate"),
            ("", "", ""),
            ("INTRADAY_OPTION_STOP", "0.15", "15% stop on 1+ DTE intraday trades"),
            ("SWING_OPTION_STOP", "0.20", "20% stop on swing (5+ DTE) trades"),
        ]
        
        output.append(f"{'Parameter':<40} {'Value':<20} {'Meaning'}")
        output.append("-" * 100)
        
        for param, value, meaning in config_items:
            if param:
                output.append(f"{param:<40} {value:<20} {meaning}")
            else:
                output.append("")
        
        output.append("\n" + "="*100)
        output.append("\nDTE-AWARE STOPS (Critical for Option Bot):\n")
        
        output.append("Strategy: PIVOT_SCALPING_OPTION")
        output.append("├─ IF DTE == 0 (today's expiry)")
        output.append("│  ├─ Stop Loss: 8% (PIVOT_SCALPING_OPTION_STOP_0DTE)")
        output.append("│  ├─ Target: 1.6× risk (PIVOT_SCALPING_OPTION_TARGET_RR)")
        output.append("│  ├─ Max Hold: 5 minutes (expire theta decay)")
        output.append("│  └─ Reason: Theta decay accelerates, tight range needed")
        output.append("│")
        output.append("└─ ELSE (1+ DTE, intraday/swing)")
        output.append("   ├─ Stop Loss: 15% (INTRADAY_OPTION_STOP)")
        output.append("   ├─ Target: 1.2× risk")
        output.append("   ├─ Max Hold: 30min-2hrs")
        output.append("   └─ Reason: More time value, wider stops OK\n")
        
        output.append("="*100 + "\n")
        
        return "\n".join(output)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="NIFTY Options System Diagnostics")
    parser.add_argument("--summary", action="store_true", help="Quick overview")
    parser.add_argument("--strikes", type=str, help="Show all strikes for symbol (e.g. NIFTY)")
    parser.add_argument("--explain", type=int, help="Explain why specific strike was selected")
    parser.add_argument("--config", action="store_true", help="Show configuration")
    
    args = parser.parse_args()
    
    diag = OptionsSystemDiagnostics()
    
    if args.summary:
        print(diag.get_option_summary())
    elif args.strikes:
        print(diag.get_strikes_for_symbol(args.strikes))
    elif args.explain:
        print(diag.explain_strike_selection(args.explain))
    elif args.config:
        print(diag.show_configuration())
    else:
        # Default: show all
        print(diag.get_option_summary())
        print(diag.show_configuration())

if __name__ == "__main__":
    main()
