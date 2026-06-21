from autonomous_param_trainer import run_autonomous_param_training


def test_autonomous_param_trainer_dry_run_plan():
    report = run_autonomous_param_training(
        strategies=["trend", "breakout"],
        symbols=["NIFTY"],
        max_runs=2,
        dry_run=True,
        write=False,
    )

    assert report["dry_run"] is True
    assert len(report["planned"]) == 2
    assert report["promoted"] == []
    assert report["paper_only"] == []
    assert report["skipped"] == []
    assert report["days"] == 210
