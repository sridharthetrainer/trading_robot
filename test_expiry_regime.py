from datetime import date

from expiry_regime import get_expiry_day_of_week, get_expiry_regime
from time_regime import is_expiry_day


# Confirmed real expiry dates from this system's own live option-chain data
# (option_chain_snapshots.db, NIFTY/BANKNIFTY/FINNIFTY, 2026-06 through
# 2026-07) — every single recorded expiry fell on a Tuesday, none on a
# Thursday. 2026-07-14: NSE moved index expiry from Thursday to Tuesday on
# 2025-09-01; this module was still hardcoded to Thursday for ~10 months.
_CONFIRMED_NIFTY_EXPIRIES = [
    date(2026, 6, 23), date(2026, 6, 30), date(2026, 7, 7), date(2026, 7, 28),
]
_ORDINARY_THURSDAY = date(2026, 7, 2)  # not an expiry under the new regime


def test_confirmed_real_expiries_are_recognized():
    for d in _CONFIRMED_NIFTY_EXPIRIES:
        assert d.weekday() == 1, f"sanity: {d} must actually be a Tuesday"
        r = get_expiry_regime(today=d, symbol="NIFTY")
        assert r["is_expiry_day"] is True, f"{d} should be recognized as expiry day"


def test_ordinary_thursday_is_not_falsely_flagged():
    """The exact regression this bug caused: Thursdays used to be
    incorrectly flagged as expiry day, suppressing breakout/trend and
    boosting mean-reversion on ordinary trading days."""
    r = get_expiry_regime(today=_ORDINARY_THURSDAY, symbol="NIFTY")
    assert r["is_expiry_day"] is False
    assert r["regime_label"] == "NORMAL"


def test_monthly_vs_weekly_expiry_labelled_correctly():
    # 2026-06-30 and 2026-07-28 are month-end Tuesdays (monthly expiry);
    # 2026-06-23 and 2026-07-07 are ordinary weekly Tuesdays.
    monthly = get_expiry_regime(today=date(2026, 6, 30), symbol="NIFTY")
    weekly = get_expiry_regime(today=date(2026, 6, 23), symbol="NIFTY")
    assert monthly["regime_label"] == "MONTHLY_EXPIRY"
    assert weekly["regime_label"] == "WEEKLY_EXPIRY"


def test_day_of_week_table_matches_verified_live_data():
    table = {s: get_expiry_day_of_week(s) for s in
             ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")}
    assert table == {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1}


def test_time_regime_is_expiry_day_matches_expiry_regime():
    """time_regime.py had its OWN separate Thursday hardcode, disconnected
    from expiry_regime.py — both must now agree on Tuesday."""
    assert get_expiry_day_of_week("NIFTY") == 1
    # time_regime.is_expiry_day() checks the real clock, so we only assert
    # its weekday constant agrees with expiry_regime's, not a live date.
    import inspect
    src = inspect.getsource(is_expiry_day)
    assert "weekday() == 1" in src


def test_bankex_matches_sensex_not_the_nse_default():
    """Code review 2026-07-14: BANKEX was silently falling through to the
    Tuesday NSE default (an accident of how that default was written, not
    a reasoned decision) instead of following its own exchange (BSE, same
    as SENSEX)."""
    assert get_expiry_day_of_week("BANKEX") == get_expiry_day_of_week("SENSEX") == 3


def test_option_chain_engine_expiry_table_matches_expiry_regime():
    """Code review 2026-07-14: option_chain_engine.py's EXPIRY_WEEKDAY
    fallback (used for REAL expiry selection when the master-contract
    lookup fails, not just a scoring nudge) had drifted from this module's
    values — it still had BANKNIFTY=Wednesday/MIDCPNIFTY=Monday/SENSEX=
    Friday after this module was fixed to Tuesday/Tuesday/Thursday. Both
    tables must agree for every symbol they share."""
    from option_chain_engine import OptionChainEngine
    table = OptionChainEngine.EXPIRY_WEEKDAY
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50",
                "SENSEX", "BANKEX"):
        assert table[sym] == get_expiry_day_of_week(sym), (
            f"{sym}: option_chain_engine={table[sym]} "
            f"expiry_regime={get_expiry_day_of_week(sym)}"
        )
