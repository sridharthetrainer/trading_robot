from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from option_scalp_views import latest_scalp_setups, render_scalp_view, scalp_navigation

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def setup_row(*, symbol="NIFTY", age=3600):
    return {"strategy": "OPTION_SCALP", "decision": "selected", "symbol": symbol,
            "side": "SELL", "spot": 25000, "ts": NOW.timestamp() - age,
            "setup_score": 8, "source_id": "scalp_" + symbol,
            "selected": {"strike": 25000, "option_type": "PE"},
            "metadata": {"scalp_evidence": {"trigger": 25010, "stop": 25030,
                                             "target_1": 24970, "target_2": 24955}}}


def render(tmp_path, rows, **kwargs):
    journal = tmp_path / "journal.jsonl"
    journal.write_text("\n".join(json.dumps(row) for row in rows))
    return render_scalp_view(now=NOW, journal_path=journal,
                             status_path=tmp_path / "missing-status.json",
                             setting=lambda name, default: default, **kwargs)


def test_stale_setup_is_labelled_and_underlying_levels_are_explicit(tmp_path):
    text = render(tmp_path, [setup_row()])
    assert "HISTORICAL / EXPIRED" in text
    assert "bearish" in text and "25,000.00 PE" in text
    assert "Underlying SL 25,030.00" in text
    assert "Underlying T1 24,970.00" in text
    assert "not calibrated" in text and "8/10" in text
    assert "80%" not in text
    assert len(text) < 4096


def test_journal_is_read_only_and_filters_non_scalp_rows_and_symbols(tmp_path):
    journal = tmp_path / "journal.jsonl"
    other = {**setup_row(), "strategy": "hero_zero"}
    content = "\n".join(json.dumps(r) for r in [setup_row(), setup_row(symbol="BANKNIFTY"), other])
    journal.write_text(content)
    result = latest_scalp_setups(journal, symbol="NIFTY")
    assert len(result["rows"]) == 1
    assert result["rows"][0]["symbol"] == "NIFTY"
    assert journal.read_text() == content


def test_missing_journal_is_not_created(tmp_path):
    path = tmp_path / "missing.jsonl"
    assert latest_scalp_setups(path)["available"] is False
    assert not path.exists()


def test_recent_signal_is_observation_and_not_order_confirmation(tmp_path):
    text = render(tmp_path, [setup_row(age=30)], mode="signals")
    assert "RECENT · SIGNAL ONLY" in text
    assert "expiry and executable premium are not verified" in text


def test_future_signal_is_not_claimed_fresh(tmp_path):
    text = render(tmp_path, [setup_row(age=-30)])
    assert "TIME UNKNOWN" in text
    assert "RECENT · SIGNAL ONLY" not in text


def test_recent_window_is_bounded_and_skips_malformed_rows(tmp_path):
    path = tmp_path / "journal.jsonl"
    latest = setup_row(age=10)
    path.write_text('x' * 2000 + '\nnot-json\n' + json.dumps(latest) + '\n')
    result = latest_scalp_setups(path, max_bytes=1024)
    assert result["truncated"] and result["rows"] == [latest]


def test_duplicate_source_is_shown_once_and_html_is_escaped(tmp_path):
    row = setup_row(symbol="<NIFTY>")
    text = render(tmp_path, [row, row])
    assert text.count("&lt;NIFTY&gt;") == 1
    assert "<NIFTY>" not in text


def test_status_view_never_scans_or_records_signals(tmp_path, monkeypatch):
    import option_scalper
    monkeypatch.setattr(option_scalper, "scan_and_journal", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not scan")))
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"timestamp": "2026-10-14T10:00:00", "mode": "PAPER",
                                  "trading": {"locked": True}}))
    text = render_scalp_view(mode="status", now=NOW, status_path=status,
                             journal_path=tmp_path / "never-created.jsonl",
                             setting=lambda name, default: default)
    assert "Entry lock: active" in text
    assert "snapshot is stale" in text
    assert not (tmp_path / "never-created.jsonl").exists()


def test_command_routes_return_text_and_navigation(tmp_path, monkeypatch):
    from telegram_commands import TelegramCommandHandler
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPTION_DECISION_JOURNAL", str(tmp_path / "absent.jsonl"))
    handler = TelegramCommandHandler("unused", "42")
    for command in ("scalp", "scalpstatus", "scalpsignals"):
        text, buttons = handler._handlers[command]("/" + command)
        assert "OPTION SCALPING" in text
        assert buttons == scalp_navigation()


def test_options_bootstrap_keeps_all_scalping_commands():
    import ast
    tree = ast.parse((Path(__file__).parent / "main_autonomous.py").read_text())
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "_OPT_ALLOWED" for target in node.targets))
    assert {"scalp", "scalpstatus", "scalpsignals", "optscalp", "scalppnl"} <= ast.literal_eval(assignment.value)
