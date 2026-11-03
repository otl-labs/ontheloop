"""Mirror tests for scripts/telemetry_verify/compare.py: the expected-versus-stored row classification (duckdb-free)."""

from __future__ import annotations

import copy
import functools
from datetime import timedelta
from typing import Any

import pytest

from scripts.telemetry_verify import compare
from src.telemetry.append import prepare_batch
from src.telemetry.identity import KEY_PLANS, derive_entity_key, derive_event_id
from src.turn_capture.record_turn import PARSER_VERSION, PRODUCER, CaptureResult, record_turn
from tests.fixtures.turn_capture_corpus import PROJECT, PROJECT_REF, TENANT, case_cap, load_case, mem_tree
from tests.fixtures.turn_capture_ducklake import load_specs

CASE = "subagent_sync.json"
OBSERVATIONS, SESSIONS, TRANSCRIPTS, AGENTS = (
    "telemetry_observations",
    "telemetry_sessions",
    "telemetry_transcripts",
    "telemetry_agents",
)


@functools.lru_cache(maxsize=1)
def _specs() -> dict[str, Any]:
    return load_specs()


@functools.lru_cache(maxsize=1)
def _captured() -> CaptureResult:
    with case_cap(CASE):
        return record_turn(mem_tree(load_case(CASE)[0]), None, project_ref=PROJECT_REF, session_final=True)


def _expected() -> list[tuple[str, list[dict[str, Any]]]]:
    return [(table, copy.deepcopy(rows)) for table, rows in _captured().batches]


def _stored() -> dict[str, list[dict[str, Any]]]:
    return {
        table: prepare_batch(_specs()[table], copy.deepcopy(rows), tenant_id=TENANT, project_id=PROJECT).rows
        for table, rows in _captured().batches
        if rows
    }


def _compare(stored: dict[str, list[dict[str, Any]]]) -> compare.CompareResult:
    return compare.compare_tree(_expected(), stored, producer=PRODUCER, parser_version=PARSER_VERSION)


def _first(stored: dict[str, list[dict[str, Any]]], table: str, **where: Any) -> dict[str, Any]:
    return next(row for row in stored[table] if all(row.get(k) == v for k, v in where.items()))


def test_equal_trees_compare_equal() -> None:
    stored = _stored()
    result = _compare(stored)
    sent = sum(len(rows) for rows in stored.values())
    assert sent > 0 and result.total("rows_equal") == sent
    zero = dict.fromkeys((*compare.ALARM_CLASSES, "race_duplicate_rows", "other_generation_rows", "other_producer_rows"), 0)
    assert {k: v for k, v in result.classes().items() if k != "rows_equal"} == zero
    assert result.defects == set() and len(result.survivors) == sent
    assert set(result.tables) <= {SESSIONS, OBSERVATIONS, TRANSCRIPTS, AGENTS}


def test_missing_unexpected_and_column_mismatches_are_classified() -> None:
    stored = _stored()
    dropped = stored[OBSERVATIONS].pop(0)
    altered = _first(stored, SESSIONS)
    altered["event_timestamp"] = altered["event_timestamp"] + timedelta(milliseconds=1)
    omitted = _first(stored, OBSERVATIONS, observation_type="turn")
    omitted["rec_id"] = "rec-1"
    transcript = stored[TRANSCRIPTS][0]
    transcript["content_bytes"] += 1
    extra = copy.deepcopy(stored[AGENTS][0])
    extra["external_ref"] = "not-produced"
    stored[AGENTS].append(extra)
    result = _compare(stored)
    counts = result.classes()
    assert counts["missing_in_store"] == 1 and (OBSERVATIONS, dropped["external_ref"]) in result.defects
    assert counts["unexpected_in_store"] == 1 and counts["column_mismatch"] == 3
    assert result.columns["event_timestamp"] == 1 and result.columns["rec_id"] == 1 and result.columns["content_bytes"] == 1
    assert counts["key_mismatch"] == 0 and counts["duplicate_ref_rows"] == 0
    stored = _stored()
    first = _first(stored, SESSIONS)
    for column, value in (("branch", "other-branch"), ("event_kind", "annotate")):
        first[column] = value
    assert _compare(stored).classes()["column_mismatch"] == 2


def test_every_derived_key_is_checked_against_identity() -> None:
    checked: set[tuple[str, str]] = set()
    for table, plans in KEY_PLANS.items():
        for column in ("event_id", *plans):
            stored = _stored()
            if not stored.get(table):
                continue
            for row in stored[table]:
                if row.get(column) is not None:
                    row[column] = row[column][:-1] + ("0" if row[column][-1] != "0" else "1")
                    break
            else:
                continue
            result = _compare(stored)
            assert result.classes()["key_mismatch"] == 1, (table, column)
            assert result.classes()["column_mismatch"] == 0, (table, column)
            checked.add((table, column))
    every = {(table, column) for table, plans in KEY_PLANS.items() for column in ("event_id", *plans)}
    nullable = {(t, c) for t, plans in KEY_PLANS.items() for c, plan in plans.items() if not plan.required}
    assert every - nullable <= checked, "every required derived column of every table was planted"
    stored = _stored()
    row = _first(stored, OBSERVATIONS, observation_type="turn")
    plan = KEY_PLANS[OBSERVATIONS]["observation_id"]
    assert row["observation_id"] == derive_entity_key(
        plan.domain_tag, TENANT, PROJECT, row["entity_ref"], row["session_started_at"]
    )
    assert row["event_id"] == derive_event_id(OBSERVATIONS, TENANT, PROJECT, row["external_ref"], row["event_timestamp"])
    row["tenant_id"] = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    assert _compare(stored).classes()["key_mismatch"] >= 1, "keys derive from the stored row's own tenant and project"


def test_other_generations_and_producers_are_counted_never_compared() -> None:
    stored = _stored()
    older = copy.deepcopy(stored[SESSIONS][0])
    older["parser_version"] = PARSER_VERSION - 1
    older["branch"] = "different"
    foreign = copy.deepcopy(stored[SESSIONS][0])
    foreign["producer"] = "litellm"
    stored[SESSIONS] += [older, foreign]
    counts = _compare(stored).classes()
    assert counts["other_generation_rows"] == 1 and counts["other_producer_rows"] == 1
    assert counts["column_mismatch"] == 0 and counts["duplicate_ref_rows"] == 0 and counts["unexpected_in_store"] == 0


def test_column_rules_are_declared_and_minimal() -> None:
    semantics = compare.field_semantics()
    for table in (SESSIONS, OBSERVATIONS, TRANSCRIPTS, AGENTS):
        every = set(semantics["ops_tables"][table]["columns"])
        value_columns = set(compare.compared_value_columns(table))
        keys = set(compare.key_columns(table))
        never = {"created_timestamp", "producer_version", "tenant_id", "project_id"}
        representation_only = every - value_columns - keys - never
        assert value_columns.isdisjoint(keys) and value_columns.isdisjoint(never) and keys.isdisjoint(never)
        assert representation_only <= ({"content", "content_uri"} if table == TRANSCRIPTS else set()), table
        roles = {name: semantics["ops_tables"][table]["columns"][name]["role"] for name in value_columns}
        assert set(roles.values()) == {"input"}
        assert {"external_ref", "producer", "parser_version", "session_started_at"} <= value_columns
        assert keys == {"event_id", *KEY_PLANS[table]}
    assert {"content_sha256", "content_bytes"} <= set(compare.compared_value_columns(TRANSCRIPTS))
    stored = _stored()
    stored[SESSIONS][0]["created_timestamp"] = stored[SESSIONS][0]["created_timestamp"] + timedelta(days=1)
    stored[SESSIONS][0]["producer_version"] = "other"
    assert _compare(stored).classes()["column_mismatch"] == 0
    one_byte = _stored()
    row = one_byte[TRANSCRIPTS][0]
    row["content_sha256"] = "0" * 64
    assert _compare(one_byte).columns["content_sha256"] == 1
    sub_ms = _stored()
    sub_ms[SESSIONS][0]["event_timestamp"] = sub_ms[SESSIONS][0]["event_timestamp"] + timedelta(microseconds=999)
    assert _compare(sub_ms).classes()["column_mismatch"] == 0, "timestamps compare at millisecond precision"


@pytest.mark.parametrize("same_event_id", [False, True])
def test_a_key_held_by_two_stored_rows_is_classified(same_event_id: bool) -> None:
    stored = _stored()
    original = stored[OBSERVATIONS][3]
    twin = copy.deepcopy(original)
    if not same_event_id:
        twin["event_id"] = original["event_id"][:-1] + ("0" if original["event_id"][-1] != "0" else "1")
    for order in (0, 1):
        rows = stored[OBSERVATIONS][:3] + stored[OBSERVATIONS][4:]
        rows += [original, twin] if order == 0 else [twin, original]
        result = _compare({**stored, OBSERVATIONS: rows})
        counts = result.classes()
        assert counts["race_duplicate_rows"] == int(same_event_id) and counts["duplicate_ref_rows"] == int(not same_event_id)
        assert counts["key_mismatch"] == 0 and counts["column_mismatch"] == 0, "the survivor is the expected derivation"
        assert result.survivors[(OBSERVATIONS, original["external_ref"])]["event_id"] == original["event_id"]


def test_unparseable_inputs_are_mismatches_never_crashes() -> None:
    assert compare._normal("metadata", "VARCHAR", "{not json") == "{not json"
    assert compare._normal("metadata", "VARCHAR", '{"a": 1}') == {"a": 1}
    assert compare._normal("cost_usd_reported", "DOUBLE", 1) == 1.0 and compare._normal("x", "BIGINT", None) is None
    stored = _stored()
    del stored[OBSERVATIONS][0]["tenant_id"]
    result = _compare(stored)
    assert result.classes()["key_mismatch"] >= 1, "a stored row without its tenant cannot be keyed"
    bad_time = _stored()
    bad_time[SESSIONS][0]["session_started_at"] = "not a time"
    assert _compare(bad_time).classes()["key_mismatch"] >= 1
