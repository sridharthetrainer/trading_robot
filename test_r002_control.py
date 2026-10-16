"""R002 random-entry control: fixed seed, reproducibility, draw window, pricing identity."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cpr_vwap_ema_nifty import r002_control as rc

REPO = Path(__file__).resolve().parent


def _table():
    rows = []
    rng = np.random.default_rng(0)
    for d in pd.date_range("2024-01-01", periods=30, freq="B"):
        for i in range(1, 12):
            for code in rc.CODES:
                rows.append({"date": d, "entry_i": i, "in_draw_window": i <= 9, "is_real": i == 2, "code": code,
                             "points": float(rng.normal(2, 20)), "B_net": float(rng.normal(100, 900))})
    return pd.DataFrame(rows)


def test_seed_is_fixed_and_draws_reproducible():
    assert rc.SEED == 20261017 and rc.N_DRAWS == 1000
    t = _table()
    a = rc.draws(t, "E2", n=50)
    b = rc.draws(t, "E2", n=50)
    assert np.array_equal(a["pf"], b["pf"]) and np.array_equal(a["B"], b["B"])
    c = rc.draws(t, "E2", n=50, seed=rc.SEED + 1)
    assert not np.array_equal(a["pf"], c["pf"])
    assert set(a["n"]) == {30}                                           # one trade per day


def test_draws_only_use_in_window_candles():
    t = _table()
    t.loc[~t.in_draw_window, "points"] = 1e6                             # poison out-of-window candles
    d = rc.draws(t, "E2", n=20)
    assert d["pf"].max() < 1000


def test_percentile_summary():
    s = rc.summarize_dist(np.arange(100, dtype=float), 50.0)
    assert s["percentile"] == 50.0 and s["median"] == 49.5


@pytest.mark.skipif(not (REPO / "options_nifty.db").exists(), reason="needs local options_nifty.db")
def test_cached_pricer_identical_to_modelled_layer():
    from datetime import datetime
    from cpr_vwap_ema_nifty.backtest import load_config
    from cpr_vwap_ema_nifty.options_layers import ModelledLayer
    cfg = load_config()
    spot = {"2026-10-14": 22622.7}
    mod, cp = ModelledLayer(cfg, spot), rc.CachedPricer(cfg, spot)
    for k, when, s in ((22650.0, datetime(2026, 10, 15, 9, 20), 22724.85), (22650.0, datetime(2026, 10, 15, 14, 25), 23014.4)):
        assert cp.price("2026-10-15", k, "PE", when, s) == mod.price("2026-10-15", k, "PE", when, s)
