"""The monitor's pure comparisons over plain rows (PLAN-telemetry-verification-monitor, VP step 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scripts.telemetry_verify import monitor_compare as mc
from src.common.ducklake_reader_client import VERB_FIELDS

STAMPS = {
    "generations": [{"producer": "claude_code", "parser_version": 4}],
    "conflicted_grain_keys": [],
    "derivation_conflicted": False,
}


def _window(**kw: Any) -> dict[str, Any]:
    row = dict.fromkeys(VERB_FIELDS["sessions_window"])
    return {**row, **STAMPS, "session_id": "r1", **kw}


def test_compare_session_reports_each_column_and_stamps_only_when_conflicted() -> None:
    verb = "sessions_window"
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deployed = _window(last_event_timestamp="2026-01-01T00:00:00+00:00", duration_seconds=5)
    derived = _window(last_event_timestamp=when, duration_seconds=5.0)
    assert mc.compare_session(verb, [deployed], [derived]) == []
    assert mc.compare_session(verb, [{**deployed, "state": "x"}], [derived]) == ["state"]
    assert mc.compare_session(verb, [deployed], [{**derived, "duration_seconds": 6, "state": "y"}]) == [
        "duration_seconds",
        "state",
    ]
    assert mc.compare_session(verb, [deployed], []) == ["<rows>"]
    conflicted = {**STAMPS, "derivation_conflicted": True}
    assert mc.compare_session(verb, [{**deployed, **conflicted, "state": "x"}], [{**derived, **conflicted}]) == []
    other = {**conflicted, "generations": []}
    assert mc.compare_session(verb, [{**deployed, **conflicted}], [{**derived, **other}]) == ["generations"]
    assert mc.compare_session(verb, [{**deployed, "generations": [{"parser_version": 3}]}], [derived]) == ["generations"]
    assert mc.compare_session(verb, [{**deployed, "derivation_conflicted": True}], [derived]) != []
    assert mc.compare_session(verb, [{**deployed, "state": True}], [{**derived, "state": 1}]) == ["state"]
    assert (
        mc.compare_session(verb, [{**deployed, "state": "2026-13-45T00:00:00"}], [{**derived, "state": "2026-13-45T00:00:00"}])
        == []
    )


def test_compare_window_counts_only_the_read_roots() -> None:
    deployed = [_window(session_id=f"r{i}", state="a") for i in range(4)]
    derived = [_window(session_id=f"r{i}", state="a") for i in range(4)]
    derived[1] = _window(session_id="r1", state="b")
    assert mc.compare_window(deployed, derived, ["r0", "r1"]) == (1, 2)
    assert mc.compare_window(deployed, derived, ["r0", "r2", "r2"]) == (2, 2)
    assert mc.compare_window(deployed, derived, ["r9"]) == (0, 1)
    assert mc.compare_window(deployed, derived[:2], ["r3"]) == (0, 1)


def _orphan(day: str, join: str, **kw: Any) -> dict[str, Any]:
    base = {
        "day": day,
        "join_id": join,
        "child_rows": 10,
        "orphans_quiescent": 0,
        "orphans_pending": 0,
        "trees_quiescent": 3,
        "trees_pending": 1,
        "conflicted_keys": 2,
    }
    return {**base, **kw}


def test_compare_orphans_reports_undercount_and_rootless() -> None:
    verb = [_orphan("d1", "j1", orphans_quiescent=2, orphans_pending=1), _orphan("d1", "j2"), _orphan("d2", "j1")]
    independent = [
        _orphan("d1", "j1", orphans_quiescent=2),
        _orphan("d1", "j2", orphans_quiescent=3, child_rows=7),
        _orphan("d3", "j1", orphans_quiescent=1),
    ]
    out = mc.compare_orphans(verb, independent, {"d1": 4, "d2": 5})
    assert out["quiescent"] == {"j1": 2, "j2": 0} and out["child_rows"] == {"j1": 20, "j2": 10}
    assert out["pending"] == 1 and out["conflicted_keys"] == 4
    assert out["undercount"] == {"j2": 3, "j1": 1}
    assert out["independent_child_rows"] == {"j1": 20, "j2": 7}
    assert out["rootless"] == 0
    assert mc.compare_orphans(verb, [], {"d1": 1})["rootless"] == 3
    assert mc.compare_orphans(verb, [], {"zz": 1})["rootless"] == 0


def test_compare_window_flags_a_repeated_row() -> None:
    row = _window(session_id="r0", state="a")
    assert mc.compare_window([row, dict(row)], [row], ["r0"]) == (0, 1)
    assert mc.compare_window([row], [row, dict(row)], ["r0"]) == (0, 1)


def test_nested_instants_and_unparseable_instants_compare_by_value() -> None:
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    nested = {"generations": [{"seen": when}]}
    assert mc.compare_row(["generations"], nested, {"generations": [{"seen": "2026-01-01T00:00:00+00:00"}]}) == []
    assert mc.compare_row(["generations"], nested, {"generations": [{"seen": when}]}) == []
    assert mc.compare_row(["state"], {"state": when}, {"state": "garbage"}) == ["state"]
    assert mc.compare_row(["state"], {"state": when}, {"state": None}) == ["state"]
