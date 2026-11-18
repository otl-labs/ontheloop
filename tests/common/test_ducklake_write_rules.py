"""MIRROR for src/common/ducklake_write_rules.py (rec-4158 plan B): the writer adapter onto the shared row-rule engine --
the value-free violation error, the widened existing-row select, the older-rows verdicts on the real projection, the
probe records that must pass the shipped rules, the portal's update shaping, and the write primitives' placement."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone

import pytest

import src.common.ducklake_writes as writes
from scripts.ducklake_smoke import core
from scripts.ducklake_smoke.lambda_ops_gates import ROW_RULES_BASE, ROW_RULES_CASES, ops_read_your_write
from scripts.ops_portal.writer_transport import _project_ops_record
from src.common import ducklake_runtime as rt
from src.common.ducklake_write_rules import (
    WRITER_REFERENCE_KINDS,
    RowRuleViolationError,
    check_json_objects,
    check_references,
    check_write,
    existing_row_select,
    json_object_columns,
    json_object_spec,
    reference_columns,
    rules_for,
)
from src.row_rules.rules import WRITER_SHAPE_KINDS, RowRules, RuleProjectionError
from tests.fixtures.ducklake_fakes import FakeCon, ops_rec_fields, ops_rec_record
from tests.fixtures.ducklake_smoke_fakes import _Resp

pytestmark = pytest.mark.unit

SPEC = rt.resolve_table_spec("ops_recommendations")
RULES = rules_for(SPEC)
OLD = datetime(2026, 4, 1, tzinfo=timezone.utc)  # before every contract date
MID = datetime(2026, 6, 1, tzinfo=timezone.utc)  # after 2026-05-01, before 2026-09-01
NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
FIFTY = "c" * 50  # a context under the 80-character floor


def _check(record: dict, created: datetime = NOW, prior: dict | None = None) -> None:
    check_write(SPEC, RULES, record, created, NOW, "01ULID", prior)


def _violation(record: dict, created: datetime = NOW, prior: dict | None = None) -> RowRuleViolationError:
    with pytest.raises(RowRuleViolationError) as raised:
        _check(record, created, prior)
    return raised.value


def _stored(record: dict, created: datetime = OLD) -> dict:
    """The existing-row fetch result for a stored record: created_timestamp, status and every dated column."""
    _, columns = existing_row_select(SPEC, RULES, True)
    return {c: created if c == "created_timestamp" else record.get(c) for c in columns}


def test_violation_is_a_schema_gate_error_without_values() -> None:
    leaked = "VALUE-MUST-NOT-LEAK"
    error = _violation(ops_rec_record(effort=leaked))
    assert isinstance(error, rt.SchemaGateError) and isinstance(error, rt.DuckLakeRuntimeError)
    assert (error.table, error.rule, error.column, error.exclude_before) == (
        "ops_recommendations",
        "accepted_values",
        "effort",
        "2026-05-01",
    )
    assert leaked not in str(error) and leaked not in repr(error.args)
    assert str(error) == "ops_recommendations.effort: row rule accepted_values violated"
    undated = _violation(ops_rec_record(status="nope"))
    assert (undated.rule, undated.column, undated.exclude_before) == ("accepted_values", "status", None)
    assert rt.RowRuleViolationError is RowRuleViolationError


def test_select_widens_only_with_dated_rules() -> None:  # and with a reference column (Decision 268)
    sql, columns = existing_row_select(SPEC, RULES, True)
    assert columns[:2] == ("created_timestamp", "status")
    dated = {"title", "source", "effort", "priority", "automatable", "risk", "file", "context", "acceptance", "tags"}
    dated |= {"provenance_json"}  # its dated json_object leg widens the select
    dated |= {"dependencies"}  # the reference column joins the widened select (Decision 268)
    assert set(columns[2:]) == dated and list(columns[2:]) == sorted(dated)
    assert sql == f"SELECT {', '.join(columns)} FROM ops_catalog.ops_recommendations_current WHERE id = ?"
    assert ";" not in sql and sql.count("SELECT") == 1  # one statement (Decision 88)
    undated = RowRules.from_projection({"id": {"nullable": False}, "status": {"nullable": False}})
    assert existing_row_select(SPEC, undated, True)[0] == (
        "SELECT created_timestamp, status, dependencies FROM ops_catalog.ops_recommendations_current WHERE id = ?"
    )
    assert existing_row_select(SPEC, undated, False)[1] == ("created_timestamp", "dependencies")
    smoke = rt.resolve_table_spec(None)
    assert rules_for(smoke) is None
    assert existing_row_select(smoke, None, False)[1] == ("created_timestamp",)
    decisions = rt.resolve_table_spec("ops_decisions")
    assert existing_row_select(decisions, rules_for(decisions), False)[1] == ("created_timestamp",)
    two_column = RowRules.from_projection(
        {"a": {"nullable": True, "not_before": "b", "exclude_before": {"not_before": "2026-09-01"}}, "b": {"nullable": True}}
    )
    assert existing_row_select(SPEC, two_column, False)[1] == ("created_timestamp", "a", "b", "dependencies")


def test_older_rows_verdicts_on_the_real_projection() -> None:
    _check(ops_rec_record())  # an insert of a clean record
    for override, rule, column in (
        ({"title": "short"}, "min_length", "title"),
        ({"context": FIFTY}, "min_length", "context"),
        ({"tags": ["Bad_Tag"]}, "array_element_format", "tags"),
        ({"dependencies": ["not-a-rec"]}, "array_element_format", "dependencies"),
        ({"file": "/abs.py"}, "pattern", "file"),
        ({"source": None}, "not_null", "source"),
        ({"effort": "XXL"}, "accepted_values", "effort"),
        ({"id": "nope-1"}, "pattern", "id"),
    ):
        error = _violation(ops_rec_record(**override), OLD)  # an insert is bound by every rule, whatever its created
        assert (error.rule, error.column) == (rule, column), override
    violating = ops_rec_record(context=FIFTY, tags=["Bad_Tag"])
    prior = _stored(violating)
    _check(violating, OLD, prior)  # older row, violating column unchanged: still updatable
    assert _violation(ops_rec_record(context="d" * 50, tags=["Bad_Tag"]), OLD, prior).column == "context"
    assert _violation(ops_rec_record(context=FIFTY, tags=["Worse_Tag"]), OLD, prior).column == "tags"
    _check(ops_rec_record(context="e" * 120, tags=["good-tag"]), OLD, prior)  # fixing the column passes
    assert _violation(violating, MID, _stored(violating, MID)).column == "context"  # created after 2026-05-01: bound


def _capture_ryw_records(monkeypatch) -> list[dict]:
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    sent: list[dict] = []
    state = {"status": "open"}

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        if action in ("write_ops", "update_ops"):
            sent.append(payload["record"])
            if action == "update_ops" and payload["record"]["id"].startswith("test-absent"):
                return _Resp(409, {"error_type": "referential"})
            state["status"] = payload["record"]["status"]
            return _Resp(200, {"ok": True})
        return _Resp(200, {"row_count": 1, "rows": [{"status": state["status"]}]})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    ops_read_your_write()
    return sent


def _capture_selftest_records(monkeypatch) -> list[dict]:
    import scripts.ops_portal.maintenance_ops as maintenance_ops  # noqa: PLC0415
    import src.common.ducklake_reader_client as reader_client  # noqa: PLC0415

    sent: list[dict] = []

    class _Reader:
        def current_state(self, table, row_filter=None):
            return [{"id": sent[0]["id"]}]

    monkeypatch.setattr(
        maintenance_ops, "_ducklake_write", lambda table, rec, **kw: sent.append(_project_ops_record(table, rec))
    )
    monkeypatch.setattr(reader_client, "make_reader", lambda profile=None: _Reader())
    assert maintenance_ops.selftest_roundtrip()["superseded"] is True
    return sent


def test_smoke_probe_records_pass(monkeypatch) -> None:
    records = _capture_ryw_records(monkeypatch) + _capture_selftest_records(monkeypatch)
    assert len(records) >= 6
    for record in records:
        _check(record)  # bound by every rule, as a fresh insert would be
    assert {r["id"].split("-")[1] for r in records} == {"ryw", "absent", "roundtrip"}


def test_row_rule_gate_cases_trip_their_rule() -> None:
    base = {**ROW_RULES_BASE, "id": "test-rr-0123456789ab"}
    _check(base)  # the gate's own base record is rule-clean, so the first production run cannot false-red
    assert [c[0] for c in ROW_RULES_CASES] == [
        "accepted_values",
        "min_length",
        "array_element_format",
        "pattern",
        "array_element_reference",
        "json_object",
    ]
    for rule, overrides, column in ROW_RULES_CASES:
        if rule == "array_element_reference":
            record = {**base, **overrides}
            _check(record)  # a row-local pass: the lookup is the writer adapter's, not the engine's
            with pytest.raises(RowRuleViolationError) as raised:
                check_references(_NoRowsCon(), SPEC, record, None)
            assert (raised.value.rule, raised.value.column) == (rule, column)
            continue
        error = _violation({**base, **overrides})
        assert (error.rule, error.column) == (rule, column)


class _NoRowsCon:
    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return []


def test_check_references_names_rule_and_column_without_values() -> None:
    leaked = "rec-424242424"
    with pytest.raises(RowRuleViolationError) as raised:
        check_references(_NoRowsCon(), SPEC, ops_rec_record(dependencies=[leaked]), None)
    error = raised.value
    assert (error.table, error.rule, error.column) == ("ops_recommendations", "array_element_reference", "dependencies")
    assert leaked not in str(error) and leaked not in repr(error.args)
    check_references(_NoRowsCon(), rt.resolve_table_spec(None), {"dependencies": [leaked]}, None)  # no reference column


def test_portal_normalized_update_of_an_older_row_passes() -> None:
    from scripts.ops_portal.cache import _sanitize_record  # noqa: PLC0415
    from scripts.sync.ops import _coerce_ops_rec_row  # noqa: PLC0415

    stored = {
        **ops_rec_record(id="rec-2000", context=FIFTY, tags=["Bad_Tag"], resolution=""),
        "dependencies": None,
        "execution_steps": None,
        "created_timestamp": OLD,
        "last_updated_timestamp": OLD,
        "ulid": "01OLD",
    }
    prior = _stored(stored)
    existing = _sanitize_record(_coerce_ops_rec_row(dict(stored)))
    assert existing["dependencies"] == [] and existing["resolution"] is None  # NULL list -> [], "" -> NULL
    merged = {**existing, "status": "closed", "resolution": "done"}  # update_rec's merge
    record = _project_ops_record("ops_recommendations", merged)
    _check(record, OLD, prior)
    assert _violation({**record, "context": "z" * 50}, OLD, prior).column == "context"
    assert _violation({**record, "tags": ["Other_Bad"]}, OLD, prior).column == "tags"


class _NoCatalogCon:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        return self


def test_row_rule_rejection_is_terminal_with_zero_retries(monkeypatch) -> None:
    def no_sleep(_seconds):
        raise AssertionError("a rule rejection must never back off and retry")

    def collision_looking(*args, **kwargs):
        raise RowRuleViolationError("t", "pattern", "conflict")  # its message matches an OCC collision marker

    assert rt.is_occ_collision(RowRuleViolationError("t", "pattern", "conflict"))
    monkeypatch.setattr(writes, "check_write", collision_looking)
    con = FakeCon(created_lookup=None)
    with pytest.raises(RowRuleViolationError):
        rt.write_scd2(con, ops_rec_record(), table="ops_recommendations", sleep=no_sleep)
    assert [s for s, _ in con.executed].count("BEGIN TRANSACTION") == 1
    assert ("ROLLBACK", None) in con.executed
    assert not any(s.startswith("MERGE INTO") for s, _ in con.executed)
    monkeypatch.undo()
    con = FakeCon(created_lookup=None)
    with pytest.raises(RowRuleViolationError) as raised:
        rt.write_scd2(con, ops_rec_record(title="short"), table="ops_recommendations", sleep=no_sleep)
    assert (raised.value.rule, raised.value.column) == ("min_length", "title")
    assert [s for s, _ in con.executed].count("BEGIN TRANSACTION") == 1
    assert not any(s.startswith("MERGE INTO") or "ops_entity_counters" in s for s, _ in con.executed)


def test_file_scd2_row_rule_rejects_before_catalog() -> None:
    con = _NoCatalogCon()
    with pytest.raises(RowRuleViolationError) as raised:
        rt.file_scd2(con, ops_rec_fields(title="short"), table="ops_recommendations")
    assert (raised.value.rule, raised.value.column) == ("min_length", "title")
    assert con.executed == []  # no BEGIN, no counter UPDATE: a reject touches no counter
    with pytest.raises(RowRuleViolationError) as raised:
        rt.file_scd2(con, ops_rec_fields(dependencies=["bad"]), table="ops_recommendations")
    assert raised.value.column == "dependencies" and con.executed == []
    with pytest.raises(rt.SchemaGateError) as gate:
        rt.file_scd2(con, ops_rec_fields(effort=3), table="ops_recommendations")
    assert not isinstance(gate.value, RowRuleViolationError)  # the type check still fires first


def test_write_scd2_update_binds_dated_rule_only_when_column_changes() -> None:
    older = ops_rec_record(context=FIFTY, tags=["Bad_Tag"])
    prior = {**_stored(older), "status": "open"}

    def run(record: dict) -> FakeCon:
        con = FakeCon(created_lookup=[prior])
        rt.write_scd2(con, record, table="ops_recommendations", require_exists=True, sleep=lambda s: None)
        return con

    con = run(ops_rec_record(status="closed", context=FIFTY, tags=["Bad_Tag"]))
    assert len([s for s, _ in con.executed if s.startswith("MERGE INTO")]) == 2  # history + current
    selected = next(s for s, _ in con.executed if s.startswith("SELECT created_timestamp"))
    assert re.fullmatch(
        r"SELECT created_timestamp, status, .*context.* FROM .*ops_recommendations_current WHERE id = \?", selected
    )
    for changed in (ops_rec_record(context="d" * 50, tags=["Bad_Tag"]), ops_rec_record(context=FIFTY, tags=["Worse_Tag"])):
        con = FakeCon(created_lookup=[prior])
        with pytest.raises(RowRuleViolationError):
            rt.write_scd2(con, changed, table="ops_recommendations", require_exists=True, sleep=lambda s: None)
        assert not any(s.startswith("MERGE INTO") for s, _ in con.executed)
        assert [s for s, _ in con.executed].count("BEGIN TRANSACTION") == 1  # zero OCC retries
        assert ("ROLLBACK", None) in con.executed


def test_a_table_without_rules_writes_unchecked_and_selects_no_extra_columns() -> None:
    check_write(SPEC, None, {"bogus": "anything"}, NOW, NOW, "01ULID", None)  # the smoke pair carries no rules
    from src.common.ducklake_write_rules import _dated_columns  # noqa: PLC0415

    assert _dated_columns(None) == []
    two_column = RowRules.from_projection(
        {
            "a": {"nullable": True, "at_most": "b", "exclude_before": {"at_most": "2026-09-01"}},
            "b": {"nullable": True},
        }
    )
    assert _dated_columns(two_column) == ["a", "b"]


_WHY = {"class": "cross_row", "reason": "r", "owner": "rec-1"}


def test_reference_columns_built_from_projected_leg() -> None:
    fields = {"id": {"nullable": False}, "deps": {"nullable": True, "array_element_reference": True}}
    assert reference_columns(fields) == frozenset({"deps"})
    assert reference_columns({"id": {"nullable": False}}) == frozenset()
    assert WRITER_REFERENCE_KINDS == frozenset({"array_element_reference"})
    assert reference_columns(SPEC.fields) == frozenset({"dependencies"})
    RowRules.from_projection(fields)  # the engine accepts the key and never evaluates it


@pytest.mark.parametrize("value", [False, None, {}, "yes", 1])
def test_reference_leg_value_must_be_true(value: object) -> None:
    with pytest.raises(RuleProjectionError, match="must be true"):
        reference_columns({"deps": {"nullable": True, "array_element_reference": value}})


def test_reference_leg_with_its_exemption_is_refused() -> None:
    both = {
        "deps": {"nullable": True, "array_element_reference": True, "write_time_exemptions": {"array_element_reference": _WHY}}
    }
    with pytest.raises(RuleProjectionError, match="exempt and must not also ship"):
        reference_columns(both)
    exempt_only = {"deps": {"nullable": True, "write_time_exemptions": {"array_element_reference": _WHY}}}
    assert reference_columns(exempt_only) == frozenset()


_V1 = {
    "required": {"schema_version": "integer", "writer": "string"},
    "optional": {"n": "number?", "ok": "boolean", "m": {"map": {"key_pattern": "^[a-z]+$", "values": ["pass", "fail"]}}},
}
_LEG = {"max_bytes": 120, "versions": {1: _V1}}
_SENTINEL = "SECRET-SENTINEL"


def _json_spec(leg: dict | None = None, extra: dict | None = None):
    column = {"role": "input", "sql_type": "VARCHAR", "nullable": True, "json_object": leg or _LEG, **(extra or {})}
    return dataclasses.replace(SPEC, fields={**SPEC.fields, "context_v2_json": column})


def _jcheck(value, *, created=NOW, prior=None, extra=None) -> None:
    spec = _json_spec(extra=extra)
    check_json_objects(spec, RowRules.from_projection(spec.fields), {"context_v2_json": value}, created, prior)


def _doc(**keys) -> str:
    return json.dumps({"schema_version": 1, "writer": "w", **keys})


def test_engine_records_json_object_and_per_write_keys() -> None:
    columns = {
        "j": {"nullable": True, "json_object": _LEG, "per_write": True, "exclude_before": {"json_object": "2026-12-01"}}
    }
    rules = RowRules.from_projection(columns)
    assert rules.exclude_before["j"]["json_object"] == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert WRITER_SHAPE_KINDS == frozenset({"json_object"})


def test_json_object_spec_built_from_projected_leg() -> None:
    built = json_object_spec("c", _LEG)
    assert built.max_bytes == 120 and set(built.versions) == {1}
    assert set(json_object_columns(_json_spec().fields)) == {"context_v2_json", "provenance_json"}
    assert json_object_columns(_json_spec().fields)["context_v2_json"] == json_object_spec("context_v2_json", _LEG)
    assert set(json_object_columns(SPEC.fields)) == {"provenance_json"}


@pytest.mark.parametrize(
    "leg",
    [
        {"max_bytes": 0, "versions": {}},
        {"max_bytes": True, "versions": {}},
        {"max_bytes": 65537, "versions": {}},
        {"max_bytes": 8},
        {"max_bytes": 8, "versions": {0: {"required": {"schema_version": "integer"}}}},
        {"max_bytes": 8, "versions": {1: {"required": {"writer": "string"}}}},
        {
            "max_bytes": 8,
            "versions": {1: {"required": {"schema_version": "integer", "a": "string"}, "optional": {"a": "string"}}},
        },
        {"max_bytes": 8, "versions": {1: {"required": {"schema_version": "integer", "Bad": "string"}}}},
        {"max_bytes": 8, "versions": {1: {"required": {"schema_version": "integer", "a": "date"}}}},
        {
            "max_bytes": 8,
            "versions": {1: {"required": {"schema_version": "integer", "a": {"map": {"key_pattern": "a", "values": ["x"]}}}}},
        },
        {
            "max_bytes": 8,
            "versions": {1: {"required": {"schema_version": "integer", "a": {"map": {"key_pattern": "^a$", "values": []}}}}},
        },
        {"max_bytes": 8, "versions": {}, "extra": 1},
        {"max_bytes": 8, "versions": {1: "not a mapping"}},
        {"max_bytes": 8, "versions": {1: {"required": {"schema_version": "integer"}, "bogus": {}}}},
        {"max_bytes": 8, "versions": {1: {"required": {"schema_version": "integer", "a": {"map": {"key_pattern": "^a$"}}}}}},
        {
            "max_bytes": 8,
            "versions": {
                1: {"required": {"schema_version": "integer", "a": {"map": {"key_pattern": "^[$", "values": ["x"]}}}}
            },
        },
    ],
)
def test_json_object_spec_refuses_malformed_params(leg: dict) -> None:
    with pytest.raises(RuleProjectionError):
        json_object_spec("c", leg)


def test_check_json_objects_without_rules_is_a_no_op() -> None:
    check_json_objects(SPEC, None, {"context_v2_json": "nope"}, NOW, None)


def test_json_object_leg_with_its_exemption_is_refused() -> None:
    with pytest.raises(RuleProjectionError, match="exempt and must not also ship"):
        json_object_columns(_json_spec(extra={"write_time_exemptions": {"json_object": _WHY}}).fields)


def test_json_object_leg_on_a_non_varchar_column_is_refused() -> None:
    with pytest.raises(RuleProjectionError, match="only legal on a VARCHAR column"):
        json_object_columns({"j": {"sql_type": "BIGINT", "nullable": True, "json_object": _LEG}})


def test_json_object_accepts_declared_shapes_and_null() -> None:
    _jcheck(None)
    _jcheck(_doc())
    _jcheck(_doc(n=None, ok=True, m={"a": "pass"}))
    _jcheck(_doc(n=1.5))
    _jcheck(_doc(n=2))


def test_json_object_map_type_uses_declared_key_pattern_and_values() -> None:
    for bad in ({"A": "pass"}, {"a": "maybe"}, {"a": 1}, "x", None):
        with pytest.raises(RowRuleViolationError):
            _jcheck(_doc(m=bad))


@pytest.mark.parametrize(
    "value",
    [
        7,
        "not json",
        "[]",
        "null",
        '"s"',
        '{"schema_version": 1, "writer": "w", "writer": "x"}',
        '{"schema_version": 1, "writer": "w", "n": NaN}',
        '{"schema_version": 1, "writer": "w", "n": Infinity}',
        '{"writer": "w"}',
        '{"schema_version": "1", "writer": "w"}',
        '{"schema_version": true, "writer": "w"}',
        '{"schema_version": 2, "writer": "w"}',
        '{"schema_version": 1}',
        '{"schema_version": 1, "writer": "w", "extra": 1}',
        '{"schema_version": 1, "writer": 3}',
        '{"schema_version": 1, "writer": "w", "n": true}',
        '{"schema_version": 1, "writer": "w", "ok": 1}',
        '{"schema_version": 1, "writer": "w", "n": "x"}',
        _doc(note="y" * 200),
    ],
)
def test_json_object_refusals_name_rule_and_column_without_values(value) -> None:
    with pytest.raises(RowRuleViolationError) as raised:
        _jcheck(
            value if not isinstance(value, str) else value.replace('"w"', f'"{_SENTINEL}"') if "writer" in value else value
        )
    error = raised.value
    assert (error.rule, error.column, error.table) == ("json_object", "context_v2_json", "ops_recommendations")
    assert _SENTINEL not in str(error) and _SENTINEL not in repr(error.args)


def test_json_object_binding_follows_exclude_before() -> None:
    dated = {"exclude_before": {"json_object": "2026-12-01"}}
    old, new = datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 12, 2, tzinfo=timezone.utc)
    prior = {"context_v2_json": "stored-and-malformed"}
    _jcheck("bad", created=old, prior=prior | {"context_v2_json": "bad"}, extra=dated)  # unchanged older row
    with pytest.raises(RowRuleViolationError) as raised:
        _jcheck("bad2", created=old, prior=prior, extra=dated)  # an older row whose write changes the column
    assert raised.value.exclude_before == "2026-12-01"
    with pytest.raises(RowRuleViolationError):
        _jcheck("bad", created=new, prior=prior | {"context_v2_json": "bad"}, extra=dated)  # created on or after
    with pytest.raises(RowRuleViolationError):
        _jcheck("bad", created=old, prior=None, extra=dated)  # an insert always binds
    with pytest.raises(RowRuleViolationError):
        _jcheck("bad", created=old, prior={"context_v2_json": "bad"})  # undated always binds


def test_check_write_evaluates_json_object_after_row_rules() -> None:
    spec = _json_spec()
    rules = RowRules.from_projection(spec.fields)
    ok = ops_rec_record(context_v2_json=_doc())
    check_write(spec, rules, ok, NOW, NOW, "01ULID", None)
    with pytest.raises(RowRuleViolationError) as raised:
        check_write(spec, rules, ops_rec_record(context_v2_json="nope"), NOW, NOW, "01ULID", None)
    assert raised.value.rule == "json_object"
    with pytest.raises(RowRuleViolationError) as first:  # a row-rule failure wins, as it runs first
        check_write(spec, rules, ops_rec_record(effort="nope", context_v2_json="nope"), NOW, NOW, "01ULID", None)
    assert first.value.rule == "accepted_values"
