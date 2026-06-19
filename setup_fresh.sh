#!/bin/bash
# setup_fresh.sh — Complete fresh setup after folder deletion
# Run: chmod +x setup_fresh.sh && ./setup_fresh.sh

cd ~/Desktop/trading_robot
source venv/bin/activate 2>/dev/null || true

echo "═══════════════════════════════════════════════════"
echo "FRESH SETUP — Downloading all missing files"
echo "═══════════════════════════════════════════════════"

# Step 1: Master Contract (all tokens)
echo ""
echo "[1] Downloading MasterContract_ALL.csv..."
python3 - << 'PYEOF'
import requests, pandas as pd, sys
print("  Downloading from Angel One API (190k instruments)...")
try:
    r = requests.get(
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
        timeout=30)
    df = pd.DataFrame(r.json())
    df.to_csv("MasterContract_ALL.csv", index=False)
    nfo = df[df["exch_seg"].str.upper()=="NFO"] if "exch_seg" in df.columns else pd.DataFrame()
    nse = df[df["exch_seg"].str.upper()=="NSE"] if "exch_seg" in df.columns else pd.DataFrame()
    nfo.to_csv("MasterContract_NFO.csv", index=False)
    print(f"  ✅ MasterContract_ALL.csv: {len(df):,} instruments")
    print(f"  ✅ MasterContract_NFO.csv: {len(nfo):,} NFO")
    print(f"  ✅ NSE EQ stocks: {len(nse):,}")
    # Spot check
    for sym,tok in [("RELIANCE-EQ","2885"),("HDFCBANK-EQ","1333"),("TCS-EQ","11536")]:
        row = df[df["symbol"]==sym]
        t = str(row.iloc[0]["token"]) if len(row) else "NOT FOUND"
        print(f"     {'✅' if t==tok else '⚠️'} {sym}: {t}")
except Exception as e:
    print(f"  ❌ {e}")
    sys.exit(1)
PYEOF

# Step 2: Create trades.db if missing
echo ""
echo "[2] Initializing trades.db..."
python3 - << 'PYEOF'
import sqlite3, os
if not os.path.exists("trades.db"):
    conn = sqlite3.connect("trades.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, strategy TEXT, direction TEXT,
        entry_price REAL, exit_price REAL, qty INTEGER,
        pnl REAL, status TEXT, entry_time TEXT, exit_time TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_pnl (
        date TEXT PRIMARY KEY, pnl REAL, trades INTEGER
    )""")
    conn.commit(); conn.close()
    print("  ✅ trades.db created")
else:
    print("  ✅ trades.db exists")
PYEOF

# Step 3: Create signal_log.csv if missing
echo ""
echo "[3] Initializing signal_log.csv..."
python3 - << 'PYEOF'
import os
if not os.path.exists("signal_log.csv"):
    with open("signal_log.csv","w") as f:
        f.write("timestamp,symbol,strategy,direction,score,action,price,notes\n")
    print("  ✅ signal_log.csv created")
else:
    print("  ✅ signal_log.csv exists")
PYEOF

# Step 4: dark_pool_history.csv
echo ""
echo "[4] Initializing dark_pool_history.csv..."
python3 - << 'PYEOF'
import os
if not os.path.exists("dark_pool_history.csv"):
    with open("dark_pool_history.csv","w") as f:
        f.write("date,symbol,side,value_cr,client\n")
    print("  ✅ dark_pool_history.csv created")
else:
    print("  ✅ dark_pool_history.csv exists")
PYEOF

# Step 5: meta_learner_state.json
echo ""
echo "[5] Initializing meta_learner state..."
python3 - << 'PYEOF'
import os, json
if not os.path.exists("meta_learner_state.json"):
    with open("meta_learner_state.json","w") as f:
        json.dump({"trades":{}}, f)
    print("  ✅ meta_learner_state.json created")
else:
    print("  ✅ meta_learner_state.json exists")
PYEOF

# Step 6: participant_oi_history.json
echo ""
echo "[6] Initializing OI history..."
python3 - << 'PYEOF'
import os, json
if not os.path.exists("participant_oi_history.json"):
    with open("participant_oi_history.json","w") as f:
        json.dump([], f)
    print("  ✅ participant_oi_history.json created")
else:
    print("  ✅ participant_oi_history.json exists")
PYEOF

# Step 7: fii_history.csv
echo ""
echo "[7] Initializing FII history..."
python3 - << 'PYEOF'
import os
if not os.path.exists("fii_history.csv"):
    with open("fii_history.csv","w") as f:
        f.write("date,fii_net,dii_net,fii_eq,dii_eq\n")
    print("  ✅ fii_history.csv created")
else:
    print("  ✅ fii_history.csv exists")
PYEOF

# Step 8: correlation_matrix.json
echo ""
echo "[8] Initializing correlation matrix..."
python3 - << 'PYEOF'
import os, json
if not os.path.exists("correlation_matrix.json"):
    with open("correlation_matrix.json","w") as f:
        json.dump({}, f)
    print("  ✅ correlation_matrix.json created")
else:
    print("  ✅ correlation_matrix.json exists")
PYEOF

# Step 9: Install optional ML packages
echo ""
echo "[9] Installing optional ML packages..."
pip install hmmlearn cvxpy --quiet 2>/dev/null && echo "  ✅ hmmlearn + cvxpy installed" || echo "  ⚠️  Optional — bot runs without them"

# Step 10: Validate
echo ""
echo "[10] Running validation..."
python3 validate_scan.py 2>/dev/null | grep -E "✅|❌|SUMMARY|DataFetcher|Signal|BOT"

echo ""
echo "═══════════════════════════════════════════════════"
echo "SETUP COMPLETE"
echo "  Run: ./bot.sh restart"
echo "  Then: python3 validate_all_symbols.py 2>/dev/null"
echo "═══════════════════════════════════════════════════"
