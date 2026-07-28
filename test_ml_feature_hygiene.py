import numpy as np
import pandas as pd

from ml_trainer import _usable_feature_cols


def test_unusable_features_are_removed_with_reasons():
    frame = pd.DataFrame({
        "good": np.arange(100, dtype=float),
        "constant": [1.0] * 100,
        "all_null": [np.nan] * 100,
        "near_constant": [0.0] * 99 + [1.0],
    })
    usable, removed = _usable_feature_cols(frame, list(frame.columns))
    assert usable == ["good"]
    assert removed == {
        "constant": "constant",
        "all_null": "all_null",
        "near_constant": "near_constant",
    }
