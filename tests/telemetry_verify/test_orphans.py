"""Mirror tests for scripts/telemetry_verify/orphans.py: counts over plain rows (the verb itself is proven against it in
tests/common/test_ducklake_event_verification_verbs.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts.telemetry_verify import orphan_counts
from scripts.telemetry_verify.joins import load_telemetry_joins

JOINS = load_telemetry_joins()
PIN = datetime(2026, 1, 1, tzinfo=timezone.utc)
IDLE = 86400
SELF_TREE = "parent_observation_id_join_observations_self_tree"


def _row(table: str, n: int, project: str = "P1", **over: Any) -> dict[str, Any]:
    return {
        "tenant_id": "T",
        "project_id": project,
        "session_started_at": PIN,
        "session_id": "S1",
        "producer": "claude_code",
        "event_id": f"{table}-{project}-{n}",
        "parser_version": 3,
        "created_timestamp": PIN,
        "event_timestamp": PIN + timedelta(seconds=n),
        "event_kind": "open",
        **over,
    }


def _tree(project: str = "P1", orphan: bool = False) -> dict[str, list[dict[str, Any]]]:
    obs = [_row("o", 1, project, observation_id="O1", parent_observation_id=None, observation_type="step")]
    if orphan:
        obs.append(_row("o", 2, project, observation_id="O2", parent_observation_id="MISSING", observation_type="step"))
    else:
        obs.append(_row("o", 2, project, observation_id="O2", parent_observation_id="O1", observation_type="step"))
    return {"telemetry_sessions": [_row("s", 0, project, parent_session_id=None)], "telemetry_observations": obs}


def _self_tree(rows: list[dict[str, Any]]) -> dict[str, Any]:
    (found,) = [r for r in rows if r["join_id"] == SELF_TREE]
    return found


def test_orphans_split_by_quiescence() -> None:
    rows = _tree(orphan=True)
    last = PIN + timedelta(seconds=2)
    pending = _self_tree(orphan_counts(rows, JOINS, last + timedelta(seconds=IDLE), IDLE))
    assert (pending["child_rows"], pending["orphans_pending"], pending["orphans_quiescent"]) == (1, 1, 0)
    assert (pending["trees_pending"], pending["trees_quiescent"]) == (1, 0)
    quiet = _self_tree(orphan_counts(rows, JOINS, last + timedelta(seconds=IDLE + 1), IDLE))
    assert (quiet["orphans_pending"], quiet["orphans_quiescent"], quiet["trees_quiescent"]) == (0, 1, 1)


def test_co_pinned_trees_in_two_projects_stay_apart() -> None:
    clean, broken = _tree("P1"), _tree("P2", orphan=True)
    merged = {t: clean[t] + broken[t] for t in clean}
    as_of = PIN + timedelta(days=3)
    got = _self_tree(orphan_counts(merged, JOINS, as_of, IDLE))
    assert (got["child_rows"], got["orphans_quiescent"], got["trees_quiescent"]) == (2, 1, 2)
    assert _self_tree(orphan_counts(clean, JOINS, as_of, IDLE))["orphans_quiescent"] == 0
    only_real = _self_tree(orphan_counts(merged, JOINS, as_of, IDLE, {"P1": "real", "P2": "synthetic"}))
    assert (only_real["child_rows"], only_real["orphans_quiescent"], only_real["trees_quiescent"]) == (1, 0, 1)


def test_conflicted_keys_count_per_day_and_missing_rows_yield_no_output() -> None:
    rows = _tree()
    rows["telemetry_observations"].append(dict(rows["telemetry_observations"][0], name="different"))
    got = orphan_counts(rows, JOINS, PIN + timedelta(days=3), IDLE)
    assert {r["conflicted_keys"] for r in got} == {1}
    assert orphan_counts({}, JOINS, PIN, IDLE) == []


def _all_trees() -> list[str]:
    from tests.fixtures import telemetry_golden_chain as chain  # noqa: PLC0415
    from tests.fixtures.turn_capture_corpus import case_names  # noqa: PLC0415

    return [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
        f"verification:{n}" for n in chain.verification.TREES
    ]


@pytest.mark.parametrize("tree", _all_trees())
def test_finalized_golden_trees_have_no_orphans(tree: str) -> None:
    pytest.importorskip("duckdb")
    from tests.fixtures import telemetry_golden_chain as chain  # noqa: PLC0415

    with chain.loaded(tree, True) as (con, rows, ids):
        if not ids:
            return
        last = max(chain.last_event(rows, s) for s in ids.values())
        got = orphan_counts(rows, JOINS, last + timedelta(days=2), IDLE)
    assert got and all(r["orphans_quiescent"] == r["orphans_pending"] == 0 for r in got)
