import json
import requests
import sqlite3


def test_websocket_engine_detects_installed_smartwebsocketv2():
    from websocket_engine import WebSocketEngine

    engine = WebSocketEngine(
        angel_obj=None,
        trade_manager=None,
        trailing=None,
        alerts=None,
    )
    assert engine._check_ws_available() is True


def test_nse_proxy_apply_sets_http_and_https(monkeypatch):
    monkeypatch.setenv("NSE_PROXY", "http://user:pass@example:8080")
    from nse_proxy import apply, get_nse_proxies, is_enabled

    session = requests.Session()
    apply(session)
    assert is_enabled() is True
    assert get_nse_proxies() == {
        "http": "http://user:pass@example:8080",
        "https": "http://user:pass@example:8080",
    }
    assert session.proxies["http"] == "http://user:pass@example:8080"
    assert session.proxies["https"] == "http://user:pass@example:8080"


def test_option_audit_counts_today_journal_rows(tmp_path):
    from datetime import datetime
    from option_bot_audit import _journal_stats

    journal = tmp_path / "option_decision_journal.jsonl"
    today = datetime.now().strftime("%Y-%m-%d")
    journal.write_text(
        "\n".join([
            json.dumps({
                "time": f"{today}T10:00:00+0530",
                "decision": "chain_signal",
                "strikes": [],
            }),
            json.dumps({
                "time": f"{today}T10:05:00+0530",
                "decision": "blocked_quality",
                "strikes": [],
            }),
        ]),
        encoding="utf-8",
    )

    stats = _journal_stats(journal)
    assert stats["today_rows"] == 2
    assert stats["today_chain_signal"] == 1
    assert stats["today_blocked"] == 1


def test_option_signal_evidence_backfills_snapshot_rows(tmp_path):
    from option_signal_evidence import backfill_option_signal_evidence

    db = tmp_path / "snapshots.db"
    journal = tmp_path / "journal.jsonl"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE option_chain_snapshots (
            snapshot_time TEXT,
            underlying TEXT,
            spot REAL,
            expiry TEXT,
            atm_strike REAL,
            summary_json TEXT,
            ok INTEGER
        )
    """)
    conn.execute(
        "INSERT INTO option_chain_snapshots VALUES (?,?,?,?,?,?,?)",
        (
            "2026-06-22T15:30:00+0530",
            "NIFTY",
            25000.0,
            "25-Jun-2026",
            25000.0,
            json.dumps({"net_bias": "BULLISH", "bullish_score": 7, "bearish_score": 2}),
            1,
        ),
    )
    conn.commit()
    conn.close()

    first = backfill_option_signal_evidence(db_path=str(db), journal_path=str(journal))
    second = backfill_option_signal_evidence(db_path=str(db), journal_path=str(journal))
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert rows[0]["decision"] == "chain_snapshot_evidence"
    assert rows[0]["side"] == "BUY"
