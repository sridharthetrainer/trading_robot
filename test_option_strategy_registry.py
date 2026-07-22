import json
from datetime import datetime

import option_strategy_registry as reg

_EXPECTED_IDS = (
    [f"A{i}" for i in range(1, 15)] + [f"B{i}" for i in range(1, 6)]
    + [f"C{i}" for i in range(1, 10)] + [f"D{i}" for i in range(1, 8)]
    + [f"E{i}" for i in range(1, 8)]
)


def test_all_catalog_ids_present():
    # The user's spec header claims "47 strategies" but its own activation
    # matrix and detailed catalog only ever enumerate 42 (A1-A14=14, B1-B5=5,
    # C1-C9=9, D1-D7=7, E1-E7=7) -- that mismatch is in the source document,
    # this asserts against what was actually specified.
    ids = {d.id for d in reg.OPTION_STRATEGY_CATALOG}
    assert ids == set(_EXPECTED_IDS)
    assert len(reg.OPTION_STRATEGY_CATALOG) == 42


def test_activation_matrix_matches_spec():
    core = set(reg.core_ids())
    assert core == {"A1", "A2", "A8", "A13", "B1", "B2", "C1", "C3", "C4", "C8", "D1", "E2"}
    assert len(reg.by_activation_status("ADVANCED")) == 18
    assert len(reg.by_activation_status("DANGEROUS")) == 4
    assert len(reg.by_activation_status("DISABLED")) == 7
    skipped = reg.by_activation_status("SKIPPED")
    assert [d.id for d in skipped] == ["D5"]
    assert reg.get_strategy("D5").risk_profile == "not_implemented"


def test_only_c1_c3_and_d1_are_implemented_this_pass():
    assert reg.implemented_ids() == ["C1", "C3", "D1"]
    for strategy_def in reg.OPTION_STRATEGY_CATALOG:
        if strategy_def.id in ("C1", "C3", "D1"):
            assert strategy_def.logic_status == "IMPLEMENTED"
            assert strategy_def.evaluate_fn is not None
        else:
            assert strategy_def.logic_status == "PLACEHOLDER"
            assert strategy_def.evaluate_fn is None


class _RaisingRecorder:
    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        raise AssertionError("record_option_decision must not be called for a placeholder strategy")


def test_placeholder_strategies_are_inert(monkeypatch):
    fake = _RaisingRecorder()
    monkeypatch.setattr("option_decision_journal.record_option_decision", fake)
    for strategy_def in reg.OPTION_STRATEGY_CATALOG:
        if strategy_def.logic_status == "IMPLEMENTED":
            continue
        result = reg._placeholder_result(strategy_def, "NIFTY")
        assert result["status"] == "not_active"
        assert strategy_def.activation_status.lower() in result["reason"]
    assert fake.calls == 0


def test_params_all_labeled_unvalidated():
    params = json.loads(open(reg.PARAMS_FILE).read())
    for key, entry in params.items():
        if key == "_meta":
            continue
        assert entry.get("unvalidated") is True, f"{key} missing unvalidated=true"


def test_entry_window_range_form():
    now = datetime(2026, 7, 16, 10, 0)
    assert reg.entry_window_ok("09:30-11:00", now) is True
    assert reg.entry_window_ok("09:30-11:00", datetime(2026, 7, 16, 11, 30)) is False
    # boundary
    assert reg.entry_window_ok("09:30-11:00", datetime(2026, 7, 16, 9, 30)) is True
    assert reg.entry_window_ok("09:30-11:00", datetime(2026, 7, 16, 11, 0)) is True


def test_entry_window_sharp_form_with_tolerance():
    """2026-07-20: was a +/-3min SYMMETRIC tolerance. Production showed real
    per-symbol cycle latency (API timeouts + retries) routinely pushes a
    symbol's evaluation past a narrow window entirely -- confirmed live:
    NIFTY's chain fetch logged at 09:20:32/09:20:46 (inside the OLD window)
    yet C1 produced zero journal entries all day. Fixed to fire from a
    small pre-target tolerance through a post-target catch-up window
    instead (spec's own C1 doc explicitly allows a "09:35 variant")."""
    target = datetime(2026, 7, 16, 9, 20)
    assert reg.entry_window_ok("09:20_sharp", target) is True
    assert reg.entry_window_ok("09:20_sharp", datetime(2026, 7, 16, 9, 22)) is True
    assert reg.entry_window_ok("09:20_sharp", datetime(2026, 7, 16, 9, 34)) is True    # catch-up window
    assert reg.entry_window_ok("09:20_sharp", datetime(2026, 7, 16, 9, 19)) is True    # small pre-target tolerance
    assert reg.entry_window_ok("09:20_sharp", datetime(2026, 7, 16, 9, 36)) is False   # past catch-up deadline
    assert reg.entry_window_ok("09:20_sharp", datetime(2026, 7, 16, 9, 18)) is False   # too early


def test_entry_window_one_shot_dedup_prevents_repeat_fires(monkeypatch):
    """The wider catch-up window means a one-shot strategy is now open for
    ~16 minutes of scan cycles -- without dedup it could sell a fresh
    straddle every cycle. evaluate_catalog must fire it at most once/day."""
    assert reg.is_one_shot_window("09:20_sharp") is True
    assert reg.is_one_shot_window("09:30-11:00") is False

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 20, 9, 21)

    monkeypatch.setattr(reg, "datetime", _FixedDateTime)

    calls = []

    def _fake_c1(**kw):
        calls.append(1)
        return {"id": "C1", "status": "selected"}

    original = reg.get_strategy("C1")
    patched = reg.OptionStrategyDef(**{**original.__dict__, "evaluate_fn": _fake_c1})
    idx = next(i for i, d in enumerate(reg.OPTION_STRATEGY_CATALOG) if d.id == "C1")
    reg.OPTION_STRATEGY_CATALOG[idx] = patched
    reg._BY_ID["C1"] = patched
    try:
        reg._SELECTED_TODAY.clear()
        for _ in range(4):   # simulate several scan cycles in the same window
            reg.evaluate_catalog(symbol="NIFTY", df=None, df_htf=None,
                                  option_data={}, regime={})
        assert len(calls) == 1, "one-shot strategy fired more than once in its window"
    finally:
        reg.OPTION_STRATEGY_CATALOG[idx] = original
        reg._BY_ID["C1"] = original
        reg._SELECTED_TODAY.clear()


def test_range_window_strategy_also_capped_at_one_selected_fire_per_day(monkeypatch):
    """2026-07-21 regression: C3 (range window "09:30-11:00", NOT a one-shot)
    selected 4 separate iron condors on the same symbol in one live morning
    as the underlying drifted -- each individually well-formed, but stacking
    multiple positions on the same underlying in one session isn't how this
    would be traded, and inflates the evidence table with correlated
    observations. Dedup must now apply to every strategy once it reaches
    "selected", not just one-shot ones -- while still letting a BLOCKED
    result keep retrying within the window (e.g. VIX clears mid-morning)."""
    assert reg.is_one_shot_window("09:30-11:00") is False  # still a range window, not one-shot

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 21, 10, 0)

    monkeypatch.setattr(reg, "datetime", _FixedDateTime)

    calls = []

    def _fake_c3(**kw):
        calls.append(1)
        return {"id": "C3", "status": "selected", "combo_id": f"combo-{len(calls)}"}

    original = reg.get_strategy("C3")
    patched = reg.OptionStrategyDef(**{**original.__dict__, "evaluate_fn": _fake_c3})
    idx = next(i for i, d in enumerate(reg.OPTION_STRATEGY_CATALOG) if d.id == "C3")
    reg.OPTION_STRATEGY_CATALOG[idx] = patched
    reg._BY_ID["C3"] = patched
    try:
        reg._SELECTED_TODAY.clear()
        results = []
        for _ in range(4):   # simulate re-evaluation cycles across the open window
            results.extend(r for r in reg.evaluate_catalog(
                symbol="NIFTY", df=None, df_htf=None, option_data={}, regime={})
                if r.get("id") == "C3")
        assert len(calls) == 1, "range-window strategy fired more than once/day after first selection"
        statuses = [r["status"] for r in results]
        assert statuses == ["selected", "already_fired_today",
                             "already_fired_today", "already_fired_today"]
    finally:
        reg.OPTION_STRATEGY_CATALOG[idx] = original
        reg._BY_ID["C3"] = original
        reg._SELECTED_TODAY.clear()


def test_entry_window_comma_separated_multiples():
    win = "09:15-09:45,14:30-15:15"
    assert reg.entry_window_ok(win, datetime(2026, 7, 16, 9, 20)) is True
    assert reg.entry_window_ok(win, datetime(2026, 7, 16, 14, 45)) is True
    assert reg.entry_window_ok(win, datetime(2026, 7, 16, 12, 0)) is False


def test_entry_window_exotic_forms_are_safe_not_open():
    # These forms belong only to strategies with no real logic yet this
    # pass; the parser must report "not open" rather than raising.
    assert reg.entry_window_ok("mon_10:00-11:30_or_tue_13:00-14:30") is False
    assert reg.entry_window_ok("same_as_base_strategy") is False
    assert reg.entry_window_ok("") is False


def test_evaluate_catalog_never_raises_and_covers_every_id(monkeypatch):
    # C1/C3's entry-window gate depends on wall-clock time (evaluate_catalog
    # doesn't take a `now` override), so if this test happens to run inside
    # their live windows they will actually execute against empty
    # option_data and hit their own "no_chain_data" block path, which calls
    # record_option_decision -- monkeypatched here so that can never write
    # to the real option_decision_journal.jsonl regardless of what time this
    # test runs at.
    monkeypatch.setattr("option_decision_journal.record_option_decision", lambda *a, **kw: {})
    results = reg.evaluate_catalog(
        symbol="NIFTY", df=None, df_htf=None, option_data={}, regime={"primary": "RANGE"})
    ids = {r["id"] for r in results if "id" in r}
    assert ids == set(_EXPECTED_IDS)


def test_evaluate_catalog_threads_gap_risk_manager_to_evaluate_fn():
    """2026-07-22: D1 needs a gap-risk-manager reference (for IV percentile)
    that C1/C3 never needed. evaluate_catalog() takes no explicit
    gap_risk_manager parameter -- it doesn't need one, since its existing
    **kw pass-through already forwards any extra kwarg to evaluate_fn
    verbatim. This locks in that a caller-supplied gap_risk_manager= kwarg
    actually reaches the strategy function, not just that evaluate_catalog()
    accepts it without raising."""
    captured = {}

    def _fake_d1(**kw):
        captured.update(kw)
        return {"id": "D1", "status": "selected"}

    original = reg.get_strategy("D1")
    patched = reg.OptionStrategyDef(**{**original.__dict__, "evaluate_fn": _fake_d1,
                                        "logic_status": "IMPLEMENTED", "entry_window": "00:00-23:59"})
    idx = next(i for i, d in enumerate(reg.OPTION_STRATEGY_CATALOG) if d.id == "D1")
    reg.OPTION_STRATEGY_CATALOG[idx] = patched
    reg._BY_ID["D1"] = patched
    sentinel = object()
    try:
        reg._SELECTED_TODAY.clear()
        reg.evaluate_catalog(symbol="NIFTY", df=None, df_htf=None, option_data={}, regime={},
                              gap_risk_manager=sentinel)
        assert captured.get("gap_risk_manager") is sentinel
    finally:
        reg.OPTION_STRATEGY_CATALOG[idx] = original
        reg._BY_ID["D1"] = original
        reg._SELECTED_TODAY.clear()
