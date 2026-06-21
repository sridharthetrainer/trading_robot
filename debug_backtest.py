#!/usr/bin/env python3
"""Debug why bhavcopy cache isn't working in backtest."""
import sys, sqlite3
sys.path.insert(0, '.')

print("=== BHAVCOPY DEBUG ===\n")

# 1. Check cache file
from pathlib import Path
db = Path("nse_cache.db")
print(f"nse_cache.db exists: {db.exists()} | size: {db.stat().st_size//1024}KB" if db.exists() else "nse_cache.db: MISSING")

if db.exists():
    conn = sqlite3.connect("nse_cache.db")
    c = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MAX(date), MIN(date) FROM ohlcv")
    total, syms, maxd, mind = c.fetchone()
    print(f"Records: {total:,} | Symbols: {syms:,} | Range: {mind} → {maxd}")
    
    # Check specific symbols
    print("\nSymbol lookups:")
    for sym in ["RELIANCE","HDFCBANK","PIDILITIND","INFY","TCS","BAJFINANCE"]:
        r = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol=?", (sym,)).fetchone()[0]
        print(f"  {sym:15}: {r} rows {'✅' if r>0 else '❌'}")
    conn.close()

# 2. Test get_ohlcv directly
print("\n=== get_ohlcv test ===")
from bhavcopy_cache import get_ohlcv
for sym in ["RELIANCE","HDFCBANK","INFY"]:
    df = get_ohlcv(sym, 60)
    if df is not None:
        print(f"  ✅ {sym}: {len(df)} bars | last ₹{float(df['close'].iloc[-1]):,.2f}")
    else:
        print(f"  ❌ {sym}: None returned")

# 3. Test _fetch from autonomous_backtest directly  
print("\n=== autonomous_backtest._fetch test ===")
try:
    from autonomous_backtest import _fetch
    for sym in ["RELIANCE","NIFTY","HDFCBANK"]:
        df = _fetch(sym, 60)
        if df is not None and len(df) > 0:
            print(f"  ✅ {sym}: {len(df)} bars | last ₹{float(df['close'].iloc[-1]):,.2f}")
        else:
            print(f"  ❌ {sym}: no data from _fetch")
except Exception as e:
    print(f"  ❌ import error: {e}")
    import traceback; traceback.print_exc()
