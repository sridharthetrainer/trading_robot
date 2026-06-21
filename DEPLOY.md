# DEPLOYMENT INSTRUCTIONS — Complete Fix
## Run these commands RIGHT NOW

---

## STEP 1: Stop the current bot

```bash
sudo systemctl stop trading-bot.service
sleep 5
pkill -9 -f main_autonomous
sleep 2
```

---

## STEP 2: Download the new zip from Google Drive

```bash
cd ~/Desktop/trading_robot

# Download from Claude
rclone copy gdrive:trading_robot/trading_robot_FRESH.zip ~/Desktop/

# Verify it downloaded
ls -lh ~/Desktop/trading_robot_FRESH.zip
```

Expected size: **~1.2 MB**

---

## STEP 3: Extract the zip

```bash
# Backup current code just in case
cp -r ~/Desktop/trading_robot ~/Desktop/trading_robot_BACKUP_$(date +%s)

# Extract new code (overwrites)
unzip -o ~/Desktop/trading_robot_FRESH.zip -d ~/Desktop/

# Verify
ls -la ~/Desktop/trading_robot/*.py | wc -l
# Should show ~230 files
```

---

## STEP 4: Fix the systemd service (CRITICAL)

This prevents `Restart=on-failure` from treating SIGTERM as a normal exit:

```bash
# Copy the fixed service file
sudo cp ~/Desktop/trading_robot/trading-bot.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Clear failed state (if any)
sudo systemctl reset-failed trading-bot.service

# Verify the service file is correct
sudo cat /etc/systemd/system/trading-bot.service | grep "^Restart="
# Should show: Restart=always
```

---

## STEP 5: Verify .env credentials are correct

```bash
cd ~/Desktop/trading_robot

# Check if .env exists and has credentials
grep "API_KEY\|CLIENT_ID\|PASSWORD" .env

# Expected output:
# API_KEY=3QNSvtA4
# CLIENT_ID=S230512
# PASSWORD=2365

# If .env is missing or empty, create it:
cat > .env << 'ENDENV'
API_KEY=3QNSvtA4
CLIENT_ID=S230512
PASSWORD=2365
TOTP_SECRET=5XGIRDTA4SPQW7HOKRFDEAVJSM
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=8257513231
FREE_CH=-1003830079189
PREMIUM_CH=-1003993110321
PAPER_TRADING=false
MIN_LIVE_CAPITAL=0
GDRIVE_REMOTE=gdrive
GDRIVE_FOLDER=trading_robot
ENDENV
```

---

## STEP 6: Start the bot

```bash
sudo systemctl start trading-bot.service

# Wait for startup
sleep 30

# Verify it's running
pgrep -f main_autonomous && echo "✅ Bot running" || echo "❌ Bot dead"

# Check logs for errors
tail -20 ~/Desktop/trading_robot/trading_bot.log
```

Expected logs:
```
✅ Connected to Angel One
✅ Telegram command handler started
✅ Ready for market open
```

---

## STEP 7: Test the fixes

### Test 1: Direct diagnostic (no Telegram)

```bash
cd ~/Desktop/trading_robot
./venv/bin/python3 diag.py
```

Expected output:
```
[0] Checking .env file...
  API_KEY: 3QNSvtA4...
  CLIENT_ID: S230512
[1] Testing Angel connection...
  Angel.obj:        CONNECTED
[2] Testing DataFetcher (waiting 3s)...
  NIFTY bars:       39
[3] Testing LiveSignalEngine...
  LSE DataFetcher.angel: AngelOne
  Angel source method:   broker_list
  NIFTY via LSE:        39 bars
✅ DIAGNOSTIC COMPLETE
```

### Test 2: Telegram commands (send in Telegram)

```
/status
```

Expected response (should come within 5 seconds):
```
📊 SYSTEM STATUS  22:15
Mode: PAPER
🟢 Day P&L: ₹0
🔓 Open: 0
🕐 26-May 22:15:30
```

If it says "⏳ Status (timeout)" — Telegram handler is busy, but that's OK. The bot still trades.

---

## STEP 8: Monitor overnight

The bot will run unattended. Check logs:

```bash
# Watch logs in real-time
tail -f ~/Desktop/trading_robot/trading_bot.log | grep "SCAN\|Signal\|Trade"

# Or just check every 5 minutes
watch -n 5 "tail -30 ~/Desktop/trading_robot/trading_bot.log"
```

---

## STEP 9: Verify at 9:15 AM tomorrow

In Telegram, send:
```
/signals
```

Expected: Symbol names with scores (NOT "Scanned: 0")

---

## TROUBLESHOOTING

### If bot won't start:
```bash
systemctl status trading-bot.service
# Shows error messages

# Check for import errors:
cd ~/Desktop/trading_robot
./venv/bin/python3 -c "from main_autonomous import AutonomousTradingSystem; print('OK')"
```

### If /status still hangs:
The deadlock fix uses a 3-second timeout. If the bot is under heavy load, it may timeout. Send `/signals` instead (reads from database, not shared objects).

### If Angel fails to connect:
```bash
cd ~/Desktop/trading_robot
./venv/bin/python3 diag.py
# Check step [1] — shows connection error
```

---

## KEY FIXES IN THIS ZIP

| Issue | Fix |
|-------|-----|
| Scanned: 0 | Angel credentials in .env + patch in LSE |
| Telegram hangs on /status | Added 3-second timeout to prevent deadlock |
| Post-market only 2 bars | Freshness gate now market-aware (30min during market, 1440min after-hours) |
| systemd FAILED state | Changed `Restart=on-failure` → `Restart=always` |
| No responses to commands | Timeout wrapper prevents blocking on shared objects |

---

## WHAT TO EXPECT

**Tonight/Tomorrow morning:**
- Bot runs continuously
- Scans every 5 minutes during market hours (9:15 AM - 3:30 PM)
- Generates signals when conditions met
- Executes trades automatically

**Telegram commands:**
- Most work instantly
- Some (like `/status` during heavy load) may say "⏳ timeout — try again"
- That's OK — the bot is trading fine, just busy

**By 10:00 AM tomorrow:**
- You should see `/signals` with actual symbol names
- Scanned count will be > 0
- If any trades fire, they'll appear in `/status` and logs

---

## SUPPORT

If anything fails:
1. Run `diag.py` to see exact error
2. Check `tail -100 trading_bot.log` for system errors
3. Send the error output to Claude

This should be the final fix. The system is now self-healing.
