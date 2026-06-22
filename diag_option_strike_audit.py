#!/usr/bin/env python3
"""
diag_option_strike_audit.py

Audit which strikes have been executed and WHY each was generated.
Shows:
  - Each executed option trade (symbol, strike, DTE, style, premium)
  - Which strategies voted for it (confluence breakdown)
  - What score it achieved
  - When it was taken

Usage:
    python diag_option_strike_audit.py [--today] [--symbol NIFTY] [--last N]
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

def get_option_trades(today_only=False, symbol_filter=None, limit=50):
    """Query signal_log.db for all executed option trades with full context."""
    
    db_path = Path("signal_log.db")
    if not db_path.exists():
        print(f"❌ {db_path} not found")
        return []
    
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            where_clauses = ["executed = 1"]  # Only executed trades
            params = []
            
            if today_only:
                today = datetime.now().strftime("%Y-%m-%d")
                where_clauses.append("signal_date = ?")
                params.append(today)
            
            if symbol_filter:
                where_clauses.append("symbol = ?")
                params.append(symbol_filter.upper())
            
            where_sql = " AND ".join(where_clauses)
            
            query = f"""
            SELECT 
                id,
                signal_date,
                signal_time,
                symbol,
                side,
                strategy,
                score,
                raw_score,
                confluence,
                n_agree,
                agreeing_strats,
                option_type,
                option_strike,
                option_expiry,
                option_dte,
                option_style,
                option_premium,
                option_symbol,
                entry_price,
                trade_id,
                regime,
                htf_bias,
                india_vix,
                pcr_atm,
                expiry_regime
            FROM signal_log
            WHERE {where_sql}
            ORDER BY signal_date DESC, signal_time DESC
            LIMIT ?
            """
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    
    except Exception as e:
        print(f"❌ Database error: {e}")
        return []

def format_trade_audit(trades: List[Dict[str, Any]]) -> str:
    """Format trades into readable audit table."""
    
    if not trades:
        return "No option trades found."
    
    output = []
    output.append("\n" + "="*140)
    output.append("OPTION TRADE AUDIT — Executed Strikes & Signal Origins")
    output.append("="*140 + "\n")
    
    for i, trade in enumerate(trades, 1):
        output.append(f"\n[{i}] {trade.get('signal_date')} {trade.get('signal_time')}")
        output.append(f"    Trade ID: {trade.get('trade_id', 'N/A')}")
        output.append("-" * 140)
        
        # Symbol & Strike Info
        output.append(f"    SYMBOL:      {trade.get('symbol')}")
        output.append(f"    STRIKE:      {trade.get('option_strike')} {trade.get('option_type')} @ ₹{trade.get('option_premium', 0):.2f}")
        output.append(f"    EXPIRY:      {trade.get('option_expiry')} (DTE={trade.get('option_dte')})")
        output.append(f"    STYLE:       {trade.get('option_style')} | QUANTITY: {trade.get('option_symbol', 'N/A')}")
        
        # Signal Generation
        output.append(f"\n    PRIMARY STRATEGY:")
        output.append(f"      Strategy:    {trade.get('strategy')}")
        output.append(f"      Score:       {trade.get('score', 0):.2f} (raw: {trade.get('raw_score', 0):.2f})")
        output.append(f"      Confluence:  {trade.get('confluence')} ({trade.get('n_agree', 1)} strategies agreed)")
        
        # Agreeing Strategies
        agreeing = trade.get('agreeing_strats', '[]')
        try:
            if isinstance(agreeing, str):
                agreeing_list = json.loads(agreeing)
            else:
                agreeing_list = agreeing
            
            if agreeing_list:
                output.append(f"\n    STRATEGY VOTE (confluence breakdown):")
                for strat in agreeing_list[:5]:  # Show top 5
                    output.append(f"      ✓ {strat}")
                if len(agreeing_list) > 5:
                    output.append(f"      ... and {len(agreeing_list) - 5} more")
        except:
            output.append(f"\n    Agreeing strategies: {agreeing}")
        
        # Market Context at Signal Time
        output.append(f"\n    MARKET CONTEXT:")
        output.append(f"      Regime:      {trade.get('regime')} (HTF bias: {trade.get('htf_bias')})")
        output.append(f"      India VIX:   {trade.get('india_vix', 0):.2f}")
        output.append(f"      PCR (ATM):   {trade.get('pcr_atm', 0):.2f}")
        output.append(f"      Expiry Mode: {trade.get('expiry_regime')}")
        output.append(f"      Entry Price: ₹{trade.get('entry_price', 0):.2f}")
        
        output.append("")
    
    output.append("="*140 + "\n")
    
    # Summary stats
    output.append(f"\nSUMMARY ({len(trades)} trades shown):")
    
    symbols = {}
    styles = {}
    strategies = {}
    for trade in trades:
        # By symbol
        sym = trade.get('symbol')
        symbols[sym] = symbols.get(sym, 0) + 1
        
        # By style
        st = trade.get('option_style', 'UNKNOWN')
        styles[st] = styles.get(st, 0) + 1
        
        # By primary strategy
        strat = trade.get('strategy', 'UNKNOWN')
        strategies[strat] = strategies.get(strat, 0) + 1
    
    output.append(f"\n  By Symbol:")
    for sym, cnt in sorted(symbols.items(), key=lambda x: -x[1]):
        output.append(f"    {sym:15} {cnt:3d} trades")
    
    output.append(f"\n  By Style:")
    for st, cnt in sorted(styles.items(), key=lambda x: -x[1]):
        output.append(f"    {st:15} {cnt:3d} trades")
    
    output.append(f"\n  By Primary Strategy:")
    for strat, cnt in sorted(strategies.items(), key=lambda x: -x[1])[:10]:
        output.append(f"    {strat:30} {cnt:3d} trades")
    
    output.append("")
    
    return "\n".join(output)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit option strikes & signal origins")
    parser.add_argument("--today", action="store_true", help="Only today's trades")
    parser.add_argument("--symbol", type=str, help="Filter by symbol (e.g. NIFTY)")
    parser.add_argument("--last", type=int, default=50, help="Last N trades (default 50)")
    
    args = parser.parse_args()
    
    trades = get_option_trades(
        today_only=args.today,
        symbol_filter=args.symbol,
        limit=args.last
    )
    
    print(format_trade_audit(trades))

if __name__ == "__main__":
    main()
