import retail_algo_readiness_report as report


def test_readiness_report_fails_closed_without_external_approvals(monkeypatch, tmp_path):
    monkeypatch.delenv("RETAIL_ALGO_STATIC_IPS", raising=False)
    monkeypatch.delenv("BROKER_RETAIL_ALGO_REGISTERED", raising=False)
    monkeypatch.delenv("OPTION_STRATEGY_EXCHANGE_REGISTERED", raising=False)
    result = report.write_report(str(tmp_path / "readiness.json"))
    assert result["live_ready"] is False
    assert result["policy"] == "fail_closed_external_approvals_required"
    assert "static_ip" in result["blocks"]
