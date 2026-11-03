"""Mirror tests for scripts/telemetry_verify/dedupe.py (stdlib and yaml only, fast tier)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.telemetry_verify import dedupe

_VECTORS = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "telemetry" / "fixtures" / "dedupe_vectors.yaml").read_text(encoding="utf-8")
)["vectors"]


def _call(event_id: str, producer: str, observation_id: str, stamp: str, created: str = "2026-09-25T10:00:00+00:00") -> dict:
    return {
        "producer": producer,
        "event_id": event_id,
        "parser_version": 1,
        "session_id": "s",
        "observation_id": observation_id,
        "event_timestamp": stamp,
        "created_timestamp": created,
        "name": event_id,
    }


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["name"] for v in _VECTORS])
def test_oracle_matches_every_dedupe_vector(vector: dict[str, Any]) -> None:
    rows_by_table = vector["rows"]
    generations = dedupe.compute_generations(rows_by_table.get("telemetry_sessions") or [])
    for table, expected in vector["expected"].items():
        rows = rows_by_table.get(table) or []
        survivors, conflicted = dedupe.resolve_table(
            table, rows, generations, compared=("content",), entity_collapse=any("observation_id" in r for r in rows)
        )
        assert sorted(r["event_id"] for r in survivors) == sorted(expected["surviving_event_ids"]), vector["name"]
        assert {tuple(k) for k in conflicted} == {tuple(k) for k in expected["conflicted_grain_keys"]}, vector["name"]


def test_model_call_precedence_beats_a_later_event_and_a_predicate_limits_the_collapse() -> None:
    rows = [
        _call("cc", "claude_code", "o1", "2026-09-25T10:00:00+00:00"),
        _call("ll", "litellm", "o1", "2026-09-25T10:09:00+00:00"),
        _call("zz", "zeta", "o1", "2026-09-25T10:30:00+00:00"),
        dict(_call("other", "claude_code", "o2", "2026-09-25T10:00:00+00:00"), kind="tool"),
        dict(_call("other2", "claude_code", "o2", "2026-09-25T10:01:00+00:00"), kind="tool"),
    ]
    survivors, _ = dedupe.resolve_table("t", rows, {}, compared=("name",), entity_collapse=lambda r: "kind" not in r)
    assert sorted(r["event_id"] for r in survivors) == ["cc", "other", "other2"]


def test_a_null_against_a_value_is_a_conflict_and_representation_columns_are_not() -> None:
    base = _call("e1", "claude_code", "o1", "2026-09-25T10:00:00+00:00")
    nulled = dict(base, name=None, created_timestamp="2026-09-25T10:00:05+00:00")
    assert dedupe.conflicted_grain_keys([base, nulled], ["name"]) == {("claude_code", "e1", 1)}
    assert dedupe.conflicted_grain_keys([base, nulled], ["observation_id"]) == set()


def test_the_default_compared_columns_are_the_governed_set() -> None:
    first = _call("e1", "claude_code", "o1", "2026-09-25T10:00:00+00:00")
    second = dict(first, producer_version="9", created_timestamp="2026-09-25T10:00:09+00:00")
    survivors, conflicted = dedupe.resolve_table("telemetry_observations", [first, second], {})
    assert len(survivors) == 1 and not conflicted
    differing = dict(second, name="other")
    _, conflicted = dedupe.resolve_table("telemetry_observations", [first, differing], {})
    assert conflicted == {("claude_code", "e1", 1)}


def test_timestamps_normalise_from_naive_strings_and_foreign_offsets() -> None:
    assert dedupe.parse_timestamp("2026-09-25T10:00:00") == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert dedupe.parse_timestamp("2026-09-25T12:00:00+02:00") == datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
    assert (
        dedupe.normalise_row({"event_timestamp": None, "created_timestamp": "2026-09-25T10:00:00Z"})["event_timestamp"] is None
    )
