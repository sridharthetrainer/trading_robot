"""
session_preflight.py -- 8:30 AM broker session/connectivity check.

Gap found 2026-08-19 (external review, verified): nothing in this codebase
runs a dedicated pre-market check that the Angel One session/TOTP is
actually working before the trading loop starts. If the session or TOTP
seed is dead at 9:15 AM, the system silently scans-and-finds-nothing all
day with no operator visibility until someone notices.

Deliberately does NOT place a real test order (the external review's literal
ask): a placed-then-cancelled order carries its own real-money risk if the
cancel fails or a fill races it, and every check this module needs --
authentication, profile access, account/margin access, live market-data
access -- is provable without ever touching the order-placement path.
angel.py's own _ensure_connected() already does the getProfile() session
check; this module adds the account (balance) and market-data (LTP) legs.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("session_preflight")


def run_preflight(angel, *, alerts=None) -> Dict[str, Any]:
    """Runs auth -> profile -> account -> market-data checks in order,
    stopping at the first failure (each step depends on the session being
    genuinely alive, so there's no value probing further once one fails).
    Returns {"ok": bool, "failed_step": str|None, "reason": str}."""
    result: Dict[str, Any] = {"ok": False, "failed_step": None, "reason": ""}

    if angel is None:
        result["failed_step"] = "no_broker"
        result["reason"] = "no broker instance available"
        _alert_failure(alerts, result)
        return result

    try:
        if not angel._ensure_connected():
            result["failed_step"] = "auth_session"
            result["reason"] = "_ensure_connected() returned False (auth/TOTP likely dead)"
            _alert_failure(alerts, result)
            return result
    except Exception as e:
        result["failed_step"] = "auth_session"
        result["reason"] = f"_ensure_connected() raised: {e}"
        _alert_failure(alerts, result)
        return result

    try:
        balance = angel.get_balance(force_real=True)
        if balance is None or balance <= 0:
            result["failed_step"] = "account_balance"
            result["reason"] = f"get_balance() returned {balance!r} -- account/RMS access likely broken"
            _alert_failure(alerts, result)
            return result
        result["balance"] = balance
    except Exception as e:
        result["failed_step"] = "account_balance"
        result["reason"] = f"get_balance() raised: {e}"
        _alert_failure(alerts, result)
        return result

    try:
        ltp = angel.get_ltp("NIFTY", "NSE")
        if not ltp or float(ltp) <= 0:
            result["failed_step"] = "market_data"
            result["reason"] = f"get_ltp('NIFTY') returned {ltp!r} -- market-data access likely broken"
            _alert_failure(alerts, result)
            return result
        result["nifty_ltp"] = float(ltp)
    except Exception as e:
        result["failed_step"] = "market_data"
        result["reason"] = f"get_ltp('NIFTY') raised: {e}"
        _alert_failure(alerts, result)
        return result

    result["ok"] = True
    logger.info("Session preflight OK | balance=%.2f nifty_ltp=%.2f", result["balance"], result["nifty_ltp"])
    if alerts:
        try:
            alerts.send(
                f"Session preflight OK -- balance Rs{result['balance']:,.0f}, "
                f"NIFTY {result['nifty_ltp']:.1f}",
                dedup_key="session_preflight_ok",
            )
        except Exception:
            pass
    return result


def _alert_failure(alerts, result: Dict[str, Any]) -> None:
    logger.critical(
        "SESSION PREFLIGHT FAILED at step=%s: %s", result["failed_step"], result["reason"],
    )
    if alerts:
        try:
            alerts.critical(
                f"SESSION PREFLIGHT FAILED at '{result['failed_step']}': {result['reason']}\n"
                f"New entries halted until this clears."
            )
        except Exception:
            pass
