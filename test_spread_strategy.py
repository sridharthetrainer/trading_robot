from spread_strategy import SpreadStrategy, LOT_SIZES


# 2026-07-15: LOT_SIZES had drifted stale (NIFTY=75, MIDCPNIFTY=75 — the
# latter ~40% understated vs the correct 120) — a second, independently
# hardcoded copy of a fact nse_master.py already tracks live. Same bug
# class as the expiry-day Thursday->Tuesday fix: a duplicated hardcoded
# assumption that silently drifted out of sync with the source of truth.
_CURRENT_LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120}


def test_fallback_dict_matches_current_nse_lot_sizes():
    assert LOT_SIZES == _CURRENT_LOT_SIZES


def test_lot_size_prefers_live_nse_master_source():
    s = SpreadStrategy()
    for sym, expected in _CURRENT_LOT_SIZES.items():
        assert s._lot_size(sym) == expected, f"{sym}: expected {expected}"


def test_lot_size_falls_back_to_dict_when_nse_master_unavailable(monkeypatch):
    import nse_master

    def _boom(*a, **kw):
        raise RuntimeError("master contract unavailable")

    monkeypatch.setattr(nse_master, "NSEMaster", _boom)
    s = SpreadStrategy()
    assert s._lot_size("MIDCPNIFTY") == 120
