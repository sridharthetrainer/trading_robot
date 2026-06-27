"""
validate_env.py

Validates your .env file before starting the trading bot.
Run this anytime to check if everything is configured correctly.

Usage:
    python validate_env.py           # full check
    python validate_env.py --fix     # show exact lines to add to .env
    python validate_env.py --live    # extra checks for live trading

Output:
    ✅ PASS   — value is set and valid
    ⚠️  WARN   — optional but recommended
    ❌ FAIL   — required, missing or invalid
"""

import os
import sys
import re
from pathlib import Path
from datetime import datetime

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"; RED  = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD = "\033[1m";  RESET  = "\033[0m"

def ok(msg):   return f"{GREEN}✅ PASS{RESET}  {msg}"
def fail(msg): return f"{RED}❌ FAIL{RESET}  {msg}"
def warn(msg): return f"{YELLOW}⚠️  WARN{RESET}  {msg}"
def info(msg): return f"{CYAN}ℹ️  INFO{RESET}  {msg}"


# ── Load .env ─────────────────────────────────────────────────────────────────
def load_env(path: str = ".env") -> dict:
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            # Strip inline comments
            if "#" in v:
                v = v.split("#")[0]
            env[k.strip()] = v.strip()
    return env


# ── Validation rules ──────────────────────────────────────────────────────────
# Format: (key, level, description, validator_fn, suggested_value)
# level: REQUIRED / RECOMMENDED / OPTIONAL

def not_empty(v):      return bool(v and v.strip())
def is_bool(v):        return v.lower() in ("true", "false", "1", "0", "yes", "no")
def is_positive(v):
    try: return float(v) > 0
    except: return False
def is_nonneg(v):
    try: return float(v) >= 0
    except: return False
def is_int_pos(v):
    try: return int(v) > 0
    except: return False
def is_pct(v):
    try: return 0 < float(v) <= 1
    except: return False

RULES = [
    # ── ANGEL ONE CREDENTIALS (absolutely required) ────────────────────────
    ("API_KEY",       "REQUIRED",     "Angel One API key",
     not_empty,       "Get from angelone.in > My Profile > API Access"),

    ("CLIENT_ID",     "REQUIRED",     "Angel One client ID (e.g. A123456)",
     not_empty,       "Your Angel One login ID"),

    ("PASSWORD",      "REQUIRED",     "Angel One login password",
     not_empty,       "Your Angel One password"),

    ("TOTP_SECRET",   "REQUIRED",     "TOTP secret for 2FA (base32 string)",
     not_empty,       "From Angel One authenticator setup — NOT the 6-digit OTP"),

    # ── TELEGRAM (required for alerts) ────────────────────────────────────
    ("TELEGRAM_BOT_TOKEN", "REQUIRED", "Telegram bot token from @BotFather",
     not_empty,        "Create bot at t.me/BotFather, copy the token"),

    ("TELEGRAM_CHAT_ID",   "REQUIRED", "Your Telegram chat/user ID",
     not_empty,        "Message @userinfobot on Telegram to get your ID"),

    ("TELEGRAM_ENABLED",   "RECOMMENDED", "Enable Telegram alerts (true/false)",
     is_bool,          "true"),

    # ── TRADING MODE ───────────────────────────────────────────────────────
    ("PAPER_TRADING",      "REQUIRED",     "Paper mode (true=safe, false=real money)",
     is_bool,          "true"),

    ("ENABLE_REAL_TRADING","REQUIRED",     "Enable real order placement",
     is_bool,          "false"),

    ("AUTO_MODE_SWITCH",   "RECOMMENDED", "Auto switch paper↔live based on balance",
     is_bool,          "true"),

    ("MIN_LIVE_CAPITAL",   "RECOMMENDED", "Minimum balance to activate live trading (₹)",
     is_positive,      "25000"),

    # ── CAPITAL ───────────────────────────────────────────────────────────
    ("CAPITAL",           "REQUIRED",     "Total capital for trading (₹)",
     is_positive,      "100000"),

    ("PAPER_CAPITAL",     "RECOMMENDED", "Simulated capital for paper trading (₹)",
     is_positive,      "100000"),

    ("REAL_CAPITAL",      "RECOMMENDED", "Fallback capital if Angel One API fails (₹)",
     is_positive,      "100000"),

    # ── RISK LIMITS ────────────────────────────────────────────────────────
    ("MAX_DAILY_LOSS",    "REQUIRED",     "Hard daily loss limit (₹)",
     is_positive,      "3000"),

    ("SOFT_DAILY_LOSS_LIMIT", "RECOMMENDED", "Soft warning before hard limit (₹)",
     is_positive,      "2000"),

    ("MAX_OPEN_POSITIONS","REQUIRED",     "Max concurrent open trades",
     is_int_pos,       "2"),

    ("MAX_LOTS",          "REQUIRED",     "Max lots per trade",
     is_int_pos,       "10"),

    ("RISK_PER_TRADE_PCT","RECOMMENDED", "Risk per trade as fraction (0.01 = 1%)",
     is_pct,           "0.01"),

    ("MAX_CONSECUTIVE_LOSSES","RECOMMENDED","Stop after N consecutive losses",
     is_int_pos,       "3"),

    ("MAX_TRADES_PER_DAY","RECOMMENDED", "Maximum trades per day",
     is_int_pos,       "10"),

    # ── OPTION SETTINGS ────────────────────────────────────────────────────
    ("OPTION_LOT_SIZE",   "REQUIRED",     "Default lot size (NIFTY=75)",
     is_int_pos,       "75"),

    ("VIX_MAX_FOR_BUYING","RECOMMENDED", "Block option buying above this VIX",
     is_positive,      "22.0"),

    ("SWING_MODE_ENABLED","RECOMMENDED", "Enable multi-day swing trades (true/false)",
     is_bool,          "true"),

    ("SWING_MIN_SCORE",   "RECOMMENDED", "Minimum score for swing trades",
     is_positive,      "7.0"),

    ("SWING_MIN_CONFIDENCE","RECOMMENDED","Min confidence for swing trades (0-1)",
     is_pct,           "0.75"),

    ("SWING_MIN_DTE",     "RECOMMENDED", "Minimum days to expiry for swing",
     is_int_pos,       "5"),

    # ── AI / ML ────────────────────────────────────────────────────────────
    ("AI_FILTER_THRESHOLD","RECOMMENDED","XGBoost min score to trade (0-5)",
     is_positive,      "2.0"),

    # ── CAPITAL ALLOCATION ─────────────────────────────────────────────────
    ("SWING_CAPITAL_PCT", "RECOMMENDED", "Fraction for swing trades (0.45 = 45%)",
     is_pct,           "0.45"),

    ("INTRADAY_CAPITAL_PCT","RECOMMENDED","Fraction for intraday trades",
     is_pct,           "0.30"),

    ("SCALPING_CAPITAL_PCT","RECOMMENDED","Fraction for scalping trades",
     is_pct,           "0.15"),

    ("RESERVE_CAPITAL_PCT","RECOMMENDED","Fraction held as reserve",
     is_pct,           "0.10"),

    # ── COMPOUNDING / DRAWDOWN ─────────────────────────────────────────────
    ("DRAWDOWN_TRIGGER_PCT","RECOMMENDED","Halve sizes after X% drawdown (0.15=15%)",
     is_pct,           "0.15"),

    ("PROFIT_LOCK_PCT",   "RECOMMENDED", "Lock X% of profits on last Friday",
     is_pct,           "0.30"),
]

# ── Live-only extra rules ─────────────────────────────────────────────────────
LIVE_RULES = [
    ("REAL_CAPITAL",      "REQUIRED", "Your actual Angel One balance (₹)",
     is_positive,      "Your current Angel One balance"),

    ("MIN_LIVE_CAPITAL",  "REQUIRED", "Set to your comfort threshold (₹)",
     is_positive,      "25000"),
]


# ── Validator ─────────────────────────────────────────────────────────────────

def validate(env: dict, live_check: bool = False) -> dict:
    results = {"pass": [], "warn": [], "fail": [], "fix": []}
    rules   = RULES + (LIVE_RULES if live_check else [])

    for key, level, desc, validator, suggested in rules:
        val = env.get(key, "")

        if not val:
            if level == "REQUIRED":
                msg = f"{key:<30} {desc}"
                print(fail(msg))
                results["fail"].append(key)
                results["fix"].append(f'{key}={suggested}    # {desc}')
            else:
                msg = f"{key:<30} not set — {desc}"
                print(warn(msg))
                results["warn"].append(key)
                results["fix"].append(f'# {key}={suggested}    # {desc} (optional)')
            continue

        if not validator(val):
            msg = f"{key:<30} invalid value: '{val}'  (expected: {suggested})"
            print(fail(msg))
            results["fail"].append(key)
            results["fix"].append(f'{key}={suggested}    # fix: was \'{val}\'')
        else:
            # Mask sensitive values
            display = val
            if any(x in key.lower() for x in ["key","secret","token","password","totp"]):
                display = val[:4] + "****" + val[-2:] if len(val) > 6 else "****"
            msg = f"{key:<30} {display}"
            print(ok(msg))
            results["pass"].append(key)

    return results


def check_capital_allocation(env: dict) -> None:
    """Verify capital allocation percentages sum to 1.0."""
    keys = ["SWING_CAPITAL_PCT", "INTRADAY_CAPITAL_PCT",
            "SCALPING_CAPITAL_PCT", "RESERVE_CAPITAL_PCT"]
    vals = []
    for k in keys:
        try: vals.append(float(env.get(k, 0)))
        except: vals.append(0)
    total = sum(vals)
    if all(v > 0 for v in vals):
        if abs(total - 1.0) < 0.02:
            print(ok(f"Capital allocation sums to {total:.2f} ✓"))
        else:
            print(fail(f"Capital allocation sums to {total:.2f} — must equal 1.0"))
            print(f"       SWING={vals[0]} + INTRADAY={vals[1]} + SCALP={vals[2]} + RESERVE={vals[3]} = {total:.2f}")


def check_mode_consistency(env: dict) -> None:
    """Check paper/live mode settings are consistent."""
    paper   = env.get("PAPER_TRADING","true").lower()
    real    = env.get("ENABLE_REAL_TRADING","false").lower()
    auto    = env.get("AUTO_MODE_SWITCH","true").lower()

    if paper == "false" and real == "false":
        print(warn("PAPER_TRADING=false but ENABLE_REAL_TRADING=false — no mode active!"))
        print(f"       Set ENABLE_REAL_TRADING=true to trade live")
    elif paper == "true" and real == "true":
        print(warn("Both PAPER_TRADING=true and ENABLE_REAL_TRADING=true — paper takes priority"))
        print(f"       To go live: set PAPER_TRADING=false")
    elif paper == "false" and real == "true":
        min_cap = env.get("MIN_LIVE_CAPITAL", "25000")
        cap     = env.get("REAL_CAPITAL", env.get("CAPITAL", "0"))
        try:
            if float(cap) < float(min_cap):
                print(warn(f"LIVE MODE but REAL_CAPITAL=₹{cap} < MIN_LIVE_CAPITAL=₹{min_cap}"))
                print(f"       Auto-mode will downgrade to PAPER until capital >= ₹{min_cap}")
            else:
                print(ok(f"LIVE MODE — REAL_CAPITAL=₹{cap} >= MIN_LIVE_CAPITAL=₹{min_cap}"))
        except: pass
    else:
        if auto == "true":
            print(ok("AUTO_MODE_SWITCH=true — system decides paper/live automatically"))
        else:
            print(ok(f"Paper trading mode active"))


def check_totp(env: dict) -> None:
    """Validate TOTP secret format (should be base32)."""
    totp = env.get("TOTP_SECRET", "")
    if not totp:
        return
    # Base32 chars only
    valid_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=")
    clean = totp.upper().replace(" ", "")
    if all(c in valid_chars for c in clean) and len(clean) >= 16:
        print(ok(f"{'TOTP_SECRET':<30} valid base32 format ({len(clean)} chars)"))
    elif totp.isdigit() and len(totp) == 6:
        print(fail("TOTP_SECRET looks like a 6-digit OTP code — this is WRONG"))
        print("       You need the BASE32 SECRET from Angel One, not the current OTP code")
        print("       Go to: Angel One app → Profile → Security → Authenticator Setup")
    else:
        print(warn(f"{'TOTP_SECRET':<30} format unusual — verify it is the base32 secret"))


def check_env_file_issues(env_path: str = ".env") -> None:
    """Check for common .env file formatting issues."""
    p = Path(env_path)
    if not p.exists():
        return
    issues = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        if "=" not in line:
            issues.append(f"Line {i}: no '=' found: {line[:50]}")
        elif line.count("=") > 1 and "http" not in line:
            val = line.split("=", 1)[1]
            if not val.startswith('"') and "=" in val:
                issues.append(f"Line {i}: multiple '=' — wrap value in quotes if intentional")
        # Check for spaces around =
        if re.match(r'\w+\s+=\s*', line) or re.match(r'\w+=\s+\S', line):
            issues.append(f"Line {i}: spaces around '=' may cause issues: {line[:50]}")
    if issues:
        for issue in issues:
            print(warn(f"Format: {issue}"))
    else:
        print(ok(f"{'File format':<30} no formatting issues found"))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    live_check  = "--live" in sys.argv
    show_fix    = "--fix"  in sys.argv
    env_path    = ".env"

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  .ENV VALIDATOR — Trading Bot{RESET}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  {os.getcwd()}")
    print(f"{'═'*60}{RESET}\n")

    # Check file exists
    if not Path(env_path).exists():
        print(fail(f".env file not found at {os.path.abspath(env_path)}"))
        print(f"\nCreate it:")
        print(f"  cp .env.example .env")
        print(f"  nano .env")
        sys.exit(1)

    print(ok(f".env file found: {os.path.abspath(env_path)}"))
    print()

    # Load
    env = load_env(env_path)
    print(f"  Loaded {len(env)} keys from .env\n")

    # File format check
    print(f"{BOLD}FILE FORMAT{RESET}")
    check_env_file_issues(env_path)
    print()

    # Core validation
    print(f"{BOLD}REQUIRED KEYS{RESET}")
    results = validate(env, live_check=live_check)
    print()

    # Special checks
    print(f"{BOLD}CONSISTENCY CHECKS{RESET}")
    check_capital_allocation(env)
    check_mode_consistency(env)
    check_totp(env)
    print()

    # Summary
    p = len(results["pass"])
    w = len(results["warn"])
    f = len(results["fail"])

    print(f"{'═'*60}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"  {GREEN}✅ PASS  {p:3d}{RESET}")
    print(f"  {YELLOW}⚠️  WARN  {w:3d}{RESET}  (optional settings not configured)")
    print(f"  {RED}❌ FAIL  {f:3d}{RESET}  (required — must fix before bot works)")
    print()

    if f == 0:
        print(f"{GREEN}{BOLD}✅ .env is valid — bot is ready to run{RESET}")
    elif f <= 2:
        print(f"{YELLOW}{BOLD}⚠️  Fix {f} required settings before starting{RESET}")
    else:
        print(f"{RED}{BOLD}❌ {f} required settings missing — bot will crash on start{RESET}")

    # Show fix suggestions
    if show_fix or f > 0:
        print(f"\n{BOLD}ADD THESE TO YOUR .env:{RESET}")
        print(f"{'─'*60}")
        for line in results["fix"]:
            if not line.startswith("#"):
                print(f"  {line}")
        print(f"{'─'*60}")
        print(f"\nEdit with:  nano {env_path}")

    if not live_check and f == 0:
        print(f"\n{CYAN}Tip: run with --live to check live trading requirements{RESET}")
        print(f"     python validate_env.py --live")

    print(f"{'═'*60}\n")
    sys.exit(0 if f == 0 else 1)
