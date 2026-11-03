"""MIRROR for src/common/ducklake_write_rules.py (rec-4158 plan B): the writer adapter onto the shared row-rule engine --
the value-free violation error, the widened existing-row select, the older-rows verdicts on the real projection, the
probe records that must pass the shipped rules, the portal's update shaping, and the write primitives' placement."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

import src.common.ducklake_writes as writes
from scripts.ducklake_smoke import core
from scripts.ducklake_smoke.lambda_ops_gates import ROW_RULES_BASE, ROW_RULES_CASES, ops_read_your_write
from scripts.ops_portal.writer_transport import _project_ops_record
from src.common import ducklake_runtime as rt
from src.common.ducklake_write_rules import RowRuleViolationError, check_write, existing_row_select, rules_for
from src.row_rules.rules import RowRules
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


def test_select_widens_only_with_dated_rules() -> None:
    sql, columns = existing_row_select(SPEC, RULES, True)
    assert columns[:2] == ("created_timestamp", "status")
    dated = {"title", "source", "effort", "priority", "automatable", "risk", "file", "context", "acceptance", "tags"}
    assert set(columns[2:]) == dated and list(columns[2:]) == sorted(dated)
    assert sql == f"SELECT {', '.join(columns)} FROM ops_catalog.ops_recommendations_current WHERE id = ?"
    assert ";" not in sql and sql.count("SELECT") == 1  # one statement (Decision 88)
    undated = RowRules.from_projection({"id": {"nullable": False}, "status": {"nullable": False}})
    assert existing_row_select(SPEC, undated, True)[0] == (
        "SELECT created_timestamp, status FROM ops_catalog.ops_recommendations_current WHERE id = ?"
    )
    assert existing_row_select(SPEC, undated, False)[1] == ("created_timestamp",)
    smoke = rt.resolve_table_spec(None)
    assert rules_for(smoke) is None
    assert existing_row_select(smoke, None, False)[1] == ("created_timestamp",)
    decisions = rt.resolve_table_spec("ops_decisions")
    assert existing_row_select(decisions, rules_for(decisions), False)[1] == ("created_timestamp",)
    two_column = RowRules.from_projection(
        {"a": {"nullable": True, "not_before": "b", "exclude_before": {"not_before": "2026-09-01"}}, "b": {"nullable": True}}
    )
    assert existing_row_select(SPEC, two_column, False)[1] == ("created_timestamp", "a", "b")


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
    assert [c[0] for c in ROW_RULES_CASES] == ["accepted_values", "min_length", "array_element_format", "pattern"]
    for rule, overrides, column in ROW_RULES_CASES:
        error = _violation({**base, **overrides})
        assert (error.rule, error.column) == (rule, column)


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
