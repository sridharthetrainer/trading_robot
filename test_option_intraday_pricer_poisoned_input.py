"""
test_option_intraday_pricer_poisoned_input.py -- poisoned-input
verification for option_intraday_pricer.py's DayPricer/anchoring logic
itself, per the ChatGPT-audit sequencing's step 2 (look-ahead/data-
lineage audit). This is the single most-used pricing primitive this
session -- delta-hedged iron butterfly, naked straddle/strangle,
contrarian-sell, real-option-translation, premium-breakout, and
trading_time_pricer.py's TradingTimeDayPricer all build on it directly
-- yet it had never itself been poisoned-input tested; only DERIVATIVE
logic built on top of it (causal_htf.py, backtest_supertrend_mtf.py) had.

DayPricer's own docstring claims: "anchor_date should be the PREVIOUS
trading day's EOD settle... zero lookahead". This test verifies that
claim empirically rather than trusting the comment: for a real
(anchor_date, expiry, strike, opt_type, eod_settle) combination, poison
(corrupt to NaN) every options_eod row on or after the ACTUAL TRADING
DAY being simulated (never touching the anchor day itself, which is
legitimately T-1 and should be used), and verify that DayPricer -- run
through _prev_trading_day_with_quote's real anchor-selection path --
produces BYTE-IDENTICAL results whether or not that future data exists.
If the anchor-selection logic ever accidentally picked a same-day-or-
later settle (an off-by-one in the backward search), poisoning it would
change the result -- this test would catch that.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, time as dtime
from pathlib import Path

from option_intraday_pricer import DayPricer, nearest_weekly_expiry
from single_leg_intraday_option_backtest import _prev_trading_day_with_quote

OPTIONS_DB = "options_nifty.db"


def main() -> int:
    # Work on a TEMPORARY COPY, never the real production database -- a
    # crash mid-poison-test must never risk corrupting the real,
    # multi-year, multi-million-row options_nifty.db.
    tmp_dir = tempfile.mkdtemp(prefix="poisoned_input_test_")
    tmp_db = str(Path(tmp_dir) / "options_nifty_copy.db")
    shutil.copy2(OPTIONS_DB, tmp_db)
    con = sqlite3.connect(tmp_db)

    # Pick a real, representative trading day with a real quoted chain.
    trading_day_str = con.execute(
        "SELECT date FROM options_eod GROUP BY date HAVING COUNT(*) > 50 "
        "ORDER BY date DESC LIMIT 1 OFFSET 30").fetchone()[0]
    trading_day = date.fromisoformat(trading_day_str)
    expiry = nearest_weekly_expiry(con, trading_day_str)
    strike_row = con.execute(
        "SELECT strike FROM options_eod WHERE date=? AND expiry=? AND opt_type='CE' "
        "ORDER BY ABS(strike - underlying) LIMIT 1", (trading_day_str, expiry)).fetchone()
    strike = strike_row[0]
    print(f"Trading day: {trading_day}  Expiry: {expiry}  Strike: {strike}")

    def build_pricer():
        anchor_day_str, anchor = _prev_trading_day_with_quote(
            con, trading_day, expiry, strike, "CE")
        if not anchor:
            print("FAIL: no anchor found for this real combination")
            return None, None
        eod_settle, eod_underlying = anchor
        anchor_date = date.fromisoformat(anchor_day_str)
        pricer = DayPricer(eod_underlying, strike, anchor_date, date.fromisoformat(expiry),
                            "CE", eod_settle)
        return pricer, anchor_day_str

    pricer_real, anchor_real = build_pricer()
    if pricer_real is None or not pricer_real.valid:
        print("FAIL: real pricer invalid, cannot run test")
        return 1

    # Price at several times through the trading day, using the REAL
    # underlying spot from candle_cache.db-style values (using the
    # anchor's own eod_underlying +/- small moves as a stand-in spot path,
    # since only the PRICER's determinism/lookahead-safety is under test
    # here, not a full backtest).
    test_times = [dtime(9, 30), dtime(11, 0), dtime(13, 0), dtime(15, 0)]
    real_prices = []
    for t in test_times:
        dt = datetime.combine(trading_day, t)
        px = pricer_real.price_at(dt, strike * 1.0)  # ATM-ish spot for a clean read
        real_prices.append(px)

    # ── Poison: corrupt every options_eod row ON OR AFTER trading_day
    # (never touching the anchor day itself, which is legitimately T-1
    # or earlier and SHOULD be used) -- on the TEMP COPY only ─────────
    con.execute("UPDATE options_eod SET settle=NULL, close=NULL, underlying=NULL "
                "WHERE date >= ?", (trading_day_str,))
    con.commit()

    pricer_poisoned, anchor_poisoned = build_pricer()
    poisoned_prices = []
    if pricer_poisoned is not None and pricer_poisoned.valid:
        for t in test_times:
            dt = datetime.combine(trading_day, t)
            px = pricer_poisoned.price_at(dt, strike * 1.0)
            poisoned_prices.append(px)

    con.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"Anchor day (real):     {anchor_real}")
    print(f"Anchor day (poisoned): {anchor_poisoned}")
    print(f"Prices (real):     {real_prices}")
    print(f"Prices (poisoned): {poisoned_prices}")

    if anchor_real == anchor_poisoned and real_prices == poisoned_prices:
        print("PASS: anchor and priced path are byte-identical with all "
              "same-day-or-later data poisoned. No lookahead detected in "
              "DayPricer's own anchoring logic.")
        return 0
    else:
        print("FAIL: anchor or priced path CHANGED when future data was poisoned "
              "-- DayPricer's anchor-selection has a lookahead leak.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
