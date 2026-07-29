import pandas as pd

import modifier_edge_holdout as meh


def _days(n: int):
    return [f"2026-01-{i + 1:02d}" for i in range(n)]


def _synthetic_df(mod_values):
    """mod_values: dict col -> list of (signal_date, mod_value, ret) tuples."""
    rows = []
    all_dates = set()
    for col, entries in mod_values.items():
        for d, _, _ in entries:
            all_dates.add(d)
    cols = set(mod_values.keys())
    # Build one row per entry per modifier column, aligning missing cols to 0.0
    by_key = {}
    for col, entries in mod_values.items():
        for i, (d, val, ret) in enumerate(entries):
            key = (col, i)
            by_key[key] = {"signal_date": d, col: val, "ret": ret}
    frame_rows = list(by_key.values())
    df = pd.DataFrame(frame_rows)
    for col in cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)
    df["ret"] = df["ret"].fillna(0.0)
    return df


def test_stat_and_welch_basic():
    s = meh._stat([1.0, 2.0, 3.0])
    assert s["n"] == 3 and s["mean"] == 2.0
    t, p = meh._welch([1.0, 2.0, 3.0, 4.0], [0.0, 0.0, 0.0, 0.0])
    assert t > 0 and 0 <= p <= 1


def test_confirmed_hurts_when_sign_consistent_train_and_holdout(monkeypatch):
    days = _days(20)
    entries = []
    jitter = [-0.1, -0.05, 0.0, 0.05, 0.1]
    for i, day in enumerate(days):
        # endorsed (mod=1.0) consistently worse than silent (mod=0.0) both halves
        for k in range(5):
            entries.append((day, 1.0, -0.5 + jitter[k]))   # endorsed: bad
            entries.append((day, 0.0, 0.5 + jitter[k]))    # silent: good
    df = _synthetic_df({"sr_level_mod": entries})
    monkeypatch.setattr(meh, "_load_clean", lambda days: (df, {}))
    monkeypatch.setattr(meh, "_MOD_COLS", ["sr_level_mod"])

    rep = meh.run()
    assert rep["labelled_days"] == 20
    block = rep["all_tested"][0]
    assert block["modifier"] == "sr_level_mod"
    assert block["verdict"] == "CONFIRMED_HURTS"
    assert len(rep["confirmed_hurts"]) == 1


def test_train_only_when_holdout_sign_flips(monkeypatch):
    days = _days(20)
    entries = []
    jitter = [-0.1, -0.05, 0.0, 0.05, 0.1]
    for i, day in enumerate(days):
        for k in range(5):
            if i < 14:
                entries.append((day, 1.0, -0.5 + jitter[k]))
                entries.append((day, 0.0, 0.5 + jitter[k]))
            else:
                # holdout: sign flips, endorsed now looks better
                entries.append((day, 1.0, 0.5 + jitter[k]))
                entries.append((day, 0.0, -0.5 + jitter[k]))
    df = _synthetic_df({"flippy_mod": entries})
    monkeypatch.setattr(meh, "_load_clean", lambda days: (df, {}))
    monkeypatch.setattr(meh, "_MOD_COLS", ["flippy_mod"])

    rep = meh.run()
    block = rep["all_tested"][0]
    assert block["verdict"] == "TRAIN_ONLY_SIGN_FLIPPED"


def test_dead_modifier_below_coverage_threshold(monkeypatch):
    days = _days(20)
    entries = [(day, 0.0, 0.1) for day in days for _ in range(10)]
    df = _synthetic_df({"dead_mod": entries})
    monkeypatch.setattr(meh, "_load_clean", lambda days: (df, {}))
    monkeypatch.setattr(meh, "_MOD_COLS", ["dead_mod"])

    rep = meh.run()
    assert rep["all_tested"][0]["verdict"] == "DEAD"


def test_too_few_days_returns_error(monkeypatch):
    df = _synthetic_df({"m": [("2026-01-01", 1.0, 0.1)] * 5})
    monkeypatch.setattr(meh, "_load_clean", lambda days: (df, {}))
    rep = meh.run()
    assert "error" in rep
