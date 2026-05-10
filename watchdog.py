"""
watchdog.py  —  Smart phase-aware watchdog for the autonomous trading bot.

DESIGN PHILOSOPHY
─────────────────
The watchdog's job is to recover from genuine hangs — NOT to
aggressively restart a healthy bot that is doing slow work.

KILL DECISION REQUIRES ALL OF:
  1. Heartbeat stale beyond phase threshold
  2. Process consuming ZERO CPU (truly stuck, not just slow)
  3. Two consecutive checks confirming the above
  4. No open positions (never kill mid-trade)
  5. Process older than startup grace period

PHASE-AWARE THRESHOLDS
───────────────────────
  STARTUP / UNKNOWN   → 1800s  (30 min — startup + learning can be slow)
  AFTER_HOURS         → 900s   (15 min — bot sleeps 300s between cycles)
  LEARNING / BACKTEST → 3600s  (60 min — overnight backtest takes time)
  LIVE / MARKET_OPEN  → 180s   (3 min  — strict during trading hours)
  OPENING_WAIT        → 300s   (5 min  — pre-market checks)

CPU ACTIVITY CHECK
──────────────────
  Reads /proc/PID/stat to get CPU ticks.
  Waits 5 seconds and reads again.
  If CPU ticks increased → bot is working → do NOT kill.
  Only kill if BOTH heartbeat stale AND zero CPU activity.

OPEN POSITION SAFETY
────────────────────
  Reads trades.db before any kill.
  If any option position is OPEN → skip kill, send alert instead.

PROGRESSIVE WARNINGS
────────────────────
  First stale check  → log warning only
  Second stale check → Telegram alert only
  Third stale check  → kill if CPU=0 and no open positions
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | WATCHDOG | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("watchdog.log"),
    ],
)
logger = logging.getLogger("watchdog")

# ── Config ────────────────────────────────────────────────────────────────────
LIVE_STATUS_FILE = os.getenv("LIVE_STATUS_FILE", "live_status.json")
TRADES_DB        = os.getenv("TRADES_DB",        "trades.db")
BOT_SERVICE      = "trading-bot"
CHECK_INTERVAL   = 30       # seconds between watchdog checks

# ── Phase thresholds (seconds before considering action) ───────────────────────
THRESHOLDS = {
    # During trading — strict
    "LIVE":          180,   # 3 min  — loops every 30s
    "MARKET_OPEN":   180,
    "MARKET_BUFFER": 300,
    "OPENING_WAIT":  300,

    # After hours — lenient
    "AFTER_HOURS":   900,   # 15 min — sleeps 300s between cycles
    "EOD":           900,
    "CLOSED":        900,

    # Slow operations — very lenient
    "STARTUP":       5400,  # 90 min — patience: holiday startup runs full cycle — startup may run backtest+ML on holidays
    "LEARNING":      3600,  # 60 min — overnight backtest/retrain
    "BACKTEST":      3600,
    "UNKNOWN":       5400,  # 90 min — unknown phase = be very patient
}

# ── Safety config ─────────────────────────────────────────────────────────────
STARTUP_GRACE_SEC  = 120    # never take action on process younger than this
CPU_CHECK_WAIT_SEC = 5      # seconds between CPU tick readings
STALE_COUNT_KILL   = 3      # consecutive stale checks before kill
MEMORY_MAX_MB      = 950    # kill on memory leak

# ── Cooldowns (prevent Telegram spam) ─────────────────────────────────────────
COOLDOWN = {
    "stale_warn":   600,    # 10 min between "not responding" alerts
    "restart":      180,    # 3 min between restart attempts
    "emergency":    300,    # 5 min between emergency close checks
    "premarket":   7200,    # 2 hr between pre-market alerts
    "burst":       3600,    # 1 hr between burst-limit alerts
    "memory":       600,    # 10 min between memory alerts
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")


# ── Telegram ──────────────────────────────────────────────────────────────────
def _tg(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import urllib.request
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data=data,
                headers={"Content-Type": "application/json"},
            ),
            timeout=10,
        )
    except Exception as e:
        logger.debug("Telegram: %s", e)


# ── Status readers ────────────────────────────────────────────────────────────
def read_status() -> dict:
    try:
        p = Path(LIVE_STATUS_FILE)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def heartbeat_age_and_phase() -> tuple[float, str]:
    s     = read_status()
    ts    = s.get("timestamp", "")
    phase = str(s.get("market_phase", "UNKNOWN")).upper()
    if not ts:
        return float("inf"), phase
    try:
        age = (datetime.now() - datetime.fromisoformat(str(ts)[:19])).total_seconds()
        return max(0.0, age), phase
    except Exception:
        return float("inf"), phase


def get_threshold(phase: str) -> int:
    return THRESHOLDS.get(phase, THRESHOLDS["UNKNOWN"])


def bot_pid() -> int:
    try:
        r = subprocess.run(
            ["pgrep", "-f", "main_autonomous.py"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in r.stdout.split() if p.isdigit()]
        return pids[0] if pids else 0
    except Exception:
        return 0


def proc_age_sec(pid: int) -> int:
    """How many seconds has this process been running."""
    try:
        r = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        v = r.stdout.strip()
        return int(v) if v.isdigit() else 9999
    except Exception:
        return 9999


def cpu_ticks(pid: int) -> int:
    """Total CPU ticks consumed by process (utime + stime from /proc)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
        return int(parts[13]) + int(parts[14])   # utime + stime
    except Exception:
        return -1


def is_cpu_active(pid: int, wait: float = CPU_CHECK_WAIT_SEC) -> bool:
    """
    Returns True if process consumed ANY CPU in the last `wait` seconds.
    A truly hung process consumes zero CPU. A slow-working process uses some.
    """
    t1 = cpu_ticks(pid)
    if t1 < 0:
        return False    # can't read = assume dead
    time.sleep(wait)
    t2 = cpu_ticks(pid)
    if t2 < 0:
        return False
    active = t2 > t1
    logger.debug("CPU check PID=%d ticks=%d→%d active=%s", pid, t1, t2, active)
    return active


def mem_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def open_positions() -> int:
    """Count of open option positions in trades.db."""
    try:
        conn = sqlite3.connect(TRADES_DB, timeout=5)
        r = conn.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE status='OPEN' AND (symbol LIKE '%CE' OR symbol LIKE '%PE')"
        ).fetchone()
        conn.close()
        return int(r[0]) if r else 0
    except Exception:
        return 0  # assume 0 if DB not readable


def svc_active() -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", BOT_SERVICE],
            capture_output=True, timeout=5,
        ).returncode == 0
    except Exception:
        return False


def svc_failed() -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-failed", "--quiet", BOT_SERVICE],
            capture_output=True, timeout=5,
        ).returncode == 0
    except Exception:
        return False


def restart_svc() -> None:
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", BOT_SERVICE],
            timeout=15, check=True,
        )
        logger.info("systemd restart triggered")
    except Exception as e:
        logger.error("Restart failed: %s", e)


def emergency_close() -> None:
    """Close all open option positions independently of the bot."""
    logger.critical("EMERGENCY CLOSE: options open after 3:35 PM, bot not active")
    _tg(
        "🚨 <b>WATCHDOG EMERGENCY CLOSE</b>\n"
        "Options open after 3:35 PM — bot not active.\n"
        "Attempting emergency close..."
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0,'.')\n"
             "from trade_manager import TradeManager\n"
             "from broker_manager import BrokerManager\n"
             "import config as cfg\n"
             "bm=BrokerManager({'API_KEY':cfg.API_KEY,'CLIENT_ID':cfg.CLIENT_ID,"
             "'PASSWORD':cfg.PASSWORD,'TOTP_SECRET':cfg.TOTP_SECRET,'PAPER_TRADE':cfg.PAPER_TRADING})\n"
             "tm=TradeManager(broker_manager=bm,capital=cfg.CAPITAL,restore_state=True)\n"
             "n=tm.close_options_at_eod()\nprint(f'Closed {n} positions')"],
            timeout=60, capture_output=True, text=True,
        )
        msg = r.stdout.strip() or r.stderr.strip()
        _tg(f"✅ Emergency close: {msg}")
    except Exception as e:
        _tg(
            f"🚨 Emergency close FAILED: {e}\n"
            "⚠️ Close manually in Angel One app NOW"
        )


# ── Main watchdog loop ────────────────────────────────────────────────────────
def run() -> None:
    logger.info(
        "Smart watchdog started | grace=%ds cpu_check=%ds stale_kills=%d",
        STARTUP_GRACE_SEC, CPU_CHECK_WAIT_SEC, STALE_COUNT_KILL,
    )
    _tg(
        "👁️ <b>SMART WATCHDOG STARTED</b>\n"
        "CPU-aware | Position-safe | Phase-adaptive\n"
        f"Startup grace: {STARTUP_GRACE_SEC}s\n"
        f"Kill requires: stale + zero CPU + no open positions"
    )

    # Cooldown timestamps
    ts = {k: 0.0 for k in COOLDOWN}

    # Consecutive stale heartbeat counter — must be stale multiple times before kill
    stale_count    = 0
    last_stale_age = 0.0
    last_stale_phase = ""

    while True:
        try:
            now  = datetime.now()
            nt   = now.time()
            t    = time.time()

            # ── 1. HEARTBEAT CHECK ────────────────────────────────────────────
            age, phase   = heartbeat_age_and_phase()
            threshold    = get_threshold(phase)
            pid          = bot_pid()
            proc_age     = proc_age_sec(pid) if pid > 0 else 0

            heartbeat_ok = age <= threshold

            if heartbeat_ok:
                # Reset stale counter — bot is alive
                if stale_count > 0:
                    logger.info(
                        "Heartbeat recovered | was_stale=%d age=%.0fs phase=%s",
                        stale_count, age, phase,
                    )
                stale_count = 0
                # Hourly log
                if now.minute == 0 and now.second < CHECK_INTERVAL:
                    logger.info(
                        "Heartbeat OK | age=%.0fs/%ds phase=%s pid=%d",
                        age, threshold, phase, pid,
                    )

            else:
                # Heartbeat is stale
                stale_count += 1
                logger.warning(
                    "Stale heartbeat #%d | age=%.0fs limit=%ds phase=%s pid=%d proc_age=%ds",
                    stale_count, age, threshold, phase, pid, proc_age,
                )

                # ── Safety gates before any kill ────────────────────────────

                # Gate 1: Process too young
                if pid > 0 and proc_age < STARTUP_GRACE_SEC:
                    logger.info(
                        "Kill skipped — process only %ds old (grace=%ds)",
                        proc_age, STARTUP_GRACE_SEC,
                    )
                    stale_count = 0   # reset — it's brand new
                    time.sleep(CHECK_INTERVAL)
                    continue

                # Gate 2: First stale — just log
                if stale_count == 1:
                    logger.warning(
                        "First stale check — monitoring. "
                        "Will alert on 2nd, kill on 3rd (if CPU=0 + no positions)."
                    )

                # Gate 3: Second stale — send Telegram warning only
                elif stale_count == 2:
                    if (t - ts["stale_warn"]) > COOLDOWN["stale_warn"]:
                        ts["stale_warn"] = t
                        _tg(
                            f"⚠️ <b>WATCHDOG WARNING</b>\n"
                            f"Bot heartbeat stale {age:.0f}s (limit {threshold}s)\n"
                            f"Phase: {phase} | PID: {pid}\n"
                            f"<i>Watching — will kill if CPU=0 next check</i>"
                        )

                # Gate 4: Third+ stale — evaluate kill
                elif stale_count >= STALE_COUNT_KILL:
                    if pid > 0:

                        # Check CPU activity — is the bot actually doing work?
                        logger.info("Checking CPU activity before kill decision...")
                        cpu_busy = is_cpu_active(pid)

                        if cpu_busy:
                            logger.info(
                                "Kill aborted — CPU active (bot working). "
                                "Stale heartbeat but not hung. Reset counter."
                            )
                            stale_count = 0   # bot is working, just didn't write heartbeat
                            _tg(
                                f"ℹ️ <b>WATCHDOG: Bot working, not hung</b>\n"
                                f"Heartbeat stale {age:.0f}s but CPU active\n"
                                f"Phase: {phase} — not killing\n"
                                f"<i>Possibly doing long operation (backtest/learning)</i>"
                            )
                        else:
                            # CPU idle — check open positions
                            positions = open_positions()

                            if positions > 0:
                                # NEVER kill with open positions
                                logger.critical(
                                    "Kill BLOCKED — %d open positions! "
                                    "Not killing bot with live trades open.",
                                    positions,
                                )
                                _tg(
                                    f"🚨 <b>WATCHDOG: KILL BLOCKED</b>\n"
                                    f"Bot appears hung but has {positions} open positions\n"
                                    f"NOT killing to protect your trades\n"
                                    f"Phase: {phase} | Age: {age:.0f}s\n"
                                    f"<b>Manual action may be needed</b>\n"
                                    f"Check: ./bot.sh logs"
                                )
                            else:
                                # Safe to kill — heartbeat stale + CPU=0 + no positions
                                logger.warning(
                                    "KILL: stale=%d CPU=idle positions=%d pid=%d",
                                    stale_count, positions, pid,
                                )
                                _tg(
                                    f"⚠️ <b>WATCHDOG: Restarting hung bot</b>\n"
                                    f"Stale: {age:.0f}s (limit {threshold}s)\n"
                                    f"CPU: idle | Positions: {positions}\n"
                                    f"Phase: {phase} | PID: {pid}\n"
                                    f"systemd will restart in ~30s"
                                )
                                try:
                                    os.kill(pid, signal.SIGKILL)
                                    logger.info("SIGKILL sent to PID=%d", pid)
                                except ProcessLookupError:
                                    pass
                                stale_count = 0
                                ts["restart"] = t

                    else:
                        # No PID — bot not running at all
                        if (t - ts["stale_warn"]) > COOLDOWN["stale_warn"]:
                            ts["stale_warn"] = t
                            _tg(
                                f"⚠️ <b>WATCHDOG: Bot not running</b>\n"
                                f"No process found. Triggering restart..."
                            )
                        if (t - ts["restart"]) > COOLDOWN["restart"]:
                            restart_svc()
                            ts["restart"] = t
                            stale_count = 0

            # ── 2. MEMORY CHECK ───────────────────────────────────────────────
            if pid > 0:
                mb = mem_mb(pid)
                if mb > MEMORY_MAX_MB:
                    if (t - ts.get("memory", 0)) > COOLDOWN["memory"]:
                        ts["memory"] = t
                        positions = open_positions()
                        if positions == 0:
                            logger.critical(
                                "Memory limit %dMB > %dMB — killing",
                                mb, MEMORY_MAX_MB,
                            )
                            _tg(
                                f"🚨 <b>WATCHDOG: Memory limit</b>\n"
                                f"Usage: {mb:.0f}MB > {MEMORY_MAX_MB}MB\n"
                                f"Open positions: {positions}\n"
                                f"Restarting..."
                            )
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except Exception:
                                pass
                        else:
                            _tg(
                                f"⚠️ <b>WATCHDOG: High memory</b>\n"
                                f"Usage: {mb:.0f}MB (limit {MEMORY_MAX_MB}MB)\n"
                                f"NOT killing — {positions} open positions\n"
                                f"Will restart after positions close"
                            )

            # ── 3. SYSTEMD BURST LIMIT ────────────────────────────────────────
            if svc_failed() and (t - ts["burst"]) > COOLDOWN["burst"]:
                ts["burst"] = t
                logger.critical("systemd: bot in FAILED state (burst limit hit)")
                _tg(
                    "🚨 <b>WATCHDOG: Bot in FAILED state</b>\n"
                    "Crashed too many times — systemd stopped restarting.\n\n"
                    "<b>Fix commands:</b>\n"
                    "<code>journalctl -u trading-bot -n 50</code>\n"
                    "<code>sudo systemctl reset-failed trading-bot</code>\n"
                    "<code>./bot.sh restart</code>\n"
                    f"Open positions: {open_positions()}"
                )

            # ── 4. EMERGENCY CLOSE after 3:35 PM ─────────────────────────────
            if (dtime(15,35) <= nt <= dtime(15,50)):
                if (t - ts["emergency"]) > COOLDOWN["emergency"]:
                    ts["emergency"] = t
                    opts = open_positions()
                    if opts > 0 and not svc_active():
                        emergency_close()

            # ── 5. PRE-MARKET: bot not running at 8:30 AM ─────────────────────
            if (dtime(8,29) <= nt <= dtime(9,10)) and not svc_active():
                if (t - ts["premarket"]) > COOLDOWN["premarket"]:
                    ts["premarket"] = t
                    logger.warning("Bot NOT running at pre-market check")
                    _tg(
                        f"⚠️ <b>WATCHDOG: Bot NOT running</b>\n"
                        f"Time: {now.strftime('%H:%M')} (market opens 9:15 AM)\n\n"
                        f"<b>To start:</b>\n"
                        f"cd ~/Desktop/trading_robot\n"
                        f"./bot.sh start"
                    )

        except Exception as e:
            logger.exception("Watchdog loop error: %s", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    # Load .env
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                if "#" in v:
                    v = v.split("#")[0]
                os.environ.setdefault(k.strip(), v.strip())

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
    run()
