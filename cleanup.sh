#!/bin/bash
# cleanup.sh — Remove obsolete files from trading_robot
# Run from: ~/Desktop/trading_robot/
# Safe to run: bot.sh stop first, then run this, then bot.sh start

set -e
cd "$(dirname "$0")"

echo "╔══════════════════════════════════════════════════╗"
echo "   TRADING ROBOT — FILE CLEANUP"
echo "   Removes 57 obsolete files, keeps 157 active"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "⚠️  Stop the bot first: ./bot.sh stop"
echo "Press ENTER to continue or Ctrl+C to cancel..."
read

# ── 1. Old individual backtest scripts (18 files) ─────────────────────────────
echo "Removing old backtest scripts..."
rm -f backtest_5min_ema.py backtest_breakout_grid.py backtest_breakout.py
rm -f backtest_iron_condor.py backtest_ma_cross.py backtest_ma_grid.py
rm -f backtest_mean_reversion.py backtest_mr_enhanced.py backtest_mr_grid.py
rm -f backtest_mr_validate_outofsample.py backtest_mr_validate.py backtest_orb.py
rm -f backtest.py backtest_scalping.py backtest_supertrend_mtf.py
rm -f backtest_trend_grid.py backtest_trend.py backtest_vwap_reversion.py

# ── 2. Old CSV / JSON results ──────────────────────────────────────────────────
echo "Removing old result files..."
rm -f backtest_breakout_NIFTY.csv backtest_ma_NIFTY.csv
rm -f backtest_NIFTY.csv backtest_trend_NIFTY.csv
rm -f equity_NIFTY.csv backtest_results_full.json strategy_results.json

# ── 3. Log files (auto-regenerated) ───────────────────────────────────────────
echo "Removing old log files..."
rm -f after_hours_signal.log main_live.log watchdog.log
rm -f system.log system_error.log training.log

# ── 4. Old runner scripts (replaced by main_autonomous.py) ────────────────────
echo "Removing old runner scripts..."
rm -f main.py run_backtest.py run_nifty_options.py run_system.py
rm -f run_system.pid run_system_state.db run_system_state.json

# ── 5. Superseded config files ────────────────────────────────────────────────
echo "Removing superseded configs..."
rm -f capital_config.py config_scalping.py

# ── 6. Absorbed/obsolete modules ──────────────────────────────────────────────
echo "Removing absorbed modules..."
rm -f ai_regime_switcher.py   # absorbed into regime.py
rm -f database.py              # replaced by trade_manager.py
rm -f distributed_engine.py   # not used (single machine)
rm -f fyers_data_feed.py       # Fyers broker, using Angel One
# logger_setup.py KEPT — angel.py depends on it
rm -f multi_tracker.py         # absorbed
rm -f nse_reference.py         # reference only, not imported
rm -f scheduler.py             # absorbed into main_autonomous.py
rm -f skip_journal.py          # old utility
rm -f skip_journal.db          # old utility DB
rm -f spot_filter.py           # absorbed
rm -f sri.py                   # unknown utility

# ── 7. One-time utilities ──────────────────────────────────────────────────────
echo "Removing one-time utilities..."
rm -f check_project_files.py convert_master.py

# ── 8. Old test files (keep test_all_files.py and test_system.py) ─────────────
echo "Removing old dev test files..."
rm -f test_angel_chain.py test_nifty_options_engine.py test_option_chain.py

echo ""
echo "✅ Cleanup complete!"
echo ""
echo "Files remaining:"
ls *.py 2>/dev/null | wc -l
echo " .py files"
ls *.csv 2>/dev/null | wc -l
echo " .csv files"
echo ""
echo "Start the bot: ./bot.sh start"
