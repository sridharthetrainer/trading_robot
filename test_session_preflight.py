"""Tests for session_preflight.py -- the 8:30 AM broker session/connectivity
check gap found 2026-08-19 (nothing verified the Angel session/TOTP was
alive before the trading loop started; a dead session at 9:15 AM meant a
silent all-day scan-and-find-nothing with no visibility).

Deliberately checks auth/account/market-data access without ever placing a
real order -- see the module docstring for why.
"""
import session_preflight as sp


class _FakeAlerts:
    def __init__(self):
        self.criticals = []
        self.sent = []

    def critical(self, msg):
        self.criticals.append(msg)

    def send(self, msg, **kwargs):
        self.sent.append(msg)


class _FakeAngel:
    def __init__(self, *, connected=True, balance=100000.0, ltp=24000.0,
                 raise_on=None):
        self._connected = connected
        self._balance = balance
        self._ltp = ltp
        self._raise_on = raise_on or set()

    def _ensure_connected(self):
        if "auth" in self._raise_on:
            raise RuntimeError("TOTP desync")
        return self._connected

    def get_balance(self, force_real=False):
        if "balance" in self._raise_on:
            raise RuntimeError("RMS API down")
        return self._balance

    def get_ltp(self, symbol, exchange=None):
        if "ltp" in self._raise_on:
            raise RuntimeError("market data down")
        return self._ltp


def test_all_checks_pass():
    alerts = _FakeAlerts()
    result = sp.run_preflight(_FakeAngel(), alerts=alerts)
    assert result["ok"] is True
    assert result["balance"] == 100000.0
    assert result["nifty_ltp"] == 24000.0
    assert alerts.criticals == []
    assert len(alerts.sent) == 1


def test_auth_failure_blocks_and_alerts():
    alerts = _FakeAlerts()
    result = sp.run_preflight(_FakeAngel(connected=False), alerts=alerts)
    assert result["ok"] is False
    assert result["failed_step"] == "auth_session"
    assert len(alerts.criticals) == 1


def test_auth_exception_blocks_and_alerts():
    alerts = _FakeAlerts()
    result = sp.run_preflight(_FakeAngel(raise_on={"auth"}), alerts=alerts)
    assert result["ok"] is False
    assert result["failed_step"] == "auth_session"
    assert "TOTP desync" in result["reason"]


def test_zero_balance_blocks():
    alerts = _FakeAlerts()
    result = sp.run_preflight(_FakeAngel(balance=0.0), alerts=alerts)
    assert result["ok"] is False
    assert result["failed_step"] == "account_balance"


def test_none_balance_blocks():
    alerts = _FakeAlerts()
    result = sp.run_preflight(_FakeAngel(balance=None), alerts=alerts)
    assert result["ok"] is False
    assert result["failed_step"] == "account_balance"


def test_balance_exception_blocks():
    result = sp.run_preflight(_FakeAngel(raise_on={"balance"}))
    assert result["ok"] is False
    assert result["failed_step"] == "account_balance"


def test_zero_ltp_blocks():
    result = sp.run_preflight(_FakeAngel(ltp=0.0))
    assert result["ok"] is False
    assert result["failed_step"] == "market_data"


def test_ltp_exception_blocks():
    result = sp.run_preflight(_FakeAngel(raise_on={"ltp"}))
    assert result["ok"] is False
    assert result["failed_step"] == "market_data"


def test_no_broker_instance_blocks_safely():
    alerts = _FakeAlerts()
    result = sp.run_preflight(None, alerts=alerts)
    assert result["ok"] is False
    assert result["failed_step"] == "no_broker"
    assert len(alerts.criticals) == 1


def test_never_calls_any_order_placement_method():
    """Confirms the preflight genuinely never touches order placement --
    a stub with no place_order-like method must still work fine."""
    angel = _FakeAngel()
    assert not hasattr(angel, "place_order")
    result = sp.run_preflight(angel)
    assert result["ok"] is True
