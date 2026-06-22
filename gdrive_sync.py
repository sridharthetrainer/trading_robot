"""
gdrive_sync.py — Bidirectional Google Drive ↔ System Sync

BIDIRECTIONAL:
  System → Drive:  code files, trades, backtest results, config
  Drive  → System: updated .py files, new config — auto deploys

TRIGGERS:
  Auto:    Every 5 min during market hours / every 30 min off-hours
  Manual:  /sync on Telegram
  Startup: Pulls latest from Drive before bot initialises
  On save: Any .py file change pushes to Drive immediately

FOLDER STRUCTURE (Google Drive):
  trading_robot/
  ├── code/           ← .py files ONLY (no venv, no __pycache__)
  ├── data/
  │   ├── trades.db          ← your trade history
  │   ├── signal_log.csv     ← ML training data
  │   ├── score_calibration.json
  │   └── backtest_results/
  ├── config/
  │   ├── .env               ← edit config remotely
  │   └── nifty200.csv       ← custom symbol list
  └── reports/
      ├── backtest/
      └── weekly/

  NOT synced (install locally from requirements.txt):
  ✗ venv/  ✗ __pycache__/  ✗ *.so  ✗ node_modules/

REMOTE WORKFLOW (from anywhere):
  1. Open Google Drive on phone/laptop
  2. Edit a .py file in trading_robot/code/
  3. Bot detects change within 5 min → auto-deploys → restarts

SETUP (one-time):
  sudo apt install rclone
  rclone config   ← follow prompts, name the remote "gdrive"
  Add to .env: GDRIVE_REMOTE=gdrive  GDRIVE_FOLDER=trading_robot
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────

# Files where LOCAL is authoritative — never PULL them from Drive (push-only),
# even if Drive's copy is newer. Was referenced in the sync loop but never defined
# → NameError crashed the sync. Empty = no push-only protection (standard sync);
# add filenames here to stop Drive from overwriting your local copy.
_PUSH_ONLY: set = set()

# ── CRITICAL: WHITELIST ───────────────────────────────────────────────────────
# Only these files are allowed to be pulled from Drive.
# Prevents venv/stdlib files from being synced and corrupting the system.
_ALLOWED_PY_FILES = {
    'adaptive_position_sizer.py','advanced_confluence.py','advanced_strategies.py',
    'ai_trade_filter.py','alerts.py','angel.py','angel_broker.py','angel_option_chain.py',
    'auto_mode.py','auto_strategy_selector.py','autonomous_backtest.py','backtest.py',
    'backtest_5min_ema.py','backtest_breakout.py','backtest_breakout_grid.py',
    'backtest_iron_condor.py','backtest_ma_cross.py','backtest_ma_grid.py',
    'backtest_mean_reversion.py','backtest_mr_enhanced.py','backtest_mr_grid.py',
    'backtest_mr_validate.py','backtest_orb.py','backtest_scalping.py',
    'backtest_supertrend_mtf.py','backtest_trend.py','backtest_trend_grid.py',
    'backtest_vwap_reversion.py','bhav_copy.py','broker_interface.py',
    'broker_manager.py','bse_option_chain.py','bulk_deals.py',
    'candlestick_patterns.py','candlestick_signals.py',
    'capital_allocator.py','capital_compounder.py','capital_recycler.py',
    'chart_patterns.py','check_connections.py','cloud_backup.py','config.py',
    'connection_monitor.py','corporate_actions.py','cross_asset.py',
    'daily_loss_limit.py','dashboard.py','dashboard_server.py',
    'data_download_tracker.py','data_fetcher.py','data_pool.py','data_sources.py',
    'day_classifier.py','dual_mode_engine.py','entry_optimizer.py',
    'entry_timing_1m.py','event_calendar.py','execution_algo.py',
    'execution_monitor.py','expiry_regime.py','expiry_strategy.py',
    'failed_breakout.py','feature_importance.py','fii_tracker.py','fno_ban_list.py',
    'gap_risk_manager.py','gdrive_sync.py','github_sync.py','global_market_filter.py',
    'greeks_sizer.py','health_monitor.py','holy_grail.py','idle_engine.py',
    'index_rebalancing.py','indicators.py','institutional_alpha.py',
    'institutional_indicators.py','institutional_strategies.py','intraday_profile.py',
    'indicator_confluence.py','iv_percentile.py','kill_switch.py',
    'live_signal_engine.py','logger_setup.py',
    'lstm_model.py','main_autonomous.py','market_context.py',
    'market_context_builder.py','market_data_feeds.py',
    'market_regime.py','master_contract.py','mean_reversion_signal.py','mtf.py',
    'news_nlp.py','nifty_options_engine.py','nse_master.py','off_hours_engine.py',
    'mtf_context.py','oi_strike_builder.py','oi_tracker.py','option_chain_engine.py',
    'option_chain_fetcher.py','option_chain_intelligence.py','option_intelligence.py',
    'option_oi_intelligence.py','option_selector.py','orb_strategy.py',
    'overnight_protection.py','param_bridge.py','participant_oi.py','pivot_boss.py',
    'pivot_scalping_strategy.py','portfolio_heat.py','portfolio_risk.py','price_structure.py','quant_models.py',
    'quote_cache.py','regime.py','remote_dashboard.py','run_backtest.py',
    'scale_in_manager.py','score_calibrator.py','self_healing.py','self_learning.py',
    'self_learning_engine.py','setup_gdrive_backup.py','signal_engine.py',
    'signal_log.py','signal_score.py','signals.py','sl_hunt_guard.py','slippage.py',
    'smart_order_router.py','spread_strategy.py','standalone_runner.py',
    'strategy_evolution.py','strategy_performance_matrix.py','strategy_scanner.py',
    'strategy_selector.py','strike_pcr.py','supertrend_mtf_strategy.py',
    'system_monitor.py','system_state.py','td_sequential.py','telegram_backup.py',
    'telegram_commands.py','test_all_files.py','test_system.py','theta_strategy.py',
    'three_confirm.py','time_regime.py','trade_manager.py','trade_rationale.py',
    'trading_agent.py','tradingview_strategies.py','trailing.py','triple_barrier.py',
    'ttm_squeeze.py','utils.py','validate_env.py','value_at_risk.py',
    'volume_profile_advanced.py','vwap_reversion_strategy.py','walk_forward_backtest.py',
    'watchdog.py','websocket_engine.py','weinstein_stage.py','whale_tracker.py',
    'williams_systems.py',
}
# ─────────────────────────────────────────────────────────────────────────────
_BOT_DIR       = Path(__file__).parent
_REMOTE        = os.getenv("GDRIVE_REMOTE",  "gdrive")
_FOLDER        = os.getenv("GDRIVE_FOLDER",  "trading_robot")
_SYNC_INTERVAL = 300   # 5 min during market hours
_SYNC_OFF_HRS  = 1800  # 30 min off-hours
_STATE_FILE    = _BOT_DIR / "sync_state.json"
_LOCK_FILE     = _BOT_DIR / ".sync_lock"

# What to sync each direction
_CODE_EXTS   = {".py"}
_DATA_EXTS   = {".db", ".csv", ".json"}
_CONFIG_EXTS = {".env"}

_GDRIVE_PATHS = {
    "code":    f"{_REMOTE}:{_FOLDER}/code",
    "data":    f"{_REMOTE}:{_FOLDER}/data",
    "config":  f"{_REMOTE}:{_FOLDER}/config",
    "reports": f"{_REMOTE}:{_FOLDER}/reports",
}


def _rclone_available() -> bool:
    try:
        r = subprocess.run(["which", "rclone"], capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _run_rclone(args: list, timeout: int = 60) -> Tuple[bool, str]:
    """Run rclone command. Returns (success, output)."""
    try:
        r = subprocess.run(
            ["rclone"] + args,
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode == 0, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# PUSH: System → Google Drive
# ─────────────────────────────────────────────────────────────────────────────
def push_to_drive(
    what: str = "all",   # "code" | "data" | "config" | "all"
    alerts=None,
) -> Dict:
    """
    Push local files to Google Drive.
    Safe — never overwrites Drive if Drive file is newer.
    """
    if not _rclone_available():
        return {"ok": False, "error": "rclone not installed"}

    results = {}

    # ── WHAT WE SYNC ──────────────────────────────────────────────────────
    # ✅ .py files (our code)
    # ✅ .db .csv .json (trade data, signals, calibration)
    # ✅ .env (config)
    # ✅ .log (recent logs)
    # ❌ venv/  (pip install from requirements.txt)
    # ❌ node_modules/  (npm install)
    # ❌ __pycache__/  (auto-generated)
    # ❌ *.so *.pyc *.egg  (compiled/binary)
    # ❌ requirements.txt, package.json (setup files — not needed in sync)
    # ─────────────────────────────────────────────────────────────────────

    if what in ("code", "all"):
        # SAFE push: only .py files that exist directly in BOT_DIR
        # Never follow symlinks (prevents venv/stdlib being uploaded)
        ok, out = _run_rclone([
            "copy", str(_BOT_DIR),
            _GDRIVE_PATHS["code"],
            "--include", "*.py",
            "--exclude", "venv/**",
            "--exclude", ".venv/**",
            "--exclude", "env/**",
            "--exclude", "__pycache__/**",
            "--exclude", "*.pyc",
            "--exclude", "*.pyo",
            "--exclude", "*.so",
            "--exclude", "*.egg-info/**",
            "--exclude", "*.egg/**",
            "--exclude", "node_modules/**",
            "--exclude", ".git/**",
            "--exclude", "lib/**",
            "--exclude", "lib64/**",
            "--exclude", "include/**",
            "--exclude", "share/**",
            "--exclude", "bin/**",
            "--exclude", "venv/**",
            "--exclude", ".venv/**",
            "--exclude", "lib/**",
            "--exclude", "lib64/**",
            "--exclude", "bin/**",
            "--exclude", "include/**",
            "--exclude", "**/__pycache__/**",
            "--update",
            "--transfers", "4",
        ])
        results["code"] = {"ok": ok, "detail": "synced" if ok else out[:80]}
        logger.info("Drive push code: %s", "✅" if ok else f"❌ {out[:60]}")

    if what in ("data", "all"):
        # Trade data, signal log, backtest results, calibration
        data_files = [
            "trades.db",
            "signal_log.csv",
            "score_calibration.json",
            "oi_tracker_state.json",
            "regime_state.json",
            "strategy_evolution_state.json",
            "sync_state.json",
        ]
        for fname in data_files:
            fpath = _BOT_DIR / fname
            if fpath.exists():
                ok, out = _run_rclone([
                    "copy", str(fpath),
                    _GDRIVE_PATHS["data"],
                    "--update",
                ])
                results[fname] = {"ok": ok}
        # Also sync backtest results folder if it exists
        bt_dir = _BOT_DIR / "backtest_results"
        if bt_dir.exists():
            ok, _ = _run_rclone([
                "copy", str(bt_dir),
                f"{_GDRIVE_PATHS['reports']}/backtest",
                "--include", "*.json",
                "--include", "*.csv",
                "--update",
            ])
            results["backtest_results"] = {"ok": ok}

    if what in ("config", "all"):
        # Only .env — NOT requirements.txt (that stays local for pip install)
        env_path = _BOT_DIR / ".env"
        if env_path.exists():
            ok, out = _run_rclone([
                "copy", str(env_path),
                _GDRIVE_PATHS["config"],
                "--update",
            ])
            results["env"] = {"ok": ok}
        # Also sync nifty200.csv (symbol list — may be customised)
        n200 = _BOT_DIR / "nifty200.csv"
        if n200.exists():
            ok, _ = _run_rclone([
                "copy", str(n200),
                _GDRIVE_PATHS["config"],
                "--update",
            ])
            results["nifty200.csv"] = {"ok": ok}

    # Save last push timestamp
    _save_state({"last_push": datetime.now().isoformat(), "results": results})
    return {"ok": all(v.get("ok", False) for v in results.values()), "results": results}


# ─────────────────────────────────────────────────────────────────────────────
# PULL: Google Drive → System (auto-deploy)
# ─────────────────────────────────────────────────────────────────────────────
def pull_from_drive(
    what: str = "code",
    auto_restart: bool = True,
    alerts=None,
) -> Dict:
    """
    Pull files from Google Drive to local system.

    For CODE: if any .py file is newer on Drive → pull + restart bot.
    For CONFIG: pull .env changes → restart to apply.

    Uses --update flag: only downloads if Drive file is NEWER.
    """
    if not _rclone_available():
        return {"ok": False, "error": "rclone not installed. Run: sudo apt install rclone"}

    # Prevent concurrent syncs
    if _LOCK_FILE.exists():
        return {"ok": False, "error": "Sync already in progress"}

    try:
        _LOCK_FILE.touch()
        results = {}
        files_updated = []

        if what in ("code", "all"):
            # Check what's newer on Drive before pulling
            ok_check, out_check = _run_rclone([
                "check",
                _GDRIVE_PATHS["code"],
                str(_BOT_DIR),
                "--include", "*.py",
            ])
            # rclone check returns list of differences
            drive_newer = _parse_rclone_differences(out_check)

            if drive_newer:
                ok, out = _run_rclone([
                    "copy",
                    _GDRIVE_PATHS["code"],
                    str(_BOT_DIR),
                    "--include", "*.py",
                    "--update",        # only if Drive is newer
                    "--transfers", "4",
                ])
                results["code"] = {"ok": ok, "files": drive_newer}
                files_updated.extend(drive_newer)
                logger.info("Pulled from Drive: %s", drive_newer)

        if what in ("config", "all"):
            ok, out = _run_rclone([
                "copy",
                _GDRIVE_PATHS["config"],
                str(_BOT_DIR),
                "--include", ".env",
                "--update",
            ])
            results["config"] = {"ok": ok}
            if ok and ".env" in out:
                files_updated.append(".env")

        # Alert and restart if files changed
        if files_updated:
            logger.info("Drive pull: %d files updated", len(files_updated))
            if alerts:
                alerts.send(
                    f"📥 <b>DRIVE SYNC: {len(files_updated)} files updated</b>\n"
                    f"  {', '.join(files_updated[:5])}\n"
                    f"  Restarting bot to apply...\n"
                    f"🕐 {datetime.now().strftime('%H:%M')}"
                )
            if auto_restart:
                # Restart via systemd (non-blocking)
                threading.Thread(
                    target=_delayed_restart, args=(3,), daemon=True
                ).start()

        _save_state({"last_pull": datetime.now().isoformat(),
                     "files_updated": files_updated})
        return {"ok": True, "files_updated": files_updated, "results": results}

    finally:
        try: _LOCK_FILE.unlink()
        except Exception: pass


def _parse_rclone_differences(output: str) -> List[str]:
    """Parse rclone check output to find files that differ."""
    import re
    # rclone check outputs lines like: "ERROR : filename.py: sizes differ"
    files = re.findall(r'ERROR\s*:\s*([\w_\.]+\.py)', output)
    return list(set(files))


def _delayed_restart(delay_sec: int = 3) -> None:
    """Restart bot via systemd after delay."""
    time.sleep(delay_sec)
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "trading-bot.service"],
            timeout=10
        )
    except Exception:
        # Fallback: touch restart flag
        (_BOT_DIR / ".restart_requested").touch()


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND WATCHER — watches Drive for changes every 5 min
# ─────────────────────────────────────────────────────────────────────────────
def smart_bidirectional_sync(alerts=None) -> dict:
    """
    Smart sync — compares timestamps file by file.
    
    For each .py file:
      - New on Drive only  → pull to system
      - New on system only → push to Drive  
      - Exists both sides  → check mtime, sync NEWER version only
    
    Uses rclone check --combined to get per-file status, then
    copies only the files that need updating in each direction.
    """
    if not _rclone_available():
        return {"pushed": [], "pulled": [], "error": "rclone not installed"}

    pushed = []
    pulled = []
    conflicts = []

    try:
        # rclone check --combined gives per-file status:
        # = identical  < local newer  > remote newer  + local only  - remote only
        ok, out = _run_rclone([
            "check",
            str(_BOT_DIR),
            _GDRIVE_PATHS["code"],
            "--include", "*.py",
            "--exclude", "venv/**",
            "--exclude", ".venv/**",
            "--exclude", "**/__pycache__/**",
            "--exclude", "lib/**",
            "--combined", "-",
        ], timeout=60)

        for line in out.strip().splitlines():
            if not line or len(line) < 3: continue
            status = line[0]   # = < > + -
            fname  = line[2:].strip()

            if status == "=":
                pass  # identical — skip

            elif status == "<":
                # Local is NEWER → push to Drive
                local_path = _BOT_DIR / fname
                if local_path.exists() and fname in _ALLOWED_PY_FILES:
                    ok2, _ = _run_rclone([
                        "copy", str(local_path),
                        _GDRIVE_PATHS["code"],
                    ])
                    if ok2:
                        pushed.append(fname)
                        logger.debug("Pushed newer local: %s", fname)

            elif status == ">":
                # Drive is NEWER → pull ONLY if in whitelist
                if fname in _PUSH_ONLY:
                    logger.info("PUSH-ONLY file, skipping pull: %s", fname)
                    continue
                if fname not in _ALLOWED_PY_FILES:
                    logger.warning("BLOCKED pull of non-whitelisted: %s", fname)
                    continue
                ok2, _ = _run_rclone([
                    "copy",
                    f"{_GDRIVE_PATHS['code']}/{fname}",
                    str(_BOT_DIR),
                ])
                if ok2:
                    pulled.append(fname)
                    logger.debug("Pulled newer Drive: %s", fname)

            elif status == "+":
                # File only on local → push to Drive
                local_path = _BOT_DIR / fname
                if local_path.exists():
                    ok2, _ = _run_rclone([
                        "copy", str(local_path),
                        _GDRIVE_PATHS["code"],
                    ])
                    if ok2:
                        pushed.append(fname)
                        logger.debug("Pushed new file: %s", fname)

            elif status == "-":
                # File only on Drive → pull ONLY if in whitelist
                if fname in _PUSH_ONLY:
                    continue
                if fname not in _ALLOWED_PY_FILES:
                    logger.warning("BLOCKED pull of non-whitelisted: %s", fname)
                    continue
                ok2, _ = _run_rclone([
                    "copy",
                    f"{_GDRIVE_PATHS['code']}/{fname}",
                    str(_BOT_DIR),
                ])
                if ok2:
                    pulled.append(fname)
                    logger.debug("Pulled new file: %s", fname)

    except Exception as e:
        logger.debug("smart_bidirectional_sync: %s", e)

    # Also sync data files (always push newer)
    data_files = [
        "trades.db", "signal_log.csv", "score_calibration.json",
        "oi_tracker_state.json", "regime_state.json", ".env",
    ]
    for fname in data_files:
        fpath = _BOT_DIR / fname
        if not fpath.exists(): continue
        try:
            ok2, out2 = _run_rclone([
                "check", str(fpath),
                _GDRIVE_PATHS["data"],
            ])
            if "<" in out2 or "+" in out2 or not ok2:
                # Local is newer or new
                ok3, _ = _run_rclone(["copy", str(fpath), _GDRIVE_PATHS["data"]])
                if ok3: pushed.append(fname)
        except Exception: pass

    _save_state({
        "last_smart_sync": datetime.now().isoformat(),
        "pushed": pushed, "pulled": pulled,
    })

    if alerts and (pushed or pulled):
        alerts.send(
            f"☁️ <b>SMART SYNC DONE</b>\n"
            f"  ↑ Pushed {len(pushed)} newer to Drive\n"
            f"  ↓ Pulled {len(pulled)} newer from Drive\n"
            + (f"  Files: {', '.join((pushed+pulled)[:4])}\n" if pushed or pulled else "")
            + f"🕐 {datetime.now().strftime('%H:%M')}"
        )

    return {"pushed": pushed, "pulled": pulled, "conflicts": conflicts}


class DriveSyncWatcher:
    """
    Background thread — polls Google Drive every 5 min.
    If it finds .py files newer than local → auto-pulls and restarts.
    """

    def __init__(self, alerts=None) -> None:
        self.alerts   = alerts
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not _rclone_available():
            logger.info("rclone not installed — Drive sync disabled")
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="DriveSyncWatcher"
        )
        self._thread.start()
        logger.info("Google Drive sync watcher started")

    def _loop(self) -> None:
        # Initial smart sync on startup
        time.sleep(30)  # wait for bot to fully initialise
        smart_bidirectional_sync(alerts=self.alerts)

        while self._running:
            try:
                from datetime import time as dtime
                now_t = datetime.now().time()
                mkt   = dtime(9, 0) <= now_t <= dtime(15, 45)
                sleep = _SYNC_INTERVAL if mkt else _SYNC_OFF_HRS

                # Smart sync — compares timestamps, syncs only newer version
                result = smart_bidirectional_sync(alerts=None)  # silent background
                if result.get("pulled"):
                    logger.info("Auto-pulled %d newer files from Drive",
                                len(result["pulled"]))
                    # Restart if code files changed
                    if any(f.endswith(".py") for f in result["pulled"]):
                        if self.alerts:
                            self.alerts.send(
                                f"📥 Drive sync: {len(result['pulled'])} files updated\n"
                                f"  {chr(44).join(result['pulled'][:3])}\n"
                                f"  Restarting to apply...\n"
                                f"🕐 {datetime.now().strftime('%H:%M')}")
                        _delayed_restart(3)

                time.sleep(sleep)

            except Exception as e:
                logger.debug("DriveSyncWatcher: %s", e)
                time.sleep(60)

    def stop(self) -> None:
        self._running = False

    def sync_now(self, direction: str = "both", alerts=None) -> Dict:
        """
        Smart bidirectional sync.
        For every existing file: compares timestamps, syncs ONLY the newer version.
        For new files: syncs in the appropriate direction.
        """
        results = smart_bidirectional_sync(alerts=alerts)
        pushed = results.get("pushed", [])
        pulled = results.get("pulled", [])
        return {
            "ok":      True,
            "pushed":  len(pushed) > 0,
            "pulled":  pulled,
            "summary": f"↑ {len(pushed)} to Drive  ↓ {len(pulled)} from Drive"
        }

    def status(self) -> str:
        state = _load_state()
        last_push = state.get("last_push", "Never")
        last_pull = state.get("last_pull", "Never")
        files_updated = state.get("files_updated", [])
        avail = "✅ rclone ready" if _rclone_available() else "❌ rclone not installed"
        return (
            f"☁️ <b>DRIVE SYNC STATUS</b>\n"
            f"  rclone:     {avail}\n"
            f"  Remote:     {_REMOTE}:{_FOLDER}\n"
            f"  Last push:  {last_push[:16] if last_push != 'Never' else 'Never'}\n"
            f"  Last pull:  {last_pull[:16] if last_pull != 'Never' else 'Never'}\n"
            f"  Last files: {', '.join(files_updated[:3]) if files_updated else 'none'}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SETUP GUIDE (printed on first run)
# ─────────────────────────────────────────────────────────────────────────────
SETUP_GUIDE = """
╔══════════════════════════════════════════════════════╗
║   GOOGLE DRIVE SYNC — ONE-TIME SETUP (5 minutes)     ║
╚══════════════════════════════════════════════════════╝

Step 1: Install rclone
  sudo apt install rclone

Step 2: Configure Google Drive
  rclone config
  → n (new remote)
  → Name: gdrive
  → Type: 17 (Google Drive)
  → client_id: [press Enter — use default]
  → client_secret: [press Enter]
  → scope: 1 (full access)
  → root_folder_id: [press Enter]
  → service_account: [press Enter]
  → y (auto config — opens browser)
  → Login with your Google account
  → y (this is ok)
  → n (not shared drive)
  → y (confirm)
  → q (quit)

Step 3: Test it works
  rclone ls gdrive:
  (should list your Google Drive files)

Step 4: Add to .env
  GDRIVE_REMOTE=gdrive
  GDRIVE_FOLDER=trading_robot

Step 5: Restart bot
  ./bot.sh restart

Done! Bot will:
  • Upload ALL code to Drive/trading_robot/code/ on startup
  • Check Drive every 5 min for updates you made remotely
  • Auto-restart when it finds newer code on Drive
  • Send Telegram alert: "DRIVE SYNC: 3 files updated, restarting"

REMOTE EDITING WORKFLOW:
  1. Open Google Drive on phone/laptop
  2. Navigate to trading_robot/code/
  3. Edit any .py file (or upload a new version)
  4. Wait 5 min — bot auto-deploys
  5. OR send /deploy on Telegram for instant deploy
"""


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────
def _save_state(data: dict) -> None:
    try:
        existing = _load_state()
        existing.update(data)
        _STATE_FILE.write_text(json.dumps(existing, indent=2))
    except Exception: pass

def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text())
    except Exception: pass
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────
_watcher: Optional[DriveSyncWatcher] = None

def get_drive_sync(alerts=None) -> DriveSyncWatcher:
    global _watcher
    if _watcher is None:
        _watcher = DriveSyncWatcher(alerts=alerts)
    return _watcher
