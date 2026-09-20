import pairs_stat_arb_validation as pairs
import numpy as np
import pandas as pd


def test_uses_residual_cointegration_threshold():
    assert pairs.ENGLE_GRANGER_CRIT_5PCT <= -3.3


def test_pair_scan_bonferroni_corrects_return_evidence(monkeypatch):
    seen = []

    def fake_validate(a, b, alpha=0.05):
        seen.append(alpha)
        return {"pair": f"{a}-{b}", "verdict": "NO_EDGE"}

    monkeypatch.setattr(pairs, "validate", fake_validate)
    report = pairs.scan_pairs([("A", "B"), ("C", "D")])

    assert seen == [0.025, 0.025]
    assert report["multiple_test_alpha"] == 0.025


def test_non_cointegrated_pair_is_no_edge_not_insufficient(monkeypatch):
    values = np.linspace(100.0, 200.0, 100)
    frame = pd.DataFrame({"a": values, "b": values ** 1.5})
    monkeypatch.setattr(pairs, "_load_aligned", lambda *_: frame)
    monkeypatch.setattr(pairs, "_adf_tstat", lambda _: -2.0)

    result = pairs.validate("A", "B")

    assert result["cointegration"]["cointegrated"] is False
    assert result["verdict"] == "NO_EDGE"
