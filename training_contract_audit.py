#!/usr/bin/env python3
"""Runtime audit of point-in-time ML features and feedback-loop promotion gates."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict

REPORT_FILE = "training_contract_audit.json"


def _signal_counts(path: str = "signal_log.db") -> Dict[str, int]:
    if not Path(path).exists():
        return {"legacy_labelled": 0, "clean_labelled": 0, "clean_days": 0}
    with sqlite3.connect(path) as conn:
        row = conn.execute("""
            SELECT
              SUM(CASE WHEN tb_label IN (-1,0,1) THEN 1 ELSE 0 END),
              SUM(CASE WHEN tb_label IN (-1,0,1) AND training_eligible=1
                        AND stop_loss>0 AND target>0 AND rr>0 THEN 1 ELSE 0 END),
              COUNT(DISTINCT CASE WHEN tb_label IN (-1,0,1) AND training_eligible=1
                        AND stop_loss>0 AND target>0 AND rr>0 THEN signal_date END)
            FROM signal_log
        """).fetchone()
    return {
        "legacy_labelled": int(row[0] or 0),
        "clean_labelled": int(row[1] or 0),
        "clean_days": int(row[2] or 0),
    }


def build_training_contract_audit(*, report_file: str = REPORT_FILE, write: bool = True) -> Dict[str, Any]:
    import pandas as pd
    import learned_filters
    import ml_trainer
    from eod_weight_engine import get_strategy_weight
    from signal_calibrator import get_calibrator

    probe = pd.DataFrame({
        "score": [1.0], "volume_ratio": [1.0], "tb_outcome": [1],
        "tb_label": [1], "tb_r_multiple": [1.5], "tb_r_multiple_net": [1.2],
        "outcome_price": [101.0], "net_pnl": [20.0], "labelled_at": [1.0],
    })
    selected = ml_trainer._feature_cols(probe)
    forbidden_selected = sorted(set(selected) & ml_trainer._OUTCOME_ONLY_COLS)
    prediction = ml_trainer.predict({"score": 1.0, "volume_ratio": 1.0})
    legacy_model_safe = bool(prediction.get("available")) or prediction.get("reason") in {
        "legacy_training_contract", "model_not_promoted", "ordered_feature_contract_missing",
    } or prediction.get("model_used") == "none"
    learned_probe = learned_filters.apply_learned_filters({"score": 9.0})
    calibrator = get_calibrator()
    counts = _signal_counts()
    checks = {
        "outcome_fields_excluded": not forbidden_selected,
        "model_artifact_contract_enforced": legacy_model_safe,
        "legacy_learned_filters_neutral": float(learned_probe.get("mult", 1.0)) == 1.0,
        "legacy_eod_weights_neutral": float(get_strategy_weight("__audit_probe__", 1.0)) == 1.0,
        "calibrator_contract_enforced": (
            not calibrator.is_trained()
            or calibrator._meta.get("training_contract") == "all_generated_signals_v2_purged_calibration"
        ),
    }
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "training_contract": ml_trainer.TRAINING_CONTRACT,
        "checks": checks,
        "ok": all(checks.values()),
        "forbidden_selected": forbidden_selected,
        "ordered_features": selected,
        "primary_model": prediction,
        "clean_evidence": counts,
        "promotion_targets": {
            "samples": ml_trainer.MIN_PROMOTION_SAMPLES,
            "days": ml_trainer.MIN_PROMOTION_DAYS,
            "min_auc": ml_trainer.MIN_PROMOTION_AUC,
            "positive_brier_skill": True,
        },
    }
    if write:
        Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(build_training_contract_audit(), indent=2, default=str))
