#!/usr/bin/env python3
"""
test_option_metadata_wiring.py

Unit test to validate that option metadata flows end-to-end:
1. Signal logged with executed=0 and empty option fields
2. mark_executed() called with option_metadata dict
3. signal_log.db row updated with executed=1 and populated option_* columns

Run:
    python test_option_metadata_wiring.py
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

# Add workspace to path
sys.path.insert(0, str(Path(__file__).parent))

from signal_log import SignalLogger

def test_option_metadata_wiring(tmp_path):
    """Validate option metadata flows from mark_executed() into signal_log.db"""
    
    print("\n" + "="*70)
    print("TEST: Option Metadata Wiring (end-to-end)")
    print("="*70 + "\n")
    
    db_path = tmp_path / "signal_log.db"
    sl = SignalLogger(db_path=str(db_path))
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: Log a candidate signal with empty option fields
    # ─────────────────────────────────────────────────────────────────────────
    print("[1] Logging candidate signal with empty option fields...")
    
    signal = {
        "symbol": "NIFTY",
        "side": "BUY",
        "strategy": "PIVOT_SCALPING",
        "score": 75.5,
        "raw_score": 65.0,
        "confluence": "HIGH",
        "regime": "UPTREND",
        "entry_price": 23500.0,
        # Score modifiers embedded in metadata
        "metadata": {
            "bhav_delivery": 2.0,
            "cross_asset": 1.5,
            "participant_mod": 0.5,
        }
        # Option fields NOT populated yet (simulating pre-execution state)
        # "option_type": "",  # Will be empty
        # "option_strike": 0,
        # "option_dte": 0,
    }
    
    # Log as candidate (executed=False, will update later)
    sl.log_candidate(
        signal=signal,
        executed=False,
        rejection_reason="",
        trade_id="",
        india_vix=14.5,
        iv_percentile=45,
        pcr_atm=1.2,
        fii_net_cash=500,
        fii_fut_ratio=1.1,
        fii_cum_5d=1000,
        client_ce_pct=0.55,
        cross_asset_bias="BULLISH",
        sensex_nifty_div=0.2,
        weekly_pivot=23450,
        monthly_pivot=23400,
        weekly_r1=23550,
        monthly_r1=23600,
        expiry_dte=0,
        expiry_regime="0DTE_SCALPING",
        trade_num_today=1,
        daily_pnl_before=100,
    )
    print("   ✅ Candidate logged with executed=0, option_* fields empty\n")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: Query the logged row and verify empty option fields
    # ─────────────────────────────────────────────────────────────────────────
    print("[2] Verifying initial state: option fields are empty/0...")
    
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, executed, option_type, option_strike, option_expiry, "
            "option_dte, option_style, option_premium, option_symbol "
            "FROM signal_log WHERE symbol='NIFTY' AND strategy='PIVOT_SCALPING' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    
    if not row:
        print("   ❌ FAIL: No row found in signal_log\n")
        assert False
    
    row_id, executed, opt_type, opt_strike, opt_expiry, opt_dte, opt_style, opt_premium, opt_symbol = row
    print(f"   Row ID: {row_id}")
    print(f"   executed: {executed} (should be 0)")
    print(f"   option_type: '{opt_type}' (should be empty)")
    print(f"   option_strike: {opt_strike} (should be 0)")
    print(f"   option_dte: {opt_dte} (should be 0)")
    
    if executed != 0 or opt_strike != 0 or opt_dte != 0:
        print("   ❌ FAIL: Initial state incorrect\n")
        assert False
    print("   ✅ Initial state verified: option fields empty\n")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: Call mark_executed() with option_metadata
    # ─────────────────────────────────────────────────────────────────────────
    print("[3] Calling mark_executed() with option metadata...")
    
    trade_id = "TRADE_001"
    # 0DTE scalping: expiry is today (dynamic so the test never goes stale;
    # mark_executed blocks expiry < today as an expired contract).
    today_iso = date.today().isoformat()
    option_metadata = {
        "option_type": "CE",
        "option_strike": 23500,
        "option_expiry": today_iso,
        "option_dte": 0,
        "option_style": "scalping",
        "option_premium": 45.50,
        "option_symbol": "NIFTY23500CE",
    }
    
    result = sl.mark_executed(
        symbol="NIFTY",
        trade_id=trade_id,
        strategy="PIVOT_SCALPING",
        option_metadata=option_metadata,
        require_trade_row=False,
    )
    
    if not result:
        print("   ❌ FAIL: mark_executed() returned False\n")
        assert False
    print("   ✅ mark_executed() successful\n")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Step 4: Query and verify option metadata is now populated
    # ─────────────────────────────────────────────────────────────────────────
    print("[4] Verifying updated state: option fields are populated...")
    
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, executed, trade_id, option_type, option_strike, "
            "option_expiry, option_dte, option_style, option_premium, option_symbol "
            "FROM signal_log WHERE id=?", (row_id,)
        ).fetchone()
    
    if not row:
        print("   ❌ FAIL: Row not found after update\n")
        assert False
    
    (row_id_chk, executed_new, trade_id_chk, opt_type_new, opt_strike_new,
     opt_expiry_new, opt_dte_new, opt_style_new, opt_premium_new, opt_symbol_new) = row
    
    print(f"   Row ID: {row_id_chk}")
    print(f"   executed: {executed_new} (should be 1)")
    print(f"   trade_id: {trade_id_chk} (should be {trade_id})")
    print(f"   option_type: '{opt_type_new}' (should be 'CE')")
    print(f"   option_strike: {opt_strike_new} (should be 23500)")
    print(f"   option_expiry: '{opt_expiry_new}' (should be '{today_iso}')")
    print(f"   option_dte: {opt_dte_new} (should be 0)")
    print(f"   option_style: '{opt_style_new}' (should be 'scalping')")
    print(f"   option_premium: {opt_premium_new} (should be 45.5)")
    print(f"   option_symbol: '{opt_symbol_new}' (should be 'NIFTY23500CE')")
    
    # Verify all fields
    checks = [
        ("executed", executed_new, 1),
        ("trade_id", trade_id_chk, trade_id),
        ("option_type", opt_type_new, "CE"),
        ("option_strike", opt_strike_new, 23500),
        ("option_expiry", opt_expiry_new, today_iso),
        ("option_dte", opt_dte_new, 0),
        ("option_style", opt_style_new, "scalping"),
        ("option_premium", opt_premium_new, 45.5),
        ("option_symbol", opt_symbol_new, "NIFTY23500CE"),
    ]
    
    all_pass = True
    for field_name, actual, expected in checks:
        if actual != expected:
            print(f"   ❌ {field_name}: {actual} != {expected}")
            all_pass = False
    
    if not all_pass:
        print("\n   ❌ FAIL: Some fields don't match\n")
        assert False
    
    print("   ✅ All fields correctly updated\n")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Success!
    # ─────────────────────────────────────────────────────────────────────────
    print("="*70)
    print("✅ TEST PASSED: Option metadata wiring works end-to-end")
    print("="*70 + "\n")
    assert True

if __name__ == "__main__":
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            success = test_option_metadata_wiring(Path(tmp))
        if success is None:
            success = True
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
