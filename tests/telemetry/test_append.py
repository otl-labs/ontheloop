"""Mirror test for src/telemetry/append.py on in-memory DuckDB (core MERGE, no extension, no network).

pytest.importorskip at MODULE level: the fast tier installs no duckdb (requirements-fast.txt).
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from src.row_rules.rules import RowRuleError  # noqa: E402
from src.telemetry.append import (  # noqa: E402
    AppendResult,
    EventTableSpec,
    PreparedBatch,
    append_events,
    execute_prepared,
    prepare_batch,
)
from src.telemetry.gate import AppendError, GateError, GrainConflictError, RowRuleGateError  # noqa: E402
from src.telemetry.identity import IdentityError  # noqa: E402

TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"

_COLUMNS = {
    "event_id": "VARCHAR",
    "event_kind": "VARCHAR",
    "event_timestamp": "TIMESTAMP WITH TIME ZONE",
    "session_started_at": "TIMESTAMP WITH TIME ZONE",
    "external_ref": "VARCHAR",
    "entity_ref": "VARCHAR",
    "producer": "VARCHAR",
    "producer_version": "VARCHAR",
    "parser_version": "BIGINT",
    "created_timestamp": "TIMESTAMP WITH TIME ZONE",
    "tenant_id": "VARCHAR",
    "project_id": "VARCHAR",
    "session_id": "VARCHAR",
    "parent_session_id": "VARCHAR",
    "workflow": "VARCHAR",
}
_NOT_NULL = frozenset({"event_id", "parser_version", "session_started_at", "created_timestamp", "session_id", "producer"})


def _spec() -> EventTableSpec:
    return EventTableSpec(table="telemetry_sessions", columns=dict(_COLUMNS), not_null=_NOT_NULL)


@pytest.fixture
def con() -> Any:
    connection = duckdb.connect()
    connection.execute("SET TimeZone='UTC'")
    col_ddl = ", ".join(f"{c} {t}" for c, t in _COLUMNS.items())
    connection.execute(f"CREATE TABLE telemetry_sessions ({col_ddl})")
    return connection


def _row(**overrides: Any) -> dict[str, Any]:
    t = datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)
    base = {
        "event_kind": "open",
        "event_timestamp": t,
        "session_started_at": t,
        "external_ref": "src#0/open",
        "entity_ref": "sess-ref-1",
        "producer": "claude_code",
        "producer_version": "1.0",
        "parser_version": 1,
        "workflow": "implement",
    }
    base.update(overrides)
    return base


def test_append_events_replay_is_noop(con: Any) -> None:
    """Module-level (rec-4024's acceptance cites this exact node_id; the graduated VP step 4 keystone)."""
    row = _row()
    first = append_events(con, _spec(), [row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    assert first.inserted == 1
    second = append_events(con, _spec(), [row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    assert second.inserted == 0
    assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 1


class TestAppendEventsCore:
    def test_single_row_inserts_and_derives_ids(self, con: Any) -> None:
        result = append_events(con, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert result == AppendResult(submitted=1, collapsed=0, inserted=1)
        stored = con.execute("SELECT event_id, session_id, workflow FROM telemetry_sessions").fetchall()
        assert len(stored) == 1
        assert stored[0][2] == "implement"
        assert len(stored[0][0]) == 26
        assert len(stored[0][1]) == 26

    def test_empty_batch_returns_zero_without_transaction(self, con: Any) -> None:
        result = append_events(con, _spec(), [], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert result == AppendResult(submitted=0, collapsed=0, inserted=0)
        assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 0

    def test_higher_parser_version_inserts_a_new_row(self, con: Any) -> None:
        row = _row()
        append_events(con, _spec(), [row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        row2 = _row(parser_version=2)
        result = append_events(con, _spec(), [row2], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert result.inserted == 1
        assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 2

    def test_created_timestamp_identical_across_batch(self, con: Any) -> None:
        rows = [_row(external_ref="src#0/open"), _row(external_ref="src#1/open", entity_ref="sess-ref-2")]
        append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        timestamps = {r[0] for r in con.execute("SELECT created_timestamp FROM telemetry_sessions").fetchall()}
        assert len(timestamps) == 1

    def test_injected_now_is_used_and_truncated(self, con: Any) -> None:
        now = datetime(2026, 9, 25, 12, 0, 0, 123456, tzinfo=timezone.utc)
        append_events(con, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory", now=now)
        stored = con.execute("SELECT created_timestamp FROM telemetry_sessions").fetchone()[0]
        assert stored == now.replace(microsecond=123000)


class TestAppendEventsRejections:
    def test_non_utc_connection_rejected_before_transaction(self) -> None:
        conn = duckdb.connect()
        conn.execute("SET TimeZone='America/New_York'")
        col_ddl = ", ".join(f"{c} {t}" for c, t in _COLUMNS.items())
        conn.execute(f"CREATE TABLE telemetry_sessions ({col_ddl})")
        with pytest.raises(AppendError):
            append_events(conn, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert conn.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 0

    def test_caller_supplied_derived_column_rejected(self, con: Any) -> None:
        with pytest.raises(AppendError):
            append_events(con, _spec(), [_row(session_id="nope")], tenant_id=TENANT, project_id=PROJECT, catalog="memory")

    def test_caller_supplied_event_id_rejected(self, con: Any) -> None:
        with pytest.raises(AppendError):
            append_events(con, _spec(), [_row(event_id="nope")], tenant_id=TENANT, project_id=PROJECT, catalog="memory")

    def test_unknown_column_rejected(self, con: Any) -> None:
        with pytest.raises(GateError) as raised:
            append_events(con, _spec(), [_row(bogus="x")], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert (raised.value.reason, raised.value.column, raised.value.table) == ("unknown_column", None, "telemetry_sessions")

    def test_missing_required_ref_field_rejected(self, con: Any) -> None:
        row = _row()
        del row["entity_ref"]
        with pytest.raises(GateError) as raised:
            append_events(con, _spec(), [_row(), row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        err = raised.value
        assert (err.reason, err.column, err.table, err.row) == ("required_ref", "entity_ref", "telemetry_sessions", 1)

    def test_missing_session_started_at_rejected(self, con: Any) -> None:
        row = _row()
        del row["session_started_at"]
        with pytest.raises(GateError) as raised:
            append_events(con, _spec(), [row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert (raised.value.reason, raised.value.column, raised.value.row) == ("not_null", "session_started_at", 0)

    def test_missing_event_timestamp_rejected(self, con: Any) -> None:
        row = _row()
        del row["event_timestamp"]
        with pytest.raises(GateError) as raised:
            append_events(con, _spec(), [row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert (raised.value.reason, raised.value.column) == ("not_null", "event_timestamp")

    def test_bad_ref_and_pre_epoch_timestamp_are_gate_errors(self, con: Any) -> None:
        def rejected(row: dict[str, Any]) -> GateError:
            with pytest.raises(GateError) as raised:
                prepare_batch(_spec(), [row], tenant_id=TENANT, project_id=PROJECT)
            assert isinstance(raised.value.__cause__, IdentityError)
            return raised.value

        bad_ref = rejected(_row(entity_ref=""))
        assert (bad_ref.reason, bad_ref.column, bad_ref.table, bad_ref.row) == (
            "invalid_ref",
            "entity_ref",
            "telemetry_sessions",
            0,
        )
        assert "sess" not in str(bad_ref)
        pre_epoch = datetime(1960, 1, 1, tzinfo=timezone.utc)
        assert (rejected(_row(session_started_at=pre_epoch)).reason, rejected(_row(session_started_at=pre_epoch)).column) == (
            "time_range",
            "session_started_at",
        )
        assert rejected(_row(event_timestamp=pre_epoch)).column == "event_timestamp"
        assert rejected(_row(external_ref="")).reason == "invalid_ref"

    def test_strict_type_rejection_bool_as_bigint(self, con: Any) -> None:
        with pytest.raises(AppendError):
            append_events(con, _spec(), [_row(parser_version=True)], tenant_id=TENANT, project_id=PROJECT, catalog="memory")

    def test_conflicting_intra_batch_duplicates_rejected(self, con: Any) -> None:
        rows = [_row(workflow="implement"), _row(workflow="plan")]
        with pytest.raises(AppendError):
            append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 0

    def test_identical_intra_batch_duplicates_collapse(self, con: Any) -> None:
        rows = [_row(), _row()]
        result = append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert result.submitted == 2
        assert result.collapsed == 1
        assert result.inserted == 1

    def test_sql_failure_rolls_back_and_connection_stays_usable(self, con: Any) -> None:
        # A column the spec declares but the real table does not have: passes the pure Python
        # gate (which never consults the DB schema) but fails DuckDB's own binder inside the
        # MERGE -- the genuine "SQL failure mid-batch" path (not a gate rejection).
        bad_columns = {**_COLUMNS, "column_absent_from_table": "VARCHAR"}
        bad_spec = EventTableSpec(table="telemetry_sessions", columns=bad_columns, not_null=_NOT_NULL)
        with pytest.raises(Exception) as excinfo:
            append_events(con, bad_spec, [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert not isinstance(excinfo.value, AppendError)
        assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 0
        # connection still usable after rollback
        con.execute("SELECT 1").fetchone()


class _RecordingConnection:
    """Wraps a real DuckDB connection, recording every MERGE statement's SQL text."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.merge_statements: list[str] = []

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if sql.strip().upper().startswith("MERGE"):
            self.merge_statements.append(sql)
        return self._real.execute(sql, *args, **kwargs)


class _MergeRaises:
    """A UTC connection whose MERGE raises the given message."""

    def __init__(self, message: str) -> None:
        self._message = message

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if sql.startswith("MERGE"):
            raise RuntimeError(self._message)
        return type("R", (), {"fetchone": staticmethod(lambda: ("UTC",))})()


class TestMergeShapeAndBounds:
    def test_merge_is_single_statement_insert_only_with_day_bounds(self, con: Any) -> None:
        recorder = _RecordingConnection(con)
        append_events(recorder, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory")

        captured = recorder.merge_statements
        assert len(captured) == 1
        sql = captured[0]
        assert sql.count("MERGE INTO") == 1
        assert "WHEN NOT MATCHED THEN INSERT" in sql
        # The only MATCHED arm ends THEN ERROR -- never THEN UPDATE, never THEN DELETE (a
        # rejection writes nothing; the writer stays insert-only).
        assert sql.count("WHEN MATCHED") == 1
        assert "THEN ERROR" in sql
        assert "THEN UPDATE" not in sql
        assert "THEN DELETE" not in sql
        assert "ON t.producer = s.producer AND t.event_id = s.event_id AND t.parser_version = s.parser_version" in sql
        assert "t.session_started_at >= ?" in sql
        assert "t.session_started_at < ?" in sql

    def test_day_bounds_span_the_batch_calendar_day(self, con: Any) -> None:
        t1 = datetime(2026, 9, 25, 1, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 25, 23, 0, 0, tzinfo=timezone.utc)
        rows = [
            _row(external_ref="src#0/open", session_started_at=t1, event_timestamp=t1),
            _row(external_ref="src#1/open", entity_ref="sess-ref-2", session_started_at=t2, event_timestamp=t2),
        ]
        result = append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        assert result.inserted == 2

    def test_values_use_multi_row_source_never_string_interpolation(self, con: Any) -> None:
        recorder = _RecordingConnection(con)
        rows = [_row(external_ref="src#0/open"), _row(external_ref="src#1/open", entity_ref="sess-ref-2")]
        append_events(recorder, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="memory")
        sql = recorder.merge_statements[0]
        assert "sess-ref-1" not in sql
        assert "sess-ref-2" not in sql
        assert sql.count("CAST(?") >= len(_COLUMNS) * 2


class TestEventTableSpecFromProjection:
    def _base_entry(self) -> dict[str, Any]:
        return {
            "status": "target",
            "write_mode": "append_only",
            "history_table": "telemetry_sessions",
            "partition": {"history": "year(session_started_at), month(session_started_at), day(session_started_at)"},
            "partition_column": "session_started_at",
            "dedupe_key": ["producer", "event_id", "parser_version"],
            "entity_key": "session_id",
            "columns": {
                "event_id": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "producer": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
                "parser_version": {"role": "input", "sql_type": "BIGINT", "nullable": False},
                "session_started_at": {"role": "input", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
                "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
                "tenant_id": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "project_id": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "session_id": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "parent_session_id": {"role": "derived", "sql_type": "VARCHAR", "nullable": True},
            },
        }

    def test_accepts_a_valid_event_projection(self) -> None:
        spec = EventTableSpec.from_projection("telemetry_sessions", self._base_entry())
        assert spec.table == "telemetry_sessions"
        assert "session_id" in spec.not_null

    def test_rejects_merge_key_present(self) -> None:
        entry = self._base_entry()
        entry["merge_key"] = "session_id"
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_current_table_present(self) -> None:
        entry = self._base_entry()
        entry["current_table"] = "telemetry_sessions_current"
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_non_append_only_write_mode(self) -> None:
        entry = self._base_entry()
        entry["write_mode"] = "scd2"
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_wrong_dedupe_key(self) -> None:
        entry = self._base_entry()
        entry["dedupe_key"] = ["event_id"]
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_wrong_partition_column(self) -> None:
        entry = self._base_entry()
        entry["partition_column"] = "event_timestamp"
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_non_triple_history_partition(self) -> None:
        entry = self._base_entry()
        entry["partition"] = {"history": "day(session_started_at)"}
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_missing_required_column(self) -> None:
        entry = self._base_entry()
        del entry["columns"]["created_timestamp"]
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_wrong_derived_role_set(self) -> None:
        entry = self._base_entry()
        entry["columns"]["parser_version"]["role"] = "derived"
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_rejects_unregistered_table(self) -> None:
        with pytest.raises(AppendError):
            EventTableSpec.from_projection("not_a_telemetry_table", self._base_entry())


class TestIdentifierValidation:
    def test_rejects_unsafe_table_identifier(self, con: Any) -> None:
        bad_spec = EventTableSpec(table="telemetry_sessions; DROP TABLE x", columns=dict(_COLUMNS), not_null=_NOT_NULL)
        with pytest.raises(AppendError):
            append_events(con, bad_spec, [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory")

    def test_rejects_now_without_timezone(self, con: Any) -> None:
        with pytest.raises(AppendError):
            append_events(
                con,
                _spec(),
                [_row()],
                tenant_id=TENANT,
                project_id=PROJECT,
                catalog="memory",
                now=datetime(2026, 9, 25),
            )


def test_cross_batch_grain_conflict_rejected(con: Any) -> None:
    """Keystone: a cross-batch row whose content differs under an existing (producer, event_id,
    parser_version) is rejected by the native MERGE ... THEN ERROR arm, and nothing from the
    conflicting batch is inserted (grain enforced at the write boundary).
    """
    first = _row()
    append_events(con, _spec(), [first], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    conflicting = _row(workflow="plan")
    with pytest.raises(AppendError, match="TELEMETRY_GRAIN_CONFLICT"):
        append_events(con, _spec(), [conflicting], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    rows = con.execute("SELECT count(*), any_value(workflow) FROM telemetry_sessions").fetchone()
    assert rows[0] == 1
    assert rows[1] == "implement"


def test_producer_version_only_resend_is_noop(con: Any) -> None:
    append_events(con, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    result = append_events(
        con, _spec(), [_row(producer_version="2.0")], tenant_id=TENANT, project_id=PROJECT, catalog="memory"
    )
    assert result.inserted == 0
    assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 1


def test_cross_producer_same_event_id_both_insert(con: Any) -> None:
    claude = _row(producer="claude_code")
    litellm = _row(producer="litellm")
    append_events(con, _spec(), [claude], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    result = append_events(con, _spec(), [litellm], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    assert result.inserted == 1
    assert con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0] == 2


def test_null_producer_rejected_without_spec_not_null(con: Any) -> None:
    """A directly-built EventTableSpec whose not_null omits producer must still reject a row
    that omits the producer key -- grain-key non-null enforcement is independent of spec.not_null.
    An explicit producer=None is already refused by the VARCHAR type gate (a NULL literal never
    matches the VARCHAR isinstance check), so this test omits the key entirely.
    """
    spec = EventTableSpec(table="telemetry_sessions", columns=dict(_COLUMNS), not_null=frozenset({"event_id"}))
    row = _row()
    del row["producer"]
    with pytest.raises(AppendError):
        append_events(con, spec, [row], tenant_id=TENANT, project_id=PROJECT, catalog="memory")


def test_from_projection_rejects_two_column_dedupe_key() -> None:
    entry = TestEventTableSpecFromProjection()._base_entry()
    entry["dedupe_key"] = ["event_id", "parser_version"]
    with pytest.raises(AppendError):
        EventTableSpec.from_projection("telemetry_sessions", entry)


def _count(con: Any) -> int:
    return con.execute("SELECT count(*) FROM telemetry_sessions").fetchone()[0]


def test_prepare_then_execute_equals_append_events(con: Any) -> None:
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    rows = [_row(), _row(external_ref="src#1/open", entity_ref="sess-ref-2"), _row()]
    prepared = prepare_batch(_spec(), rows, tenant_id=TENANT, project_id=PROJECT, now=now)
    assert isinstance(prepared, PreparedBatch) and (prepared.submitted, prepared.collapsed, len(prepared.rows)) == (3, 1, 2)
    con.execute("BEGIN")
    result = execute_prepared(con, prepared, catalog="memory")
    assert _count(con) == 2
    con.execute("ROLLBACK")
    assert _count(con) == 0 and result == AppendResult(submitted=3, collapsed=1, inserted=2)
    facade = append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="memory", now=now)
    assert facade == result and _count(con) == 2
    empty = prepare_batch(_spec(), [], tenant_id=TENANT, project_id=PROJECT)
    assert execute_prepared(con, empty, catalog="memory") == AppendResult(submitted=0, collapsed=0, inserted=0)
    with pytest.raises(AppendError, match="safe SQL identifier"):
        execute_prepared(con, prepared, catalog="bad catalog")


def test_grain_conflict_is_typed(con: Any) -> None:
    with pytest.raises(GrainConflictError):
        append_events(
            con, _spec(), [_row(workflow="a"), _row(workflow="b")], tenant_id=TENANT, project_id=PROJECT, catalog="memory"
        )
    prepared = prepare_batch(_spec(), [_row()], tenant_id=TENANT, project_id=PROJECT)
    execute_prepared(con, prepared, catalog="memory")
    conflicting = prepare_batch(_spec(), [_row(workflow="plan")], tenant_id=TENANT, project_id=PROJECT)
    assert conflicting.source_index == {conflicting.rows[0]["event_id"]: 0}
    with pytest.raises(GrainConflictError, match="TELEMETRY_GRAIN_CONFLICT") as stored:
        execute_prepared(con, conflicting, catalog="memory")
    assert (stored.value.table, stored.value.event_id, stored.value.row) == (
        "telemetry_sessions",
        conflicting.rows[0]["event_id"],
        0,
    )
    with pytest.raises(GrainConflictError) as unparsed:
        execute_prepared(_MergeRaises("TELEMETRY_GRAIN_CONFLICT: no id here"), conflicting, catalog="memory")
    assert (unparsed.value.event_id, unparsed.value.row) == (None, None)
    with pytest.raises(GrainConflictError) as in_batch:
        prepare_batch(_spec(), [_row(), _row(external_ref="x#1"), _row(workflow="b")], tenant_id=TENANT, project_id=PROJECT)
    assert (in_batch.value.table, in_batch.value.row) == ("telemetry_sessions", 2)
    with pytest.raises(GateError):
        append_events(con, _spec(), [_row(parser_version=True)], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    assert _count(con) == 1


def test_execute_prepared_leaves_a_failing_batch_to_the_callers_transaction(con: Any) -> None:
    bad = EventTableSpec(table="telemetry_sessions", columns={**_COLUMNS, "absent": "VARCHAR"}, not_null=_NOT_NULL)
    prepared = prepare_batch(bad, [_row()], tenant_id=TENANT, project_id=PROJECT)
    con.execute("BEGIN")
    with pytest.raises(Exception) as raised:
        execute_prepared(con, prepared, catalog="memory")
    assert not isinstance(raised.value, AppendError)
    con.execute("ROLLBACK")


def _transcript_spec() -> EventTableSpec:
    from tests.fixtures.turn_capture_ducklake import load_specs

    return load_specs()["telemetry_transcripts"]


def _exempt_spec() -> EventTableSpec:
    spec = _transcript_spec()  # a representation change needs a threshold change, which one projection cannot hold
    return replace(spec, rules=replace(spec.rules, exemptions={"inline_within_threshold": ("test", "test")}))


def _transcript(payload: str, *, spilled: bool = False, ref: str = "u1#0", **over: Any) -> dict[str, Any]:
    digest = hashlib.sha256(payload.encode()).hexdigest()
    row: dict[str, Any] = {
        "event_kind": "point",
        "event_timestamp": datetime(2026, 9, 25, 10, 0, 5, tzinfo=timezone.utc),
        "session_started_at": datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc),
        "external_ref": ref,
        "entity_ref": ref,
        "session_ref": "sess",
        "observation_ref": "turn",
        "producer": "claude_code",
        "producer_version": "t",
        "parser_version": 2,
        "purpose": "prompt",
        "origin": "human",
        "content_sha256": digest,
        "content_bytes": len(payload.encode()),
        "content_truncated": False,
    }
    row["content_uri" if spilled else "content"] = f"t/p/s/{digest}" if spilled else payload
    row.update(over)
    return row


@pytest.fixture
def transcripts_con() -> Any:
    spec = _transcript_spec()
    connection = duckdb.connect()
    connection.execute("SET TimeZone='UTC'")
    connection.execute(f"CREATE TABLE telemetry_transcripts ({', '.join(f'{c} {t}' for c, t in spec.columns.items())})")
    return connection


def test_transcript_representation_change_is_not_a_conflict(transcripts_con: Any) -> None:
    spec = _exempt_spec()
    now = datetime(2026, 9, 25, 10, 1, tzinfo=timezone.utc)
    payload = "p" * 70_000
    kwargs = {"tenant_id": TENANT, "project_id": PROJECT, "catalog": "memory", "now": now}
    first = append_events(transcripts_con, spec, [_transcript(payload)], **kwargs)
    again = append_events(transcripts_con, spec, [_transcript(payload, spilled=True)], **kwargs)
    assert (first.inserted, again.inserted) == (1, 0)
    other = _transcript("q" * 70_000, spilled=True)
    with pytest.raises(GrainConflictError, match="TELEMETRY_GRAIN_CONFLICT"):
        append_events(transcripts_con, spec, [other], **kwargs)
    row = transcripts_con.execute("SELECT count(*), count(content), count(content_uri) FROM telemetry_transcripts").fetchone()
    assert row == (1, 1, 0)
    omitted = _transcript("x", ref="u2#0", content_sha256="a" * 64, content_bytes=9_000_000)
    omitted.pop("content")
    omitted["content_omitted_reason"] = "oversize"
    assert append_events(transcripts_con, spec, [omitted], **kwargs).inserted == 1
    assert append_events(transcripts_con, spec, [omitted], **kwargs).inserted == 0


def test_rule_rejection_leaves_no_row_and_no_transaction(transcripts_con: Any) -> None:
    spec = _transcript_spec()
    bad = _transcript("hi", event_timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(GateError, match="not_before") as raised:
        append_events(transcripts_con, spec, [bad], tenant_id=TENANT, project_id=PROJECT, catalog="memory")
    err = raised.value
    assert isinstance(err, RowRuleGateError) and isinstance(err.__cause__, RowRuleError)
    assert (err.rule, err.column, err.table, err.row) == ("not_before", "event_timestamp", "telemetry_transcripts", 0)
    assert transcripts_con.execute("SELECT count(*) FROM telemetry_transcripts").fetchone()[0] == 0


class TestFromProjectionRules:
    def test_rules_and_compare_exclusions_come_from_the_projection(self) -> None:
        entry = TestEventTableSpecFromProjection()._base_entry()
        entry["columns"]["workflow"] = {
            "role": "input", "sql_type": "VARCHAR", "nullable": True, "accepted_values": ["implement"],
            "representation_of": ["parser_version"],
        }  # fmt: skip
        spec = EventTableSpec.from_projection("telemetry_sessions", entry)
        assert spec.rules.accepted_values == {"workflow": frozenset({"implement"})}
        assert spec.compare_excluded == frozenset({"created_timestamp", "producer_version", "workflow"})

    def test_malformed_rule_projection_is_an_append_error(self) -> None:
        entry = TestEventTableSpecFromProjection()._base_entry()
        entry["columns"]["workflow"] = {"role": "input", "sql_type": "VARCHAR", "nullable": True, "mystery": 1}
        with pytest.raises(AppendError, match="telemetry_sessions: workflow: unknown rule key"):
            EventTableSpec.from_projection("telemetry_sessions", entry)

    def test_a_grain_key_column_cannot_be_representation_only(self) -> None:
        entry = TestEventTableSpecFromProjection()._base_entry()
        entry["columns"]["producer"]["representation_of"] = ["parser_version"]
        with pytest.raises(AppendError, match="grain-key column"):
            EventTableSpec.from_projection("telemetry_sessions", entry)
