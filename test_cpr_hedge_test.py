"""Test #43 hedge: bear put spread arithmetic (both legs, costs, slippage) and ledger key."""
from datetime import date

import pytest

from cpr_vwap_ema_nifty import hedge_test as ht
from cpr_vwap_ema_nifty.backtest import load_config


def test_spread_net_matches_leg_arithmetic():
    cfg = load_config()
    d = date(2026, 10, 15)
    # long put 100 -> 150, short put 40 -> 55; 1 pt slippage per side per leg, lot 65
    net = ht.spread_net(100.0, 150.0, 40.0, 55.0, d, cfg)
    gross = ((149 - 101) + (39 - 56)) * 65
    costs = ht.leg_costs(101, 149, d, cfg, "OPT_BUY") + ht.leg_costs(56, 39, d, cfg, "OPT_SELL")
    assert net == pytest.approx(gross - costs)
    assert costs > 2 * 20                                       # brokerage on each of four orders at least partly


def test_spread_caps_profit_relative_to_naked():
    cfg = load_config()
    d = date(2026, 10, 15)
    naked = ht.naked_net(100.0, 400.0, d, cfg)                 # big down move
    spread = ht.spread_net(100.0, 400.0, 40.0, 200.0, d, cfg)  # short leg gains back much of it
    assert spread < naked
    assert ht.WIDTH == 200.0


def test_ledger_key():
    assert ht.TEST_KEY == "hedge:R002_bear_put_spread_200"
