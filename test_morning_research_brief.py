"""Morning research brief: builds every section, tolerates missing files, read-only."""
import json
from datetime import date
from pathlib import Path

import morning_research_brief as mrb


def test_brief_contains_all_sections():
    text = mrb.build(date(2026, 10, 19))
    for part in ("Research brief", "R001", "R002", "Option snapshots", "calibration", "Journal",
                 "Momentum paper tracker", "Tests run on the backtest dataset"):
        assert part in text, part


def test_brief_tolerates_missing_files(tmp_path, monkeypatch):
    for name in ("FORWARD", "RULES", "LEDGER", "MOMENTUM", "WATCHDOG_STATE", "HEARTBEAT"):
        monkeypatch.setattr(mrb, name, tmp_path / f"missing_{name}")
    text = mrb.build(date(2026, 10, 19))
    assert "no pre-registered rules" in text and "no record yet" in text and "dataset: 0" in text


def test_prev_weekday_and_next_month():
    assert mrb._prev_weekday(date(2026, 10, 19)) == date(2026, 10, 16)          # Monday -> Friday
    assert "next run 2027-01-01" in mrb.section_momentum(date(2026, 12, 10))[0]


def test_brief_is_read_only():
    rules = Path(mrb.RULES)
    before = rules.read_bytes()
    mrb.build(date(2026, 10, 19))
    assert rules.read_bytes() == before


def test_timer_is_weekday_morning():
    timer = (Path(mrb.REPO) / "deploy/systemd/research-brief.timer").read_text()
    assert "OnCalendar=Mon..Fri 08:50" in timer
