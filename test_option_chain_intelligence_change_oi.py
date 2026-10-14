"""Missing change-in-OI must not create a bearish bias (2026-10-14 fix)."""
import pandas as pd

from option_chain_intelligence import OptionChainIntelligence


def _chain(chg_ce, chg_pe):
    rows = []
    for k in range(24500, 25550, 50):
        rows.append({"strikePrice": k, "CE_openInterest": 100000, "PE_openInterest": 100000,
                     "CE_changeinOpenInterest": chg_ce, "PE_changeinOpenInterest": chg_pe,
                     "CE_totalTradedVolume": 5000, "PE_totalTradedVolume": 5000,
                     "CE_lastPrice": 100, "PE_lastPrice": 100})
    return pd.DataFrame(rows)


def test_no_change_oi_is_not_bearish():
    s = OptionChainIntelligence("NIFTY").analyze(_chain(0, 0), 25000.0)
    assert s.change_oi_available is False
    assert s.oi_buildup_signal == "NO_CHANGE_OI_DATA"
    assert s.pcr_change_oi == 1.0
    assert s.net_bias != "BEARISH"


def test_real_change_oi_still_votes():
    s = OptionChainIntelligence("NIFTY").analyze(_chain(50000, 1000), 25000.0)   # heavy call writing
    assert s.change_oi_available is True
    assert s.pcr_change_oi < 0.9
    assert s.oi_buildup_signal != "NO_CHANGE_OI_DATA"
    # the change-in-OI vote is still applied: heavy call writing adds bearish weight
    none = OptionChainIntelligence("NIFTY").analyze(_chain(0, 0), 25000.0)
    assert s.bearish_score >= none.bearish_score + 2.0


def test_missing_gamma_gives_no_gamma_levels():
    s = OptionChainIntelligence("NIFTY").analyze(_chain(0, 0), 25000.0)   # no gamma columns/zero gamma
    assert s.gamma_support is None and s.gamma_resistance is None
    df = _chain(0, 0).assign(CE_gamma=0.0, PE_gamma=0.0)
    # realistic walls: put OI peaks below spot, call OI peaks above (they cancel)
    df.loc[df["strikePrice"] == 24800, "PE_openInterest"] = 300000
    df.loc[df["strikePrice"] == 25200, "CE_openInterest"] = 300000
    s2 = OptionChainIntelligence("NIFTY").analyze(df, 25000.0)
    assert s2.gamma_support is None and s2.gamma_resistance is None
    assert s2.net_bias == "NEUTRAL"                                       # symmetric chain -> no tilt
