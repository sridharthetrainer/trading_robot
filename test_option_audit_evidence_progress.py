import json

from option_bot_audit import _journal_stats, _score_option_bot
from option_decision_journal import label_option_shadow_decisions


def test_pretrade_shadow_outcome_does_not_become_execution(tmp_path):
    journal = tmp_path / 'journal.jsonl'
    journal.write_text(json.dumps({
        'decision': 'selected_pre_trade', 'source_id': 'observation-123',
        'trade_id': '', 'is_live_data': True, 'data_source': 'angel',
        'strikes': [{'strike': 25000, 'option_type': 'CE'}],
    }) + '\n')
    updated = label_option_shadow_decisions('observation-123', [{
        'strike': 25000, 'option_type': 'CE', 'pnl': 5, 'label': 1,
    }], path=str(journal))
    assert updated == 1
    stats = _journal_stats(journal)
    assert stats['verified_shadow_outcomes'] == 1
    assert stats['executed_selected'] == 0
    assert json.loads(journal.read_text())['trade_id'] == ''


def test_progress_reports_actual_threshold_and_missing_execution():
    score = _score_option_bot({
        'option_chain_snapshots': {'verified_live_rows': 25, 'verified_strike_outcomes': 150},
        'decision_journal': {'verified_selected': 1},
    })
    assert score['minimum_verified_outcomes'] == 100
    assert score['evidence_progress']['signal_outcomes'] == {'observed': 150, 'required': 100}
    assert score['evidence_progress']['executed_selected_records']['observed'] == 0
    assert 'no_executed_option_fill_evidence' in score['evidence_blocks']
    assert any('Reconcile actual executed' in s for s in score['top_improvements'])
    assert score['total'] <= 59
