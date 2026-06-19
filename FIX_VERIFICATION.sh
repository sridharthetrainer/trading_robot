#!/bin/bash

echo "════════════════════════════════════════════════════════════"
echo "VERIFICATION: All Scanned:0 Fixes Applied"
echo "════════════════════════════════════════════════════════════"

# FIX #1: Angel passed to DataFetcher
echo ""
echo "[1] DataFetcher gets Angel parameter..."
grep -A15 "self.data_fetcher = DataFetcher" live_signal_engine.py | head -20
if grep -q "angel=" live_signal_engine.py | grep -A2 "self.data_fetcher"; then
    echo "✅ Angel parameter present"
else
    echo "⚠️  Check if Angel passed via patch at lines 524-571"
fi

# FIX #2: Market-aware freshness gate
echo ""
echo "[2] Freshness gate is market-aware..."
if grep -q "in_market_hours" data_fetcher.py; then
    echo "✅ Market-aware gate found"
    grep -A2 "in_market_hours" data_fetcher.py | head -4
else
    echo "❌ Market-aware gate MISSING"
fi

# FIX #3: MIN_BARS lowered
echo ""
echo "[3] MIN_BARS threshold..."
grep "_min_bars" live_signal_engine.py | head -3
if grep -q "_min_bars = 5" live_signal_engine.py; then
    echo "✅ MIN_BARS lowered to 5"
else
    echo "⚠️  Check MIN_BARS value"
fi

# FIX #4: PAPER_TRADING=false
echo ""
echo "[4] Real trading enabled..."
grep "PAPER_TRADING" .env
if grep -q "PAPER_TRADING=false" .env; then
    echo "✅ Real trading enabled"
else
    echo "❌ PAPER_TRADING not false"
fi

# FIX #5: Symbol universe initialized
echo ""
echo "[5] Symbol universe..."
ls -la *.csv 2>/dev/null | head -3 || echo "⚠️  No CSVs found (uses default list)"

# FIX #6: /status has timeout
echo ""
echo "[6] Telegram /status deadlock fix..."
if grep -A10 "def _cmd_status" telegram_commands.py | grep -q "timeout"; then
    echo "✅ Timeout wrapper present"
    grep -A10 "def _cmd_status" telegram_commands.py | grep "timeout"
else
    echo "❌ Timeout wrapper MISSING"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
