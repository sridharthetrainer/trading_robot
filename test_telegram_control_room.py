import json

import telegram_views
from telegram_commands import TelegramCommandHandler


def test_control_room_summarizes_live_gate_without_mutation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    monkeypatch.setenv("LIVE_PROBATION_ENABLED", "true")
    monkeypatch.setenv("LIVE_PROBATION_MAX_LOTS", "1")
    monkeypatch.setenv("LIVE_PROBATION_MAX_TRADES_PER_DAY", "1")
    monkeypatch.setenv("LIVE_PROBATION_ALLOWED_STATUSES", "PAPER_PROMISING,LIVE_EVIDENCE_READY")

    (tmp_path / "system_readiness_report.json").write_text(json.dumps({
        "mode": {"live_ready_count": 0, "total_strategies": 104},
        "execution": {"net_pnl": -625.47},
        "learning": {"edge_conclusion": "NO significant edge survives cost"},
    }))
    (tmp_path / "ml_training_last_result.json").write_text(json.dumps({
        "cross_symbol": {
            "champion_algorithm": "random_forest",
            "cv_auc_mean": 0.7478,
            "promoted": False,
            "profit_utility": {
                "best_avg_net_r": 0.033723,
                "best_threshold": 0.65,
                "best_selected": 1166,
            },
        }
    }))
    (tmp_path / "learned_filters.json").write_text(json.dumps({
        "active": False,
        "filters": [],
        "boosts": [],
        "model_promoted": False,
    }))
    (tmp_path / "profit_discipline_report.json").write_text(json.dumps({
        "summary": {"QUARANTINED": 36, "VALIDATING": 65, "PAPER_PROMISING": 1}
    }))
    (tmp_path / "option_bot_audit_report.json").write_text(json.dumps({
        "score": {"total": 91, "grade": "A", "readiness": "PAPER_PROMISING"},
        "option_chain_snapshots": {"rows": 3782, "verified_strike_outcomes": 19257},
        "decision_journal": {"today_selected": 8, "today_blocked": 21},
    }))

    text = telegram_views.control_room()

    assert "TRADING CONTROL ROOM" in text
    assert "real orders OFF" in text
    assert "Probation: ON" in text
    assert "Model promoted: ❌ no" in text
    assert "Quarantined 36" in text
    assert "Audit 91/100 A" in text
    assert "read-only" in text


def test_control_room_command_aliases_registered():
    handler = TelegramCommandHandler(bot_token="x", chat_id="1")
    for command in ("controlroom", "readiness", "profitgate", "go"):
        assert handler._handlers[command] == handler._cmd_control_room
