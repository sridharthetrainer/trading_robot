from strategy_finetune_review import review


def evidence(high_return):
    rows = []
    for day in range(1, 21):
        for i in range(10):
            rows.append({'strategy': 'trend', 'signal_date': f'2026-09-{day:02d}',
                         'score': 7 if i < 5 else 5, 'tb_label': 1,
                         'tb_r_multiple_net': (high_return if i < 5 else -1) + day * .001})
    return rows


def test_review_finds_candidate_without_mutating_genomes():
    genomes = {'trend': {'min_score': 5, 'target_atr': 2}}
    report = review(evidence(.5), genomes)
    assert report['counts'] == {'candidate_requires_forward_validation': 1}
    assert genomes == {'trend': {'min_score': 5, 'target_atr': 2}}
    assert report['strategies'][0]['candidate']['target_atr'] == 2


def test_less_negative_is_not_an_acceptable_candidate():
    report = review(evidence(-.5), {'trend': {'min_score': 5}})
    assert report['counts'].get('candidate_requires_forward_validation', 0) == 0


def test_missing_evidence_stays_insufficient():
    report = review([], {'trend': {'min_score': 5}})
    assert report['counts'] == {'insufficient_evidence': 1}
