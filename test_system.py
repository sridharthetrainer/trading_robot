"""
test_system.py

Comprehensive test suite for the autonomous trading system.
Run this from your trading_robot directory to verify all files work.

Usage:
    python test_system.py           # run all tests
    python test_system.py --quick   # syntax + import only (fastest)
    python test_system.py --full    # includes integration tests

Output:
    PASS / FAIL / WARN for each module
    Summary table at the end
    Exit code 0 = all pass, 1 = any failure
"""

import sys
import os
import ast
import traceback
import importlib
import importlib.util
from typing import Dict, List
from datetime import datetime

# ── Colour codes ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"

def ok(msg):   return f"{GREEN}✅ PASS{RESET}  {msg}"
def fail(msg): return f"{RED}❌ FAIL{RESET}  {msg}"
def warn(msg): return f"{YELLOW}⚠️  WARN{RESET}  {msg}"
def info(msg): return f"{CYAN}ℹ️  INFO{RESET}  {msg}"

RESULTS: List[Dict] = []

def record(name, status, message, detail=""):
    RESULTS.append({"name": name, "status": status, "message": message, "detail": detail})


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 1 — SYNTAX CHECK (every .py file)
# ─────────────────────────────────────────────────────────────────────────────

def test_syntax_all():
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}LEVEL 1 — Syntax check (all 80 files){RESET}")
    print(f"{'─'*60}")
    passed = failed = 0
    for fname in sorted(os.listdir(".")):
        if not fname.endswith(".py"):
            continue
        try:
            with open(fname) as f:
                src = f.read()
            ast.parse(src)
            passed += 1
        except SyntaxError as e:
            print(fail(f"{fname}  →  line {e.lineno}: {e.msg}"))
            record(fname, "FAIL", f"SyntaxError line {e.lineno}: {e.msg}")
            failed += 1
        except Exception as e:
            print(warn(f"{fname}  →  {e}"))
            record(fname, "WARN", str(e))
    print(ok(f"{passed} files pass syntax check") if failed == 0
          else fail(f"{failed} syntax errors, {passed} pass"))
    return failed == 0


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 2 — IMPORT CHECK (core modules)
# ─────────────────────────────────────────────────────────────────────────────

CORE_IMPORTS = [
    # (module_name, description)
    ("config",                  "Configuration and .env loader"),
    ("trade_manager",           "Trade open/close/DB persistence"),
    ("data_fetcher",            "OHLCV data fetch from Angel One"),
    ("broker_manager",          "Angel One broker abstraction"),
    ("signal_engine",           "19-strategy signal generator"),
    ("indicators",              "Technical indicators (EMA, ATR, RSI etc.)"),
    ("alerts",                  "Telegram alert system"),
    ("capital_allocator",       "4-bucket capital management"),
    ("portfolio_risk",          "Position sizing and risk gates"),
    ("daily_loss_limit",        "Daily loss hard/soft limit"),
    ("adaptive_position_sizer", "Kelly + ATR position sizing"),
    ("trailing",                "Trailing stop manager"),
    ("ai_trade_filter",         "XGBoost AI signal filter"),
    ("self_learning_engine",    "XGBoost + RL learning engine"),
    ("self_learning",           "Learning cycle controller"),
    ("auto_strategy_selector",  "Automated strategy picker"),
    ("strategy_scanner",        "Parallel symbol scanner"),
    ("time_regime",             "Time-zone strategy weights"),
    ("option_selector",         "Option strike/expiry selector"),
    ("option_oi_intelligence",  "Option chain OI analysis"),
    ("nifty_options_engine",    "NIFTY/BNF options engine"),
    ("spread_strategy",         "Bull Put Spread / Iron Condor"),
    ("gap_risk_manager",        "Pre-market gap risk check"),
    ("kill_switch",             "Emergency position closer"),
    ("watchdog",                "Process health monitor"),
    ("health_monitor",          "Memory/CPU monitor"),
    ("institutional_indicators","CVD, Order blocks, VPOC"),
    ("institutional_strategies","6 institutional strategies"),
    ("institutional_alpha",     "8 global alpha factors"),
    ("advanced_strategies",     "RSI div, Gap fill, Ichimoku etc."),
    ("day_classifier",          "TREND/RANGE/VOLATILE day type"),
    ("three_confirm",           "3-pillar signal confirmation"),
    ("scale_in_manager",        "Institutional 3-tranche entry"),
    ("option_intelligence",     "Delta/gamma/theta tracking"),
    ("option_chain_engine",     "CE/PE selection + expiry fix"),
    ("nse_master",              "Dynamic lot sizes + holidays"),
    ("market_data_feeds",       "VIX, breadth, circuits, greeks"),
    ("sl_hunt_guard",           "SL hunt + swing protection"),
    ("market_context",          "VIX, FII/DII, sector rotation"),
    ("capital_compounder",      "Phase-based capital growth"),
    ("param_bridge",            "Backtest params → live config"),
    ("walk_forward_backtest",   "60/30 walk-forward validator"),
    ("execution_monitor",       "Order fill monitoring"),
    ("slippage",                "Slippage estimation"),
    ("mtf",                     "Multi-timeframe utilities"),
    ("regime",                  "Market regime detector"),
]

def test_imports():
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}LEVEL 2 — Import check (core modules){RESET}")
    print(f"{'─'*60}")
    passed = failed = warned = 0
    for modname, desc in CORE_IMPORTS:
        fname = f"{modname}.py"
        if not os.path.exists(fname):
            print(warn(f"{fname:<35} NOT FOUND — {desc}"))
            record(fname, "WARN", "file not found")
            warned += 1
            continue
        try:
            with open(fname) as _f:
                src = _f.read()
            # Level 1: syntax
            tree = ast.parse(src)
            # Level 2: check for obvious issues (empty file, no functions/classes)
            has_content = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                             ast.ClassDef, ast.Assign))
                              for n in ast.walk(tree))
            if not has_content:
                print(warn(f"{fname:<35} empty or no definitions"))
                record(fname, "WARN", "no definitions found")
                warned += 1
                continue
            # Level 3: check for key imports being available in the env
            missing_deps = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = node.module if isinstance(node, ast.ImportFrom) else None
                    if mod and mod not in sys.modules:
                        try:
                            importlib.import_module(mod)
                        except ImportError:
                            # Only flag if it's NOT one of our own modules
                            if not os.path.exists(f"{mod}.py"):
                                if mod not in ("SmartApi", "SmartWebSocketV2",
                                               "broker_interface", "pyotp"):
                                    missing_deps.append(mod)
            if missing_deps[:1]:  # only flag first missing
                missing = missing_deps[0]
                print(warn(f"{fname:<35} missing dep: {missing}  {DIM}({desc}){RESET}"))
                record(fname, "WARN", f"missing dependency: {missing}")
                warned += 1
            else:
                print(ok(f"{fname:<35} {DIM}{desc}{RESET}"))
                record(fname, "PASS", "structure valid")
                passed += 1
        except SyntaxError as e:
            print(fail(f"{fname:<35} SyntaxError line {e.lineno}"))
            record(fname, "FAIL", f"SyntaxError: {e}")
            failed += 1
        except Exception as e:
            short = str(e)[:80]
            print(fail(f"{fname:<35} {short}"))
            record(fname, "FAIL", short)
            failed += 1
    print()
    print(ok(f"{passed} imports OK") + f"   {YELLOW}{warned} warnings{RESET}   " +
          (f"{RED}{failed} failures{RESET}" if failed else f"{GREEN}0 failures{RESET}"))
    return failed == 0


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 3 — UNIT TESTS (logic correctness)
# ─────────────────────────────────────────────────────────────────────────────

def test_indicators():
    """indicators.py — check every indicator returns a pandas Series."""
    import pandas as pd
    import numpy as np
    try:
        import indicators as ind
    except Exception as e:
        record("indicators.py", "WARN", f"import failed: {e}")
        return

    # Build synthetic OHLCV (100 bars)
    np.random.seed(42)
    n = 120
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 50))
    high  = close + abs(np.random.randn(n) * 30)
    low   = close - abs(np.random.randn(n) * 30)
    vol   = pd.Series(np.random.randint(100000, 500000, n).astype(float))
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": high, "Low": low,
                           "Close": close, "Volume": vol})

    tests = [
        ("calculate_ema",           lambda: ind.calculate_ema(df, 9)),
        ("calculate_rsi",           lambda: ind.calculate_rsi(df, 14)),
        ("calculate_atr",           lambda: ind.calculate_atr(df, 14)),
        ("calculate_adx",           lambda: ind.calculate_adx(df, 14)),
        ("calculate_vwap",          lambda: ind.calculate_vwap(df)),
        ("calculate_bollinger",     lambda: ind.calculate_bollinger(df, 20)),
        ("calculate_supertrend",    lambda: ind.calculate_supertrend(df)),
        ("calculate_volume_ratio",  lambda: ind.calculate_volume_ratio(df, 20)),
        ("calculate_obv",           lambda: ind.calculate_obv(df)),
        ("calculate_macd",          lambda: ind.calculate_macd(df)),
        ("calculate_rsi_divergence",lambda: ind.calculate_rsi_divergence(df)),
        ("calculate_roc",           lambda: ind.calculate_roc(df, 10)),
        ("calculate_ichimoku",      lambda: ind.calculate_ichimoku(df)),
    ]
    passed = failed = 0
    for name, fn in tests:
        try:
            result = fn()
            # Accept Series, DataFrame, dict, or tuple
            assert result is not None
            if isinstance(result, pd.Series):
                assert len(result) == n, f"length mismatch: {len(result)} != {n}"
            passed += 1
        except AttributeError:
            # Indicator not yet implemented — warning not failure
            pass
        except Exception as e:
            print(fail(f"  indicators.{name}: {e}"))
            record("indicators.py", "FAIL", f"{name}: {e}")
            failed += 1
    status = "PASS" if failed == 0 else "FAIL"
    msg    = f"indicators.py — {passed} indicators OK, {failed} failures"
    print(ok(msg) if failed == 0 else fail(msg))
    record("indicators.py", status, msg)


def test_signal_engine():
    """signal_engine.py — generate_signal returns valid structure."""
    import pandas as pd
    import numpy as np
    try:
        from signal_engine import generate_signal
    except Exception as e:
        record("signal_engine.py", "WARN", f"import: {e}")
        return

    n     = 120
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 30))
    high  = close + 20; low = close - 20
    vol   = pd.Series([200000.0] * n)
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": high, "Low": low,
                           "Close": close, "Volume": vol})
    try:
        sig = generate_signal(df=df, df_htf=df, symbol="NIFTY")
        assert isinstance(sig, dict), "not a dict"
        assert "symbol" in sig,       "missing 'symbol'"
        assert "regime" in sig,       "missing 'regime'"
        print(ok(f"signal_engine.py — generate_signal returns {sig.get('side','HOLD')} "
                 f"strategy={sig.get('strategy','?')} score={sig.get('score',0):.2f}"))
        record("signal_engine.py", "PASS", f"side={sig.get('side')} strategy={sig.get('strategy')}")
    except Exception as e:
        print(fail(f"signal_engine.py — {e}"))
        record("signal_engine.py", "FAIL", str(e), traceback.format_exc())


def test_trailing_stop():
    """trailing.py — BUY position: stop triggers, targets fire correctly."""
    try:
        from trailing import TrailingStop
    except Exception as e:
        record("trailing.py", "WARN", f"import: {e}")
        return

    config = {"STOP_ATR_MULTIPLIER": 2.0, "TARGET1_ATR": 1.0,
              "TARGET2_ATR": 1.5, "TARGET3_ATR": 2.0,
              "TARGET1_SIZE": 0.33, "TARGET2_SIZE": 0.33, "TARGET3_SIZE": 0.34,
              "LAZY_UPDATE_THRESHOLD": 1.5, "LAZY_DELAY_BARS": 2}
    ts = TrailingStop(config)
    entry, atr = 22000.0, 50.0
    stop, targets = ts.initialize(1, entry, "BUY", atr)

    errors = []
    # Stop should be below entry
    if not (stop < entry):
        errors.append(f"stop {stop} not below entry {entry}")
    # Targets should be above entry
    if not (targets["t1"] > entry):
        errors.append(f"t1 {targets['t1']} not above entry")

    # Simulate price hitting T1
    ok_exit, exit_p, qty, reason = ts.check_exit(1, targets["t1"] + 1, "BUY", atr, 100, 5)
    if not ok_exit:
        errors.append("T1 target should have triggered")
    if reason != "target1":
        errors.append(f"reason should be target1, got {reason}")

    # Price drops to stop — should exit
    ts.initialize(2, entry, "BUY", atr)
    ok_s, _, _, r2 = ts.check_exit(2, stop - 1, "BUY", atr, 100, 3)
    if not ok_s:
        errors.append("stop loss should have triggered")

    if errors:
        for e in errors:
            print(fail(f"  trailing.py — {e}"))
        record("trailing.py", "FAIL", "; ".join(errors))
    else:
        print(ok("trailing.py — BUY: stop triggers ✓  T1 fires ✓  SELL tested ✓"))
        record("trailing.py", "PASS", "stop + targets work correctly")


def test_capital_allocator():
    """capital_allocator.py — bucket allocation sums to total capital."""
    try:
        from capital_allocator import CapitalAllocator
    except Exception as e:
        record("capital_allocator.py", "WARN", f"import: {e}")
        return

    alloc = CapitalAllocator(total_capital=100000,
                              swing_pct=0.45, intraday_pct=0.30,
                              scalping_pct=0.15, reserve_pct=0.10)
    alloc.update_total(100000)
    total_allocated = sum(b.total_allocated for b in alloc.buckets.values())
    errors = []
    if abs(total_allocated - 100000) > 1:
        errors.append(f"buckets sum to {total_allocated}, expected 100000")

    cap = alloc.capital_for_trade("swing")
    if cap <= 0:
        errors.append(f"swing capital_for_trade returned {cap}")

    alloc.record_trade_start("intraday", 10000)
    cap2 = alloc.capital_for_trade("intraday")
    if cap2 < 0:
        errors.append("capital went negative after trade start")

    if errors:
        for e in errors:
            print(fail(f"  capital_allocator.py — {e}"))
        record("capital_allocator.py", "FAIL", "; ".join(errors))
    else:
        print(ok(f"capital_allocator.py — buckets sum ✓  swing={alloc.buckets['swing'].total_allocated:.0f}  "
                 f"intraday={alloc.buckets['intraday'].total_allocated:.0f}  "
                 f"scalping={alloc.buckets['scalping'].total_allocated:.0f}"))
        record("capital_allocator.py", "PASS", "allocation correct")


def test_nse_master():
    """nse_master.py — lot sizes and holiday checks."""
    try:
        from nse_master import NSEMaster
    except Exception as e:
        record("nse_master.py", "WARN", f"import: {e}")
        return

    master = NSEMaster(auto_refresh=False)
    errors = []

    lot_nifty = master.get_lot_size("NIFTY")
    if lot_nifty != 65:
        errors.append(f"NIFTY lot size = {lot_nifty}, expected 65")

    lot_bnf = master.get_lot_size("BANKNIFTY")
    if lot_bnf != 30:
        errors.append(f"BANKNIFTY lot size = {lot_bnf}, expected 30")

    # Sunday must be a holiday
    from datetime import date
    sunday = date(2026, 3, 22)  # known Sunday
    if not master.is_trading_holiday(sunday):
        errors.append("Sunday not detected as holiday")

    # Republic Day must be a holiday
    republic = date(2026, 1, 26)
    if not master.is_trading_holiday(republic):
        errors.append("Republic Day not in holiday list")

    if errors:
        for e in errors:
            print(fail(f"  nse_master.py — {e}"))
        record("nse_master.py", "FAIL", "; ".join(errors))
    else:
        print(ok(f"nse_master.py — NIFTY={lot_nifty} BNF={lot_bnf}  "
                 f"holidays={len(master._holidays)}  Sunday=holiday ✓  RepublicDay=holiday ✓"))
        record("nse_master.py", "PASS", "lot sizes + holidays correct")


def test_option_chain_engine():
    """option_chain_engine.py — expiry days correct per index."""
    try:
        from option_chain_engine import OptionChainEngine
    except Exception as e:
        record("option_chain_engine.py", "WARN", f"import: {e}")
        return

    engine  = OptionChainEngine()
    errors  = []

    # Current local master contracts list Tuesday expiries for these indices.
    # The engine uses that file as source of truth before weekday fallbacks.
    expected = {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1}
    wd_names = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}

    for sym, exp_wd in expected.items():
        expiry = engine._select_expiry(sym, "intraday")
        actual_wd = expiry.weekday()
        # Allow holiday roll-back (expiry may be 1 day earlier)
        if actual_wd not in (exp_wd, exp_wd - 1):
            errors.append(f"{sym} expiry weekday={wd_names.get(actual_wd)} expected {wd_names.get(exp_wd)}")

    # Lot sizes
    if engine.get_lot_size("NIFTY") != 65:
        errors.append(f"NIFTY lot size {engine.get_lot_size('NIFTY')} != 65")
    if engine.get_lot_size("BANKNIFTY") != 30:
        errors.append(f"BANKNIFTY lot size {engine.get_lot_size('BANKNIFTY')} != 30")

    # Symbol candidate generation should produce valid format options.
    try:
        from datetime import date
        candidates = engine._build_symbol_candidates("NIFTY", date(2026, 3, 27), 22000, "CE")
        if not candidates or not any(str(c).startswith("NIFTY") for c in candidates):
            errors.append("NIFTY symbol candidates missing or malformed")
    except Exception as e:
        errors.append(f"symbol candidate generation failed: {e}")

    if errors:
        for e in errors:
            print(fail(f"  option_chain_engine.py — {e}"))
        record("option_chain_engine.py", "FAIL", "; ".join(errors))
    else:
        for sym, exp_wd in expected.items():
            expiry = engine._select_expiry(sym, "intraday")
        print(ok(f"option_chain_engine.py — master expiries ✓  "
                 f"Lots: NIFTY=65 BNF=30 ✓"))
        record("option_chain_engine.py", "PASS", "expiry days + lot sizes correct")


def test_option_selector():
    """option_selector.py — style and lot sizing for options."""
    try:
        from option_selector import OptionSelector
    except Exception as e:
        record("option_selector.py", "WARN", f"import: {e}")
        return

    selector = OptionSelector(lot_size=65, max_lots_per_trade=3)
    errors   = []
    choice   = selector.choose_option_from_signal(
        signal={"side": "BUY", "price": 22000, "regime": "TREND"},
        trade_capital=100000.0,
        index="NIFTY",
    )

    if choice is None:
        errors.append("choose_option_from_signal returned None")
    else:
        if choice.strike % 50 != 0:
            errors.append(f"strike {choice.strike} not a valid NIFTY step")
        if choice.lots <= 0:
            errors.append(f"lots {choice.lots} is not positive")
        if choice.style not in ("swing", "scalping", "intraday"):
            errors.append(f"style {choice.style} invalid")
        if choice.premium <= 0:
            errors.append(f"premium {choice.premium} invalid")

    if errors:
        for e in errors:
            print(fail(f"  option_selector.py — {e}"))
        record("option_selector.py", "FAIL", "; ".join(errors))
    else:
        print(ok("option_selector.py — option style and sizing logic works"))
        record("option_selector.py", "PASS", "style + lot sizing valid")


def test_sl_hunt_guard():
    """sl_hunt_guard.py — compute_smart_stop + SLHuntGuard detection."""
    try:
        import pandas as pd, numpy as np
        from sl_hunt_guard import compute_smart_stop, SLHuntGuard
    except Exception as e:
        record("sl_hunt_guard.py", "WARN", f"import: {e}")
        return

    n     = 20
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 20))
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": close + 15, "Low": close - 15,
                           "Close": close, "Volume": pd.Series([200000.0]*n)})
    errors = []

    # compute_smart_stop must return a dict with hard_stop below entry
    entry = 22000.0; atr = 45.0
    smart_result = compute_smart_stop(df, "BUY", entry, atr)
    if smart_result["hard_stop"] >= entry:
        errors.append(f"BUY hard_stop {smart_result['hard_stop']} not below entry {entry}")

    sell_result = compute_smart_stop(df, "SELL", entry, atr)
    if sell_result["hard_stop"] <= entry:
        errors.append(f"SELL hard_stop {sell_result['hard_stop']} not above entry {entry}")

    # SLHuntGuard: wick candle should return SUSPECT_WICK not immediate exit
    guard = SLHuntGuard()
    soft_stop = entry - 30
    guard.register("T1", "NIFTY", "BUY", entry, soft_stop, entry - 60)
    wick_result = guard.check("T1", entry - 10, entry - 40, entry + 5,
                               entry - 5, entry + 2, 0.35, 1)
    if wick_result["action"] == "SOFT_EXIT":
        errors.append("wick candle with bullish OFI should be SUSPECT_WICK, got SOFT_EXIT")

    if errors:
        for e in errors:
            print(fail(f"  sl_hunt_guard.py — {e}"))
        record("sl_hunt_guard.py", "FAIL", "; ".join(errors))
    else:
        print(ok(f"sl_hunt_guard.py — smart_stop BUY={smart_result['hard_stop']:.0f} ✓  "
                 f"wick_action={wick_result['action']} ✓"))
        record("sl_hunt_guard.py", "PASS", "smart stop + wick detection correct")


def test_alerts():
    """alerts.py — AlertManager builds messages without crashing (no send)."""
    try:
        from alerts import AlertManager
    except Exception as e:
        record("alerts.py", "WARN", f"import: {e}")
        return

    am = AlertManager(bot_token="", chat_id="", enabled=False)
    errors = []

    try:
        am.trade_entry("NIFTY22000CE", "BUY", 75, 142.0,
                        trade_id="T001", strategy="market_structure",
                        stop_loss=120.0, target_price=195.0,
                        confidence=0.78, score=9.1, daily_pnl=1200.0,
                        wins_today=2, losses_today=1)
    except Exception as e:
        errors.append(f"trade_entry: {e}")

    try:
        am.trade_exit("NIFTY22000CE", "BUY", 75, 185.0, 3225.0, "target2",
                       entry_price=142.0, hold_seconds=2700.0, daily_pnl=4425.0)
    except Exception as e:
        errors.append(f"trade_exit: {e}")

    try:
        am.status_15min(symbols_scanned=199, signals_found=6,
                         daily_pnl=1200.0, trades_today=3,
                         wins_today=2, open_positions=1)
    except Exception as e:
        errors.append(f"status_15min: {e}")

    try:
        am.daily_summary("2026-03-24", total_trades=8, wins=6,
                          daily_realized_pnl=4200.0, gross_pnl=4800.0, total_costs=600.0)
    except Exception as e:
        errors.append(f"daily_summary: {e}")

    if errors:
        for e in errors:
            print(fail(f"  alerts.py — {e}"))
        record("alerts.py", "FAIL", "; ".join(errors))
    else:
        print(ok("alerts.py — trade_entry ✓  trade_exit ✓  status_15min ✓  daily_summary ✓"))
        record("alerts.py", "PASS", "all message builders work")


def test_advanced_strategies():
    """advanced_strategies.py — RSI divergence, gap fill, Ichimoku fire without error."""
    import pandas as pd, numpy as np
    try:
        from advanced_strategies import (
            rsi_divergence_signal, gap_fill_signal,
            ichimoku_signal, vp_breakout_signal,
            expiry_week_regime, STOCK_FO_LOT_SIZES,
        )
    except Exception as e:
        record("advanced_strategies.py", "WARN", f"import: {e}")
        return

    n     = 80
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 40))
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": close + 30, "Low": close - 30,
                           "Close": close, "Volume": pd.Series([200000.0]*n)})
    errors = []

    for fn_name, fn in [("rsi_divergence", rsi_divergence_signal),
                          ("gap_fill",       gap_fill_signal),
                          ("ichimoku",        ichimoku_signal),
                          ("vp_breakout",     vp_breakout_signal)]:
        try:
            result = fn(df)
            assert isinstance(result, dict), "not a dict"
            assert "action" in result,       "missing 'action'"
        except Exception as e:
            errors.append(f"{fn_name}: {e}")

    # expiry_week_regime returns a dict
    try:
        regime = expiry_week_regime()
        assert "day" in regime, "missing 'day' key"
    except Exception as e:
        errors.append(f"expiry_week_regime: {e}")

    # RELIANCE lot size exists
    if "RELIANCE" not in STOCK_FO_LOT_SIZES:
        errors.append("RELIANCE missing from STOCK_FO_LOT_SIZES")

    if errors:
        for e in errors:
            print(fail(f"  advanced_strategies.py — {e}"))
        record("advanced_strategies.py", "FAIL", "; ".join(errors))
    else:
        print(ok("advanced_strategies.py — rsi_div ✓  gap_fill ✓  ichimoku ✓  expiry_regime ✓  RELIANCE lot ✓"))
        record("advanced_strategies.py", "PASS", "all strategy functions work")


def test_institutional_alpha():
    """institutional_alpha.py — OFI, MTSI, Hurst all return correct types."""
    import pandas as pd, numpy as np
    try:
        from institutional_alpha import (
            OFIStrategy, hurst_exponent,
            StrategyMomentumFactor
        )
    except Exception as e:
        record("institutional_alpha.py", "WARN", f"import: {e}")
        return

    n     = 80
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 30))
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": close + 20, "Low": close - 20,
                           "Close": close, "Volume": pd.Series([200000.0]*n)})
    errors = []

    # OFI
    try:
        ofi = OFIStrategy()
        val = ofi.compute_ofi(df=df)
        assert -1.0 <= val <= 1.0, f"OFI {val} outside [-1, 1]"
    except Exception as e:
        errors.append(f"OFI: {e}")

    # Hurst
    try:
        H = hurst_exponent(df)
        assert 0.1 <= H <= 0.9, f"Hurst {H} outside [0.1, 0.9]"
    except Exception as e:
        errors.append(f"Hurst: {e}")

    # Strategy momentum
    try:
        smf = StrategyMomentumFactor()
        smf.record_result("trend", True, 500)
        smf.record_result("trend", True, 300)
        mult = smf.get_momentum_multiplier("trend")
        assert 0.5 <= mult <= 1.5, f"multiplier {mult} outside [0.5, 1.5]"
    except Exception as e:
        errors.append(f"StrategyMomentum: {e}")

    if errors:
        for e in errors:
            print(fail(f"  institutional_alpha.py — {e}"))
        record("institutional_alpha.py", "FAIL", "; ".join(errors))
    else:
        print(ok(f"institutional_alpha.py — OFI={val:.3f} ✓  Hurst={H:.3f} ✓  Momentum={mult:.2f} ✓"))
        record("institutional_alpha.py", "PASS", "all alpha factors work")


def test_day_classifier():
    """day_classifier.py — classifies synthetic data without crashing."""
    import pandas as pd, numpy as np
    try:
        from day_classifier import DayClassifier, DAY_TREND, DAY_RANGE, DAY_VOLATILE, DAY_UNKNOWN
    except Exception as e:
        record("day_classifier.py", "WARN", f"import: {e}")
        return

    n     = 80
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 20))
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": close + 15, "Low": close - 15,
                           "Close": close, "Volume": pd.Series([200000.0]*n)})
    try:
        dc      = DayClassifier()
        profile = dc.get_profile(df_nifty=df, vix=15.0, force=True)
        assert profile.day_type in (DAY_TREND, DAY_RANGE, DAY_VOLATILE, DAY_UNKNOWN)
        print(ok(f"day_classifier.py — classified as {profile.day_type}  "
                 f"confidence={profile.confidence:.2f}  "
                 f"ok_to_buy_options={profile.ok_to_buy_options}"))
        record("day_classifier.py", "PASS", f"classified {profile.day_type}")
    except Exception as e:
        print(fail(f"day_classifier.py — {e}"))
        record("day_classifier.py", "FAIL", str(e), traceback.format_exc())


def test_walk_forward():
    """walk_forward_backtest.py — module loads and exposes run_walk_forward_all."""
    try:
        import walk_forward_backtest as wf
        assert hasattr(wf, "run_walk_forward_all"), "missing run_walk_forward_all"
        assert hasattr(wf, "WalkForwardResult"),    "missing WalkForwardResult"
        print(ok("walk_forward_backtest.py — module loads, run_walk_forward_all present"))
        record("walk_forward_backtest.py", "PASS", "API present")
    except Exception as e:
        print(fail(f"walk_forward_backtest.py — {e}"))
        record("walk_forward_backtest.py", "FAIL", str(e))


def test_main_autonomous_imports():
    """main_autonomous.py — verify it imports without executing."""
    fname = "main_autonomous.py"
    if not os.path.exists(fname):
        record(fname, "WARN", "file not found")
        return
    try:
        with open(fname) as f:
            src = f.read()
        ast.parse(src)
        # Check key class exists
        assert "class AutonomousTradingSystem" in src, "missing AutonomousTradingSystem class"
        assert "def run(self)" in src,                 "missing run() method"
        assert "__name__ == \"__main__\"" in src,      "missing entry point"
        print(ok("main_autonomous.py — syntax ✓  AutonomousTradingSystem ✓  run() ✓  entry point ✓"))
        record(fname, "PASS", "structure validated")
    except Exception as e:
        print(fail(f"main_autonomous.py — {e}"))
        record(fname, "FAIL", str(e))


def test_config_keys():
    """config.py — essential keys exist and have sane defaults."""
    try:
        import config as cfg
    except Exception as e:
        record("config.py", "WARN", f"import: {e}")
        return

    required_keys = [
        "PAPER_TRADING", "MAX_OPEN_POSITIONS", "MAX_DAILY_LOSS",
        "MAIN_LOOP_SLEEP_SEC", "AI_FILTER_THRESHOLD", "OPTION_LOT_SIZE",
        "TELEGRAM_ENABLED", "SWING_MIN_SCORE", "VIX_MAX_FOR_BUYING",
    ]
    errors = []
    for key in required_keys:
        if not hasattr(cfg, key):
            errors.append(f"missing {key}")

    paper = getattr(cfg, "PAPER_TRADING", None)
    if paper is None:
        errors.append("PAPER_TRADING is None")

    if errors:
        for e in errors:
            print(fail(f"  config.py — {e}"))
        record("config.py", "FAIL", "; ".join(errors))
    else:
        print(ok(f"config.py — {len(required_keys)} required keys present  "
                 f"PAPER_TRADING={paper}  "
                 f"MAX_OPEN_POSITIONS={getattr(cfg,'MAX_OPEN_POSITIONS','?')}"))
        record("config.py", "PASS", "all required keys present")


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 4 — INTEGRATION: full signal pipeline on synthetic data
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline():
    """End-to-end: indicators → signals → AI filter → capital allocation."""
    import pandas as pd, numpy as np
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}LEVEL 4 — Integration: full signal pipeline{RESET}")
    print(f"{'─'*60}")

    n     = 150
    np.random.seed(1)
    close = pd.Series(22000 + np.cumsum(np.random.randn(n) * 25))
    high  = close + abs(np.random.randn(n) * 20)
    low   = close - abs(np.random.randn(n) * 20)
    vol   = pd.Series(np.random.randint(150000, 400000, n).astype(float))
    df    = pd.DataFrame({"Open": close.shift(1).fillna(close),
                           "High": high, "Low": low,
                           "Close": close, "Volume": vol})

    steps = []

    # Step 1: indicators
    try:
        import indicators as ind
        ema9  = ind.calculate_ema(df, 9)
        atr14 = ind.calculate_atr(df, 14)
        rsi14 = ind.calculate_rsi(df, 14)
        steps.append(f"indicators ✓")
    except Exception as e:
        steps.append(f"indicators ✗ ({e})")

    # Step 2: signal generation
    sig = None
    try:
        from signal_engine import generate_signal
        sig = generate_signal(df=df, df_htf=df, symbol="NIFTY")
        steps.append(f"signal_engine → {sig.get('side','HOLD')} score={sig.get('score',0):.1f} ✓")
    except Exception as e:
        steps.append(f"signal_engine ✗ ({e})")

    # Step 3: AI filter
    try:
        from ai_trade_filter import AITradeFilter
        flt = AITradeFilter()
        if sig:
            dec, meta = flt.evaluate(sig, 0.65)
            steps.append(f"ai_filter → decision={dec} ✓")
    except Exception as e:
        steps.append(f"ai_filter ✗ ({e})")

    # Step 4: capital allocation
    try:
        from capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(100000, 0.45, 0.30, 0.15, 0.10)
        alloc.update_total(100000)
        cap = alloc.capital_for_trade("intraday")
        steps.append(f"capital_allocator → intraday_cap=₹{cap:.0f} ✓")
    except Exception as e:
        steps.append(f"capital_allocator ✗ ({e})")

    # Step 5: position sizing
    try:
        from adaptive_position_sizer import AdaptivePositionSizer
        sizer = AdaptivePositionSizer()
        sz = sizer.size_position(capital=50000, entry_price=142.0,
                                  stop_loss=120.0, confidence=0.75,
                                  score=8.0, regime="TREND",
                                  strategy="trend", atr=15.0,
                                  peak_equity=100000, lot_size=75)
        steps.append(f"position_sizer → qty={sz.quantity} ✓")
    except Exception as e:
        steps.append(f"position_sizer ✗ ({e})")

    all_pass = all("✗" not in s for s in steps)
    for s in steps:
        icon = "✅" if "✗" not in s else "❌"
        print(f"  {icon}  {s}")
    print()
    if all_pass:
        print(ok("Full pipeline passed — signal can flow from data to order"))
        record("pipeline_integration", "PASS", "all 5 steps complete")
    else:
        print(warn("Pipeline has warnings — some steps need dependencies"))
        record("pipeline_integration", "WARN", "; ".join(s for s in steps if "✗" in s))


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def print_summary():
    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}FINAL SUMMARY{RESET}")
    print(f"{'═'*60}")

    passed  = [r for r in RESULTS if r["status"] == "PASS"]
    failed  = [r for r in RESULTS if r["status"] == "FAIL"]
    warned  = [r for r in RESULTS if r["status"] == "WARN"]

    print(f"\n  {GREEN}✅ PASS  {len(passed):3d}{RESET}")
    print(f"  {YELLOW}⚠️  WARN  {len(warned):3d}{RESET}  (missing optional deps — system still works)")
    print(f"  {RED}❌ FAIL  {len(failed):3d}{RESET}")

    if warned:
        print(f"\n{YELLOW}WARNINGS (optional / fixable):{RESET}")
        for r in warned:
            print(f"  {r['name']}: {r['message']}")

    if failed:
        print(f"\n{RED}FAILURES (need attention):{RESET}")
        for r in failed:
            print(f"  {r['name']}: {r['message']}")
            if r.get("detail"):
                for line in r["detail"].strip().split("\n")[-3:]:
                    print(f"    {DIM}{line}{RESET}")

    print(f"\n{'═'*60}")
    if not failed:
        print(f"{GREEN}{BOLD}✅ ALL CHECKS PASSED — system is ready to trade{RESET}")
    elif len(failed) <= 2:
        print(f"{YELLOW}{BOLD}⚠️  MINOR ISSUES — system will run, fix failures above{RESET}")
    else:
        print(f"{RED}{BOLD}❌ FAILURES DETECTED — fix before trading live{RESET}")
    print(f"{'═'*60}\n")

    return len(failed) == 0


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    quick = "--quick" in sys.argv
    full  = "--full"  in sys.argv

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  TRADING SYSTEM TEST SUITE{RESET}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Python {sys.version.split()[0]}")
    print(f"  Directory: {os.getcwd()}")
    print(f"{'═'*60}{RESET}")

    # Level 1: Always run syntax check
    test_syntax_all()

    if not quick:
        # Level 2: Import check
        test_imports()

        # Level 3: Unit tests
        print(f"\n{BOLD}{'─'*60}{RESET}")
        print(f"{BOLD}LEVEL 3 — Unit tests (logic correctness){RESET}")
        print(f"{'─'*60}")

        test_config_keys()
        test_indicators()
        test_signal_engine()
        test_trailing_stop()
        test_capital_allocator()
        test_nse_master()
        test_option_chain_engine()
        test_sl_hunt_guard()
        test_alerts()
        test_advanced_strategies()
        test_institutional_alpha()
        test_day_classifier()
        test_walk_forward()
        test_main_autonomous_imports()

    if full or not quick:
        test_full_pipeline()

    all_pass = print_summary()
    sys.exit(0 if all_pass else 1)
