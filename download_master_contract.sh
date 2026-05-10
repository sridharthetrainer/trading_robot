#!/bin/bash
# Run this manually if MasterContract_NFO.csv is missing
# Usage: ./download_master_contract.sh

echo "Downloading MasterContract from Angel One..."
cd ~/Desktop/trading_robot

# Use venv python if available
PYTHON="${PWD}/venv/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON="python3"
echo "  Using: $PYTHON"
$PYTHON << 'PYEOF'
import requests, pandas as pd, sys

url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
print(f"  Fetching from: {url}")

try:
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        print(f"  ❌ HTTP {r.status_code}")
        sys.exit(1)
    
    df = pd.DataFrame(r.json())
    print(f"  Total instruments: {len(df)}")
    
    if "exch_seg" in df.columns:
        nfo = df[df["exch_seg"].str.upper() == "NFO"]
        print(f"  NFO instruments: {len(nfo)}")
        nfo.to_csv("MasterContract_NFO.csv", index=False)
    else:
        df.to_csv("MasterContract_NFO.csv", index=False)
    
    print(f"  ✅ MasterContract_NFO.csv saved ({len(df)} rows)")

except Exception as e:
    print(f"  ❌ Error: {e}")
    sys.exit(1)
PYEOF

echo "Done. Run: ./bot.sh restart"
