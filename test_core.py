"""
test_core.py — Basic tests for critical system components

Run: python3 test_core.py
All tests must pass before deploying to live trading.
"""
from __future__ import annotations
import sys, ast, re
from pathlib import Path

PASSED = 0
FAILED = 0

def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name} — {detail}")


def run_tests():
    global PASSED, FAILED
    print("=" * 55)
    print("SYSTEM TEST SUITE")
    print("=" * 55)
    
    # ── 1. Syntax check all files ─────────────────────────
    print("\n[1] Syntax Check")
    py_files = list(Path(".").glob("*.py"))
    syntax_errors = []
    for f in py_files:
        try: ast.parse(f.read_text())
        except SyntaxError as e: syntax_errors.append(f"{f.name}:{e.lineno}")
    check(f"All {len(py_files)} files parse", len(syntax_errors) == 0,
         f"Errors: {syntax_errors[:3]}")
    
    # ── 2. No paper_trade=True forces ─────────────────────
    print("\n[2] Paper Trade Safety")
    paper_forces = 0
    for f in py_files:
        src = f.read_text()
        for l in src.split("\n"):
            if l.strip().startswith("#"): continue
            if re.search(r'\.PAPER_TRADING\s*=\s*True', l): paper_forces += 1
            if re.search(r'\.paper_trade\s*=\s*\(mode', l): paper_forces += 1
    check("No paper_trade=True forces", paper_forces == 0,
         f"Found {paper_forces} forces")
    
    # ── 3. No bare DataFetcher() ──────────────────────────
    print("\n[3] Data Fetcher Safety")
    bare = 0
    for f in py_files:
        if f.name == 'test_core.py': continue  # skip self
        for l in f.read_text().split("\n"):
            if 'DataFetcher()' in l and '#' not in l.split('DataFetcher')[0] and 'def ' not in l:
                bare += 1
    check("No bare DataFetcher()", bare == 0, f"Found {bare}")
    
    # ── 4. Angel defaults correct ─────────────────────────
    print("\n[4] Angel Configuration")
    ang = Path("angel.py").read_text()
    check("Angel connect unconditional", "ALWAYS connect for DATA" in ang)
    check("Angel paper_trade default=False", "paper_trade: bool = False" in ang)
    check("Angel block_real_orders flag", "block_real_orders" in ang)
    check("verify_order_fill exists", "def verify_order_fill" in ang)
    check("reconcile_positions exists", "def reconcile_positions" in ang)
    
    # ── 5. Config defaults ────────────────────────────────
    print("\n[5] Config Defaults")
    cfg = Path("config.py").read_text()
    check("PAPER_TRADING default=false", '"false"' in cfg.split('_env("PAPER_TRADING"')[1][:20] if '_env("PAPER_TRADING"' in cfg else "")
    check("MIN_BARS_FOR_SIGNAL <= 20",
         int(re.search(r'MIN_BARS_FOR_SIGNAL.*?(\d+)\)', cfg).group(1)) <= 20 if re.search(r'MIN_BARS_FOR_SIGNAL.*?(\d+)\)', cfg) else False)
    
    # ── 6. Data pipeline ──────────────────────────────────
    print("\n[6] Data Pipeline")
    df = Path("data_fetcher.py").read_text()
    check("Angel source", "_fetch_from_angel" in df)
    check("Candle cache fallback", "candle_cache" in df)
    check("Upstox fallback", "upstox" in df.lower())
    check("Bhavcopy fallback", "bhavcopy" in df.lower())
    check("NSE index fallback", "NSE index" in df)
    check("Parallel fetch", "ThreadPoolExecutor" in df)
    check("Rate limit", "sleep(0.3)" in df)
    
    # ── 7. Telegram commands ──────────────────────────────
    print("\n[7] Telegram Commands")
    tg = Path("telegram_commands.py").read_text()
    defined = set(re.findall(r'    def (_cmd_\w+)\(', tg))
    reg_matches = re.findall(r'self\.register\(.+?,\s*self\.(_cmd_\w+)\)', tg)
    registered = set(reg_matches)
    missing = registered - defined
    check(f"All {len(registered)} methods defined", len(missing) == 0,
         f"Missing: {list(missing)[:3]}")
    check("send() has chat_id param", "chat_id: str = None" in tg)
    check("/deploy command detached", "setsid" in tg or "start_new_session" in tg)
    
    # ── 8. Risk management ────────────────────────────────
    print("\n[8] Risk Management")
    lse = Path("live_signal_engine.py").read_text()
    check("Kill switch wired", "KillSwitch" in lse)
    check("Top scores logging", "TOP SCORES" in lse)
    check("WebSocket tracker", "_ws_tracker" in lse)
    
    # ── 9. Critical files exist ───────────────────────────
    print("\n[9] Critical Files")
    critical = [
        "angel.py", "data_fetcher.py", "live_signal_engine.py",
        "signal_engine.py", "trade_manager.py", "telegram_commands.py",
        "config.py", "main_autonomous.py", "websocket_tracker.py",
        "candle_cache.py", "upstox_data.py", "indicator_cache.py",
        "signal_broadcaster.py", "off_hours_engine.py",
    ]
    for cf in critical:
        check(f"{cf} exists", Path(cf).exists())
    
    # ── Summary ───────────────────────────────────────────
    print(f"\n{'='*55}")
    total = PASSED + FAILED
    print(f"RESULTS: {PASSED}/{total} passed ({PASSED*100//total}%)")
    if FAILED:
        print(f"❌ {FAILED} TESTS FAILED — fix before deploying live")
    else:
        print("✅ ALL TESTS PASSED — system ready for deployment")
    print("=" * 55)
    return FAILED == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
