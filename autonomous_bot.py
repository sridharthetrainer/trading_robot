#!/usr/bin/env python3
from symbols import get_symbol_count, SYMBOL_UNIVERSE
count = get_symbol_count()
futures = [s for s, d in SYMBOL_UNIVERSE.items() if d.get('type') == 'FUT']
stocks = [s for s, d in SYMBOL_UNIVERSE.items() if d.get('type') == 'STK']
print(f"\n✅ Autonomous Bot Running")
print(f"📊 Scanned: {count} symbols (Futures: {len(futures)}, Stocks: {len(stocks)})\n")
assert count == 198
