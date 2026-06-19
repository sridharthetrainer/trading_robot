"""
test_live_order.py — validate the LIVE order path with (near) ZERO risk.

The live execution path (execution_algo / broker_manager) had an undefined-name
bug until this session and has never placed a correct live order. This verifies
it works WITHOUT risking a fill.

USAGE (run only during market hours, 09:15–15:30 IST):

  1) Read-only (no order at all) — proves connectivity + the get_ltp path that
     execution_algo uses for limit pricing (the exact thing that was broken):
        python test_live_order.py --symbol "<ANGEL_OPTION_SYMBOL>"

  2) Placement test (places a BUY LIMIT far BELOW market → cannot fill, then
     cancels it). Proves order placement + cancellation on the live broker:
        python test_live_order.py --symbol "<ANGEL_OPTION_SYMBOL>" --place

You supply the symbol (you know what's liquid/cheap today). Pick a cheap option.
The limit is set to ~40% of LTP on the BUY side, so it rests unfilled and is
cancelled within seconds. qty = 1 lot.

NOTHING here flips any flag or routes through the autonomous engine.
"""
from __future__ import annotations

import argparse
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("test_live_order")


def main() -> int:
    p = argparse.ArgumentParser(description="Validate live order path with zero fill risk")
    p.add_argument("--symbol", required=True, help="Angel option tradingsymbol, e.g. NIFTY...CE")
    p.add_argument("--exchange", default="NFO")
    p.add_argument("--place", action="store_true",
                   help="actually place a no-fill BUY LIMIT then cancel (default: read-only)")
    p.add_argument("--lots", type=int, default=1)
    args = p.parse_args()

    import config  # loads .env
    if not getattr(config, "ENABLE_REAL_TRADING", False):
        log.warning("ENABLE_REAL_TRADING is False — this would hit the paper broker.")

    from broker_manager import BrokerManager
    bm = BrokerManager()

    # ── 1. Connectivity + balance ──────────────────────────────────────────
    try:
        bal = None
        for b in getattr(bm, "brokers", []):
            if hasattr(b, "get_balance"):
                bal = b.get_balance(force_real=True)
                break
        log.info("Broker balance (live): %s", bal)
    except Exception as e:
        log.error("Balance fetch FAILED: %s", e); return 1

    # ── 2. LTP fetch (the path execution_algo uses for limit pricing) ──────
    ltp = None
    try:
        for b in getattr(bm, "brokers", []):
            if hasattr(b, "get_ltp"):
                ltp = b.get_ltp(args.symbol, exchange=args.exchange)
                if ltp:
                    break
        log.info("LTP of %s = %s", args.symbol, ltp)
    except Exception as e:
        log.error("LTP fetch FAILED: %s", e); return 1
    if not ltp or float(ltp) <= 0:
        log.error("No valid LTP — check the symbol. (Order test skipped.)"); return 1

    if not args.place:
        log.info("READ-ONLY PASS: connectivity + LTP work. Re-run with --place to test order+cancel.")
        return 0

    # ── 3. Place a BUY LIMIT far below market (cannot fill) ─────────────────
    try:
        from nifty_options_engine import _get_lot_size_dynamic
        lot = _get_lot_size_dynamic(args.symbol.split("NIFTY")[0] or "NIFTY")
    except Exception:
        lot = int(getattr(config, "OPTION_LOT_SIZE", 75))
    qty = max(1, lot) * max(1, args.lots)
    limit_price = round(max(0.05, float(ltp) * 0.40), 2)   # 40% of LTP → won't fill
    log.info("Placing NO-FILL BUY LIMIT: %s qty=%d @ ₹%.2f (LTP ₹%.2f)",
             args.symbol, qty, limit_price, float(ltp))

    broker_name, order_id = bm.place_order(
        symbol=args.symbol, qty=qty, buy_sell="BUY",
        order_type="LIMIT", price=limit_price, exchange=args.exchange,
    )
    if not order_id:
        log.error("ORDER PLACEMENT FAILED (no order_id). Broker=%s", broker_name); return 1
    log.info("✅ Order accepted | broker=%s order_id=%s", broker_name, order_id)

    time.sleep(3)
    try:
        st = bm.get_order_status(order_id, exchange=args.exchange)
        log.info("Order status: %s", st)
    except Exception as e:
        log.warning("status check failed (non-fatal): %s", e)

    # ── 4. Cancel it ───────────────────────────────────────────────────────
    cancelled = False
    for b in getattr(bm, "brokers", []):
        if hasattr(b, "cancel_order"):
            try:
                res = b.cancel_order(order_id)
                log.info("Cancel result: %s", res); cancelled = True; break
            except Exception as e:
                log.warning("cancel via %s failed: %s", type(b).__name__, e)
    if cancelled:
        log.info("✅ PLACEMENT TEST PASS: order placed AND cancelled — live path works, no fill.")
        return 0
    log.error("⚠️ Order placed but CANCEL FAILED — CANCEL MANUALLY IN THE APP: order_id=%s", order_id)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
