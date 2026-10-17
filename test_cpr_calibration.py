"""Modelled vs real option P&L calibration: collection, de-duplication, summaries."""
import pandas as pd

from cpr_vwap_ema_nifty import calibration as cal


def _write(root, rel, rows):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(p, index=False)


def test_collect_dedupes_and_requires_both_prices(tmp_path):
    t = {"date": "2026-10-01", "entry_time": "2026-10-01 09:30", "exit_time": "2026-10-01 10:30",
         "strike": 25000.0, "opt_type": "PE", "points": 20.0, "B_net": 1000.0, "C_net": 800.0}
    _write(tmp_path, "latest/trades.csv", [t, {**t, "entry_time": "2026-10-01 11:00", "C_net": None}])
    _write(tmp_path, "latest/exit_test_trades.csv", [t])                       # same trade again -> counted once
    _write(tmp_path, "latest/conditions_trades.csv",
           [{**t, "date": "2026-10-02", "opt_type": "CE", "points": -10.0, "B_net": -500.0, "C_net": -600.0}])
    df = cal.collect(tmp_path)
    assert len(df) == 2
    rep = cal.report(tmp_path)
    assert rep["all"]["trades"] == 2 and rep["all"]["real_minus_model_per_trade"] == -150.0
    assert rep["PE"]["trades"] == 1 and rep["winners"]["trades"] == 1 and rep["losers"]["trades"] == 1
    assert "real minus model -150" in cal.line(rep)


def test_line_when_empty(tmp_path):
    assert "no trades" in cal.line(cal.report(tmp_path))
