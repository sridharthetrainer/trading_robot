#!/usr/bin/env python3
"""
chaos_tests.py — offline fault-injection diagnostics (audit gap #12).

Chaos testing surfaces where the system fails when its dependencies do. This
runs SAFE, offline fault injections (no network, no broker login, no market) and
reports per-scenario whether the code degrades gracefully (✅) or crashes /
behaves unsafely (❌). Scenarios that genuinely need a live/staging environment
are reported as skipped (⏭) rather than faked green.

It is a DIAGNOSTIC, not a pass/fail gate — it always runs to completion and
prints a summary, so a ❌ is information, not a hard error.

Usage:
    python chaos_tests.py
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Callable, List, Tuple

Result = Tuple[str, str, str]  # (name, status, detail)  status ∈ {ok, fail, skip}


@contextmanager
def _patched(obj, attr, value):
    had = hasattr(obj, attr)
    old = getattr(obj, attr, None)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        if had:
            setattr(obj, attr, old)
        else:
            try:
                delattr(obj, attr)
            except Exception:
                pass


@contextmanager
def _chdir(path: str):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


# ── Scenarios ────────────────────────────────────────────────────────────────

def s_telegram_outage() -> Result:
    """Telegram API down → AlertManager.send must swallow, never crash callers."""
    try:
        import alerts
        def _boom(*a, **k):
            raise ConnectionError("simulated telegram outage")
        # The failure path spools to a CWD-relative telegram_spool.jsonl; run in
        # a temp dir so the injected messages never enter the real retry spool.
        with tempfile.TemporaryDirectory() as tmp, _chdir(tmp):
            am = alerts.AlertManager(bot_token="0:test", chat_id="1")
            # no-op sleep so the real retry/backoff (3× exponential) runs instantly
            with _patched(alerts.requests, "post", _boom), \
                 _patched(alerts.time, "sleep", lambda *a, **k: None):
                am.send("chaos test")          # must not raise
                am.critical("chaos critical")  # must not raise
        return ("telegram_outage", "ok", "send/critical swallowed the outage")
    except Exception as exc:
        return ("telegram_outage", "fail", f"propagated: {exc!r}")


def s_option_chain_all_sources_down() -> Result:
    """Every option-chain source fails → fetch() returns None, no crash."""
    try:
        import option_chain_fetcher as ocf
        import data_source_resilience as dsr
        import sensibull_client as sbc
        def _boom(*a, **k):
            raise RuntimeError("source down")
        f = ocf.NSEOptionChainFetcher(underlying="NIFTY")
        with _patched(dsr, "fetch_option_chain", _boom), \
             _patched(sbc, "fetch_option_chain", _boom), \
             _patched(f, "_market_open", lambda *a, **k: True), \
             _patched(f, "_fetch_live", lambda *a, **k: None), \
             _patched(f, "_fetch_from_angel", lambda *a, **k: None), \
             _patched(f, "_load_cache", lambda *a, **k: None):
            out = f.fetch()
        if out is None:
            return ("option_chain_all_down", "ok", "fetch() returned None gracefully")
        return ("option_chain_all_down", "fail", f"expected None, got {type(out).__name__}")
    except Exception as exc:
        return ("option_chain_all_down", "fail", f"crashed: {exc!r}")


def s_db_missing() -> Result:
    """Missing DB → capital_simulation returns [] instead of crashing."""
    try:
        import capital_simulation as cs
        rows = cs.load_trade_returns(db_path="/tmp/_chaos_does_not_exist.db")
        if isinstance(rows, list):
            return ("db_missing", "ok", f"returned {len(rows)} rows (graceful)")
        return ("db_missing", "fail", f"non-list: {type(rows).__name__}")
    except Exception as exc:
        return ("db_missing", "fail", f"crashed: {exc!r}")


def s_db_corrupt() -> Result:
    """Corrupt DB file → graceful empty result."""
    try:
        import capital_simulation as cs
        fd, path = tempfile.mkstemp(suffix=".db")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"not a sqlite database \x00\x01\x02 garbage")
        try:
            rows = cs.load_trade_returns(db_path=path)
        finally:
            os.remove(path)
        if isinstance(rows, list):
            return ("db_corrupt", "ok", "handled corrupt file (graceful)")
        return ("db_corrupt", "fail", f"non-list: {type(rows).__name__}")
    except Exception as exc:
        return ("db_corrupt", "fail", f"crashed: {exc!r}")


def s_registry_unwritable() -> Result:
    """Experiment registry on an unwritable path → log_result returns None."""
    try:
        import experiment_registry as er
        class _R:
            strategy = "chaos"; symbol = "X"; best_params = {}
            n_trials = 0; dev_avg_sharpe = 0.0; holdout_sharpe = None
            deflated_sharpe = 0.0; beats_benchmark = False; verdict = "FAIL"
        out = er.log_result(_R(), timeframe="5m", db_path="/proc/cannot_write_here.db")
        # log_result is best-effort: must return None (or a hash) without raising
        return ("registry_unwritable", "ok", f"log_result handled it (returned {out!r})")
    except Exception as exc:
        return ("registry_unwritable", "fail", f"crashed: {exc!r}")


def s_dashboard_no_reports() -> Result:
    """All report files + DB absent → dashboard renders placeholders, no crash."""
    try:
        import daily_dashboard as dd
        with tempfile.TemporaryDirectory() as tmp, _chdir(tmp):
            report = dd.build_report()
        if isinstance(report, str) and "(no " in report:
            return ("dashboard_no_reports", "ok", "rendered placeholders gracefully")
        return ("dashboard_no_reports", "fail", "did not render expected placeholders")
    except Exception as exc:
        return ("dashboard_no_reports", "fail", f"crashed: {exc!r}")


# Scenarios that need a live/staging environment — not faked green here.
_STAGING_ONLY = [
    ("angel_api_failure", "needs staging: live Angel session to drop mid-fetch"),
    ("token_expiry",      "needs staging: expire a live SmartAPI session token"),
    ("internet_loss",     "needs staging: cut network on a running bot"),
    ("market_halt",       "needs staging: exchange halt / circuit during market hours"),
]


def run() -> List[Result]:
    scenarios: List[Callable[[], Result]] = [
        s_telegram_outage,
        s_option_chain_all_sources_down,
        s_db_missing,
        s_db_corrupt,
        s_registry_unwritable,
        s_dashboard_no_reports,
    ]
    results = [fn() for fn in scenarios]
    results += [(name, "skip", why) for name, why in _STAGING_ONLY]
    return results


def main() -> int:
    results = run()
    icon = {"ok": "✅", "fail": "❌", "skip": "⏭"}
    print("\nCHAOS TESTS (offline fault injection)")
    print("-" * 60)
    for name, status, detail in results:
        print(f"  {icon.get(status,'?')} {name:26s} {detail}")
    n_ok   = sum(1 for _, s, _ in results if s == "ok")
    n_fail = sum(1 for _, s, _ in results if s == "fail")
    n_skip = sum(1 for _, s, _ in results if s == "skip")
    print("-" * 60)
    print(f"  {n_ok} graceful, {n_fail} failed, {n_skip} need staging")
    # Exit non-zero only if an offline scenario actually crashed.
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
