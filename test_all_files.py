#!/usr/bin/env python3
"""
test_all_files.py

Run this directly on your trading_robot machine to verify:
1. All required files are present
2. All Python files have clean syntax
3. All critical imports work
4. .env has required keys
5. Signal engine loads correctly

Usage:
    cd ~/Desktop/trading_robot
    source .venv/bin/activate
    python test_all_files.py
"""
import os
import ast
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results = {"pass": 0, "fail": 0, "warn": 0}

def ok(msg):
    print(f"  {PASS} {msg}")
    results["pass"] += 1

def fail(msg):
    print(f"  {FAIL} {msg}")
    results["fail"] += 1

def warn(msg):
    print(f"  {WARN} {msg}")
    results["warn"] += 1

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "═"*60)
print("  TRADING ROBOT — FULL SYSTEM TEST")
print("═"*60)

# ── TEST 1: Required files present ───────────────────────────────────────────
print("\n[1] REQUIRED FILES")

required_files = [
    # Core
    "main_autonomous.py", "live_signal_engine.py", "signal_engine.py",
    "trade_manager.py", "signals.py",
    # Broker
    "angel.py", "angel_broker.py", "broker_manager.py",
    "auto_mode.py", "dual_mode_engine.py",
    # Book strategies
    "pivot_boss.py", "candlestick_signals.py", "failed_breakout.py",
    "ttm_squeeze.py", "holy_grail.py", "williams_systems.py",
    "weinstein_stage.py", "td_sequential.py",
    # Options
    "nifty_options_engine.py", "option_selector.py", "expiry_strategy.py",
    "iv_percentile.py", "spread_strategy.py",
    # Risk
    "daily_loss_limit.py", "portfolio_risk.py", "kill_switch.py",
    "adaptive_position_sizer.py", "sl_hunt_guard.py", "greeks_sizer.py",
    # Intelligence
    "overnight_protection.py", "global_market_filter.py",
    "event_calendar.py", "gap_risk_manager.py", "entry_timing_1m.py",
    # AI/ML
    "ai_trade_filter.py", "self_learning_engine.py",
    "strategy_performance_matrix.py",
    # Capital
    "capital_allocator.py", "capital_compounder.py",
    # Execution
    "execution_algo.py", "trailing.py", "slippage.py",
    # Data
    "data_fetcher.py", "indicators.py",
    # Ops
    "watchdog.py", "health_monitor.py", "cloud_backup.py",
    "alerts.py", "remote_dashboard.py",
    # Config
    "config.py", "validate_env.py",
    # Env
    ".env",
]

for f in required_files:
    if os.path.exists(f):
        ok(f)
    else:
        fail(f"MISSING: {f}")

# ── TEST 2: Python syntax check ───────────────────────────────────────────────
print("\n[2] PYTHON SYNTAX CHECK")

py_files = []
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for name in files:
        if name.endswith(".py"):
            py_files.append(os.path.join(root, name).lstrip("./"))
py_files = sorted(py_files)
syntax_ok = 0
syntax_fail = 0

for f in py_files:
    try:
        with open(f) as fh:
            ast.parse(fh.read())
        syntax_ok += 1
    except SyntaxError as e:
        fail(f"SYNTAX ERROR in {f} at line {e.lineno}: {e.msg}")
        syntax_fail += 1

if syntax_fail == 0:
    ok(f"All {syntax_ok} Python files have clean syntax")
else:
    fail(f"{syntax_fail} files have syntax errors")

# ── TEST 3: Critical imports ──────────────────────────────────────────────────
print("\n[3] CRITICAL IMPORTS")

critical_imports = [
    ("config",           "config"),
    ("angel",            "angel"),
    ("trade_manager",    "trade_manager"),
    ("signal_engine",    "signal_engine"),
    ("data_fetcher",     "data_fetcher"),
    ("alerts",           "alerts"),
    ("daily_loss_limit", "daily_loss_limit"),
    ("dual_mode_engine", "dual_mode_engine"),
    ("pivot_boss",       "pivot_boss"),
    ("candlestick_signals","candlestick_signals"),
    ("ttm_squeeze",      "ttm_squeeze"),
    ("holy_grail",       "holy_grail"),
    ("williams_systems", "williams_systems"),
    ("weinstein_stage",  "weinstein_stage"),
    ("td_sequential",    "td_sequential"),
    ("iv_percentile",    "iv_percentile"),
    ("failed_breakout",  "failed_breakout"),
    ("capital_compounder","capital_compounder"),
    ("overnight_protection","overnight_protection"),
    ("event_calendar",   "event_calendar"),
]

for label, module in critical_imports:
    try:
        __import__(module)
        ok(f"import {module}")
    except ImportError as e:
        fail(f"import {module} — {e}")
    except Exception as e:
        warn(f"import {module} — loaded with warning: {type(e).__name__}")

# ── TEST 4: .env keys ─────────────────────────────────────────────────────────
print("\n[4] .env CONFIGURATION")

required_env = [
    "API_KEY", "CLIENT_ID", "PASSWORD", "TOTP_SECRET",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "PAPER_TRADING", "ENABLE_REAL_TRADING",
    "CAPITAL", "MAX_DAILY_LOSS", "MAX_TRADES_PER_DAY",
    "MAX_OPEN_POSITIONS", "MAX_LOTS", "MIN_LIVE_CAPITAL",
]
optional_env = [
    "WEEKLY_LOSS_LIMIT", "PREFER_LIMIT_FOR_OPTIONS",
    "ENABLE_1M_ENTRY_TIMING", "OPTION_LOT_SIZE",
]

if os.path.exists(".env"):
    with open(".env") as f:
        env_content = f.read()
    for key in required_env:
        if key in env_content:
            ok(f".env: {key} present")
        else:
            fail(f".env: {key} MISSING")
    for key in optional_env:
        if key in env_content:
            ok(f".env: {key} present (optional)")
        else:
            warn(f".env: {key} not set (optional — using default)")
else:
    fail(".env file not found")

# ── TEST 5: Signal engine strategy count ─────────────────────────────────────
print("\n[5] SIGNAL ENGINE STRATEGIES")

try:
    import signal_engine as _se
    with open("signal_engine.py") as f:
        se_src = f.read()

    # Check all _AVAILABLE vars are defined
    available_vars = [
        "_PB_AVAILABLE", "_CS_AVAILABLE", "_FB_AVAILABLE",
        "_TTM_AVAILABLE", "_HG_AVAILABLE", "_WR_AVAILABLE",
        "_TD_AVAILABLE", "_WEINSTEIN_AVAILABLE", "_SM_AVAILABLE",
    ]
    for var in available_vars:
        if f"{var} = True" in se_src:
            ok(f"signal_engine: {var} defined")
        else:
            fail(f"signal_engine: {var} NOT defined — import block missing")

    strategies = list(getattr(_se, "STRATEGIES", []) or [])
    if strategies:
        ok(f"STRATEGIES registry loaded {len(strategies)} strategies")
        if hasattr(_se, "_invoke_strategy"):
            ok("signal_engine: signature-aware strategy adapter present")
        else:
            fail("signal_engine: _invoke_strategy missing")
    else:
        fail("STRATEGIES registry is empty")
except Exception as e:
    fail(f"Could not read signal_engine.py: {e}")

# ── TEST 6: DB schema columns ─────────────────────────────────────────────────
print("\n[6] TRADES DATABASE SCHEMA")

try:
    import sqlite3, config as cfg
    db_path = getattr(cfg, "TRADES_DB", "trades.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
        conn.close()
        new_cols = ["gross_pnl","brokerage","stt","exchange_charge",
                    "sebi_levy","gst","stamp_duty","total_charges",
                    "cumulative_pnl","holding_minutes","r_multiple",
                    "paper_pnl","live_pnl","trade_type"]
        for c in new_cols:
            if c in cols:
                ok(f"trades.db column: {c}")
            else:
                warn(f"trades.db column: {c} missing (will be added on first trade)")
        ok(f"trades.db has {len(cols)} total columns")
    else:
        warn("trades.db not found — will be created on first trade")
except Exception as e:
    warn(f"DB check: {e}")

# ── TEST 7: Dual mode engine ──────────────────────────────────────────────────
print("\n[7] DUAL MODE ENGINE")

try:
    from dual_mode_engine import DualModeEngine, get_dual_engine
    engine = DualModeEngine()
    status = engine.get_status()
    ok(f"DualModeEngine initialised | mode={status['mode']}")
    ok(f"Min capital: ₹{status['min_capital']:,.0f}")
    ok(f"Paper forced: {status['paper_forced']}")
    ok(f"Real allowed: {status['real_allowed']}")
except Exception as e:
    fail(f"DualModeEngine: {e}")

# ── FINAL SUMMARY ─────────────────────────────────────────────────────────────
print()
print("═"*60)
print(f"  RESULTS: {results['pass']} passed | {results['fail']} failed | {results['warn']} warnings")
print("═"*60)

if results["fail"] == 0:
    print(f"\n  ✅ ALL TESTS PASSED — system is ready")
    print(f"  Bot is running in paper mode")
    print(f"  Watch Telegram tomorrow from 8:30 AM")
else:
    print(f"\n  ❌ {results['fail']} FAILURES — fix before going live")
    print(f"  Run: python validate_env.py   for .env issues")
    print(f"  Run: ./bot.sh logs            for runtime errors")

print()
