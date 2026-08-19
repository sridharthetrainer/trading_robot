"""
position_reconciliation.py -- periodic two-directional position reconciliation.

Gap found 2026-08-19 (external review, verified against code): angel.py's
reconcile_positions() fetched Angel's live positions but never compared
them against local tracked state -- the matched/missing_local/missing_angel/
mismatched fields it promised were declared, never populated. Its only
caller (off_hours_engine.py's _run_recon) was itself never called anywhere.
Fixed reconcile_positions() to do the real comparison; this module wires it
into a periodic live check: any nonzero-quantity mismatch on any symbol
halts new entries and alerts, since every risk calculation downstream
(margin circuit breaker, cluster gate, directional caps) is computed from
trade_manager's local state -- a silent drift there means all of those are
computed on fake data.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("position_reconciliation")


def run_reconciliation(angel, trade_manager, *, alerts=None) -> Dict[str, Any]:
    """Compare trade_manager's open positions against Angel's real book.
    Returns the reconcile_positions() result plus a "mismatch" bool. Never
    raises. A data-fetch failure (angel disconnected, API error) returns
    mismatch=False -- a failed fetch isn't evidence of drift, it's evidence
    of nothing; treating it as a mismatch would halt trading every time the
    broker API hiccups, which is its own operational risk."""
    result: Dict[str, Any] = {"mismatch": False, "reason": "", "checked": False}
    try:
        local_positions: Dict[str, int] = {}
        for p in trade_manager.get_open_positions():
            sym = str(p.get("symbol", ""))
            qty = int(p.get("qty", 0) or 0)
            side = str(p.get("side", "")).upper()
            if sym and qty:
                signed_qty = qty if side == "BUY" else -qty
                local_positions[sym] = local_positions.get(sym, 0) + signed_qty

        recon = angel.reconcile_positions(local_positions=local_positions)
        result.update(recon)
        result["checked"] = True

        if recon.get("mismatched") or recon.get("missing_angel") or recon.get("missing_local"):
            result["mismatch"] = True
            details = []
            for m in recon.get("mismatched", []):
                details.append(f"{m['symbol']}: local={m['local_qty']} angel={m['angel_qty']}")
            for m in recon.get("missing_angel", []):
                details.append(f"{m['symbol']}: local={m['local_qty']} angel=MISSING "
                                "(broker doesn't have this position)")
            for m in recon.get("missing_local", []):
                details.append(f"{m['symbol']}: local=MISSING angel={m['angel_qty']} "
                                "(untracked live position!)")
            result["reason"] = "; ".join(details)
            logger.critical("POSITION RECONCILIATION MISMATCH: %s", result["reason"])
            if alerts:
                try:
                    alerts.critical(
                        f"POSITION MISMATCH -- halting new entries until reconciled.\n"
                        f"{result['reason']}"
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.debug("run_reconciliation failed: %s", e)
    return result
