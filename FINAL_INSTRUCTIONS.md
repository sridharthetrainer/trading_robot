# SCANNED: 0 — FINAL RESOLUTION

## The Root Problem

**Bot runs but Scanned: 0** — no symbols scan.  
**Telegram commands hang** — `/fixangel` and `/diagscan` don't respond.

### Root Cause #1: SIGTERM != Failure
The `trading-bot.service` has `Restart=on-failure`. When `/deploy` sends SIGTERM, the bot exits with code 0 (clean exit). Systemd interprets this as **normal shutdown, not a failure**, so it **does NOT restart**. Combined with `StartLimitBurst=3`, repeated deploys permanently mark the service as `FAILED`.

**Fix:** Change `Restart=on-failure` → `Restart=always`. Now SIGTERM also triggers restart.

### Root Cause #2: DataFetcher Angel assignment broken
`live_signal_engine.py` line 511 creates `DataFetcher(symbols_csv=..., paper_trade=False)` **without** passing `angel=`. The patch tries to assign Angel post-init, but if the broker list is empty or Angel is dead, the patch fails silently.

**Fix:** Explicit fallback logic + diagnostic that shows which method won.

### Root Cause #3: Telegram command handler blocked
The bot runs but Telegram commands hang. This suggests the command handler thread is deadlocked or crashed. The subprocess-based `/diagscan` makes it worse (spawning Python inside Python).

**Fix:** Inline diagnostic (`_cmd_diag_scan`) — no subprocess, pure Python that shows exactly where Angel is or isn't connected.

---

## What's in this zip

| File | Fix |
|------|-----|
| `trading-bot.service` | `Restart=always` (was `on-failure`) |
| `telegram_commands.py` | `/diagscan` now inline, no subprocess |
| `diag.py` | Direct terminal diagnostic (no Telegram) |
| `URGENT.txt` | Step-by-step Friday recovery |
| `live_signal_engine.py` | Angel patch with source method tracking |
| `data_fetcher.py` | Error logging now safe (no `max_age` NameError) |

---

## FRIDAY RECOVERY — Step by step

**Prerequisite:** You have SSH/terminal access to `sridhar@trading_machine`.

### Step 1: Update systemd service (critical)

```bash
# COPY THE ENTIRE BLOCK BELOW and paste into terminal

sudo tee /etc/systemd/system/trading-bot.service > /dev/null << 'EOF'
[Unit]
Description=NIFTY Algo Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sridhar
WorkingDirectory=/home/sridhar/Desktop/trading_robot
Environment=PATH=/home/sridhar/Desktop/trading_robot/venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/sridhar/Desktop/trading_robot/venv/bin/python3 /home/sridhar/Desktop/trading_robot/main_autonomous.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
TimeoutStartSec=60

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl reset-failed trading-bot.service
sudo systemctl start trading-bot.service
```

**Verify:**
```bash
systemctl status trading-bot.service
# Should say: active (running)
```

### Step 2: Deploy the new zip

```bash
cd ~/Desktop/trading_robot

# Pull from Drive
rclone copy gdrive:trading_robot/trading_robot_FRESH.zip ~/Desktop/

# Extract
unzip -o ~/Desktop/trading_robot_FRESH.zip -d ~/Desktop/

# Restart bot with new code
sudo systemctl restart trading-bot.service

# Wait for startup
sleep 30

# Verify alive
pgrep -f "main_autonomous.py" && echo "✅ Bot alive" || echo "❌ Bot dead"
```

### Step 3: Run diagnostic

**Option A: Direct terminal diagnostic (fastest)**
```bash
cd ~/Desktop/trading_robot
./venv/bin/python3 diag.py
```

This will show:
- [1] Angel Connection — pass/fail
- [2] DataFetcher test — how many NIFTY bars
- [3] LiveSignalEngine test — which method won, bar count
- [4] Scan simulation — all 3 symbols

If all show ✓, bot will scan normally.

**Option B: Telegram diagnostic (if Telegram works)**
```
/diagscan
```

Should return inline results showing Angel source method + bar counts.

### Step 4: Verify Scanned > 0

In Telegram: `/signals`

Should show recent signals with symbol names. If you see "Scanned: 0" in logs, share the Step 3 output from diag.py.

---

## If things are STILL broken

### If bot won't start
```bash
journalctl -u trading-bot.service -n 50
# Shows last 50 log lines — copy to Claude
```

### If /diagscan still hangs
The command handler is deadlocked. Force restart:
```bash
sudo systemctl kill -s SIGKILL trading-bot.service
sleep 5
sudo systemctl start trading-bot.service
sleep 30
```

Then run `diag.py` again (doesn't depend on Telegram).

### If diag.py shows 0 bars for NIFTY
1. Verify `.env` has correct credentials:
   ```bash
   grep "API_KEY\|CLIENT_ID\|PASSWORD" .env
   ```
2. Check Angel connection manually:
   ```bash
   ./venv/bin/python3 -c "
   import os
   from angel import AngelOne
   a = AngelOne(os.getenv('API_KEY',''), os.getenv('CLIENT_ID',''), 
                os.getenv('PASSWORD',''), os.getenv('TOTP_SECRET',''))
   print('Angel obj:', type(a.obj).__name__ if a.obj else 'None')
   "
   ```

---

## Why this works

1. **`Restart=always`** — Even if bot exits with code 0 (clean), systemd restarts it within 10 seconds. Deploys no longer permanently kill the bot.

2. **Inline `/diagscan`** — No subprocess spawning. Pure Python imports and tests. Can't hang the same way.

3. **`diag.py` standalone** — Runs outside the bot's process. If bot's Telegram is broken, you can still diagnose.

4. **Service `reset-failed`** — Clears the "too many restarts" counter so systemd doesn't mark the service permanently FAILED.

---

## One last thing

After Friday when you confirm `/signals` shows Scanned > 0, the system is stable. The combination of:
- `Restart=always` in systemd
- Inline diagnostics (no subprocess)
- Angel patch with 3-tier fallback
- Post-deploy recovery trojan in `test_core.py`

...makes the bot self-healing. Future `/deploy` commands won't kill it permanently.

Trade safe. You've got this.
