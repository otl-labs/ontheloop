"""append_events: the telemetry kernel's write-boundary primitive (Decision 199, rec-4024 slice 1).

The write boundary the contracts name ("populated_by: the write boundary (rec-4024's append_events
verb), derived from ..."): derives every KEY_PLANS key and event_id from the caller's refs and
REJECTS any caller-supplied derived column; stamps tenant_id/project_id (already resolved,
canonical) and one created_timestamp; runs the strict gate.py row gate before any SQL; collapses
intra-batch duplicates that differ only in created_timestamp/producer_version and rejects any
other conflicting ones; then runs ONE transaction, ONE MERGE over a typed multi-row VALUES source,
bound to (producer, event_id, parser_version) AND the batch's UTC calendar-day range: a matched row
whose content differs (every stored column except created_timestamp and producer_version) errors
loudly (grain enforced at the write boundary); an identical or producer_version-only re-send is a
no-op; the writer never updates or deletes -- a rejection writes nothing from the batch.

Every row-local rule the contract declares (not-null, accepted values, required_when, exactly-one-of, ordering,
bounds, content integrity/threshold/cap, the write-time skew) is projected into the spec's RowRules and enforced
by src/row_rules/rules.py before any SQL (Decision 210). prepare_batch is the pure half (gate, rules, derivation,
intra-batch collapse); execute_prepared runs the one MERGE inside a transaction the CALLER owns, so a writer can
put several tables in one transaction; append_events is the one-table facade over both.

Out of scope (rec-4024, the writer verb): OCC retry, row caps, project_ref/tenant resolution, the writer-edge
decoding of timestamp strings (timestamps.parse_wire_timestamp), and parent_observation_id existence checking,
which is not a write-time check under the amended R3 contract.
duckdb is imported only under TYPE_CHECKING (plane-neutral rule, Decision 184 cl.2) -- this module
is reachable without duckdb installed; only calling append_events() needs a live connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from src.row_rules.rules import RowRuleError, RowRules, check_row
from src.telemetry.gate import (
    AppendError,
    GateError,
    GrainConflictError,
    RowRuleGateError,
    check_and_normalize_value,
    check_not_null,
    collapse_or_reject_duplicates,
    reject_unknown_or_derived_columns,
)
from src.telemetry.identity import KEY_PLANS, IdentityError, derive_entity_key, derive_event_id
from src.telemetry.timestamps import TimestampError

if TYPE_CHECKING:
    import duckdb

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STRUCTURAL_NOT_NULL = frozenset({"producer", "event_id", "parser_version", "session_started_at", "created_timestamp"})
_ALWAYS_DERIVED = frozenset({"event_id", "created_timestamp", "tenant_id", "project_id"})
_GRAIN_KEY = ("producer", "event_id", "parser_version")
_STRUCTURAL_EXCLUDED = frozenset({"created_timestamp", "producer_version"})
_CONFLICT_TOKEN = "TELEMETRY_GRAIN_CONFLICT"
_CONFLICT_EVENT_ID_RE = re.compile(re.escape(_CONFLICT_TOKEN) + r": conflicting row for event_id=([0-9A-Z]{26})")


@dataclass(frozen=True)
class EventTableSpec:
    """The append primitive's per-table shape: stored columns, NOT NULL set, and key plan."""

    table: str
    columns: dict[str, str] = field(default_factory=dict)  # column -> sql_type, declaration order
    not_null: frozenset[str] = field(default_factory=frozenset)
    rules: RowRules = field(default_factory=RowRules)
    compare_excluded: frozenset[str] = _STRUCTURAL_EXCLUDED

    @property
    def key_plans(self) -> dict[str, Any]:
        return KEY_PLANS[self.table]

    @property
    def derived_columns(self) -> frozenset[str]:
        return _ALWAYS_DERIVED | frozenset(self.key_plans)

    @property
    def caller_known_columns(self) -> frozenset[str]:
        stored_input = frozenset(self.columns) - self.derived_columns
        ref_fields = frozenset(kp.ref_field for kp in self.key_plans.values())
        return stored_input | ref_fields

    @classmethod
    def from_projection(cls, table: str, entry: dict[str, Any]) -> EventTableSpec:
        """Build a spec from scripts/field_semantics_event_projection.py's output shape.

        Rejects (AppendError): merge_key/current_table present, write_mode != append_only,
        dedupe_key != [producer, event_id, parser_version], partition_column != session_started_at, a
        history partition that is not the calendar-day triple over session_started_at, a missing
        event_id/parser_version/session_started_at/created_timestamp column, or a derived-role set
        that differs from {event_id, created_timestamp, tenant_id, project_id} plus this table's
        own KEY_PLANS columns.
        """
        if "merge_key" in entry or "current_table" in entry:
            raise AppendError(f"{table}: an event projection must carry no merge_key/current_table")
        if entry.get("write_mode") != "append_only":
            raise AppendError(f"{table}: write_mode must be append_only, got {entry.get('write_mode')!r}")
        if list(entry.get("dedupe_key") or []) != ["producer", "event_id", "parser_version"]:
            raise AppendError(f"{table}: dedupe_key must be [producer, event_id, parser_version]")
        if entry.get("partition_column") != "session_started_at":
            raise AppendError(f"{table}: partition_column must be session_started_at")

        expected_partition = "year(session_started_at), month(session_started_at), day(session_started_at)"
        history_partition = (entry.get("partition") or {}).get("history")
        if history_partition != expected_partition:
            raise AppendError(f"{table}: history partition must be the calendar-day triple, got {history_partition!r}")

        columns_raw = entry.get("columns") or {}
        if table not in KEY_PLANS:
            raise AppendError(f"{table}: not a registered telemetry table (no KEY_PLANS entry)")
        key_plans = KEY_PLANS[table]

        for required in ("producer", "event_id", "parser_version", "session_started_at", "created_timestamp"):
            if required not in columns_raw:
                raise AppendError(f"{table}: projection is missing required column {required!r}")

        expected_derived_roles = _ALWAYS_DERIVED | frozenset(key_plans)
        actual_derived_roles = {name for name, spec in columns_raw.items() if spec.get("role") == "derived"}
        if actual_derived_roles != expected_derived_roles:
            raise AppendError(
                f"{table}: derived-role set {sorted(actual_derived_roles)} != expected {sorted(expected_derived_roles)}"
            )

        columns = {name: spec["sql_type"] for name, spec in columns_raw.items()}
        not_null = frozenset(_STRUCTURAL_NOT_NULL) | frozenset(col for col, plan in key_plans.items() if plan.required)
        try:
            rules = RowRules.from_projection(columns_raw, entry.get("table_rules"))
        except ValueError as exc:
            raise AppendError(f"{table}: {exc}") from exc
        compare_excluded = _STRUCTURAL_EXCLUDED | rules.representation_only
        if not compare_excluded.isdisjoint(_GRAIN_KEY):
            raise AppendError(f"{table}: a grain-key column cannot be excluded from the compared content")
        return cls(table=table, columns=columns, not_null=not_null, rules=rules, compare_excluded=compare_excluded)


@dataclass(frozen=True)
class AppendResult:
    submitted: int
    collapsed: int
    inserted: int


def _validate_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise AppendError(f"{kind} {name!r} is not a safe SQL identifier")
    return name


def _require_utc_connection(con: duckdb.DuckDBPyConnection) -> None:
    row = con.execute("SELECT current_setting('TimeZone')").fetchone()
    assert row is not None
    (tz,) = row
    if tz != "UTC":
        raise AppendError(
            f"connection TimeZone is {tz!r}, not 'UTC' -- DuckLake evaluates year()/month()/day() in the "
            "session TimeZone, so a non-UTC writer files a UTC date under the wrong day. "
            "Remedy: SET TimeZone='UTC' on this connection before calling append_events."
        )


def _identity_gate_error(exc: IdentityError, *, time_column: str, ref_column: str) -> GateError:
    """A value-free GateError for an identity derivation failure: a time basis out of range, else an invalid ref."""
    if isinstance(exc.__cause__, TimestampError):
        return GateError(f"{time_column} is outside the supported time range", column=time_column, reason="time_range")
    return GateError(f"{ref_column} is not a valid reference", column=ref_column, reason="invalid_ref")


def _derive_row(
    table_spec: EventTableSpec,
    raw_row: dict[str, Any],
    *,
    tenant_id: str,
    project_id: str,
    created_timestamp: datetime,
) -> dict[str, Any]:
    reject_unknown_or_derived_columns(raw_row, table_spec.caller_known_columns, table_spec.derived_columns)

    typed: dict[str, Any] = {}
    for col, value in raw_row.items():
        if col in table_spec.columns:
            typed[col] = check_and_normalize_value(col, value, table_spec.columns[col])

    session_started_at = typed.get("session_started_at")
    if not isinstance(session_started_at, datetime):
        raise GateError(
            "session_started_at is required and must be a datetime", column="session_started_at", reason="not_null"
        )

    for column, plan in table_spec.key_plans.items():
        ref = raw_row.get(plan.ref_field)
        if ref is None:
            if plan.required:
                raise GateError(
                    f"{table_spec.table}: required ref field {plan.ref_field!r} is missing",
                    column=plan.ref_field,
                    reason="required_ref",
                )
            typed[column] = None
            continue
        try:
            typed[column] = derive_entity_key(plan.domain_tag, tenant_id, project_id, ref, session_started_at)
        except IdentityError as exc:
            raise _identity_gate_error(exc, time_column="session_started_at", ref_column=plan.ref_field) from exc

    external_ref = typed.get("external_ref")
    event_timestamp = typed.get("event_timestamp")
    if not isinstance(external_ref, str) or not isinstance(event_timestamp, datetime):
        missing = "external_ref" if not isinstance(external_ref, str) else "event_timestamp"
        raise GateError(
            f"{table_spec.table}: external_ref and event_timestamp are required to derive event_id",
            column=missing,
            reason="not_null",
        )
    try:
        typed["event_id"] = derive_event_id(table_spec.table, tenant_id, project_id, external_ref, event_timestamp)
    except IdentityError as exc:
        raise _identity_gate_error(exc, time_column="event_timestamp", ref_column="external_ref") from exc

    typed["tenant_id"] = tenant_id
    typed["project_id"] = project_id
    typed["created_timestamp"] = created_timestamp

    check_not_null(typed, table_spec.not_null)
    try:
        check_row(table_spec.table, typed, table_spec.rules, created_timestamp)
    except RowRuleError as exc:
        raise RowRuleGateError(str(exc), rule=exc.rule, table=exc.table, column=exc.column) from exc
    return typed


def _day_bounds(session_started_at_values: list[datetime]) -> tuple[datetime, datetime]:
    floors = [v.replace(hour=0, minute=0, second=0, microsecond=0) for v in session_started_at_values]
    lower = min(floors)
    upper = max(floors) + timedelta(days=1)
    return lower, upper


def _build_merge_sql(
    catalog: str, table: str, ordered_columns: list[tuple[str, str]], row_count: int, compare_excluded: frozenset[str]
) -> str:
    columns = [c for c, _ in ordered_columns]
    col_list = ", ".join(columns)
    one_row = "(" + ", ".join(f"CAST(? AS {sql_type})" for _, sql_type in ordered_columns) + ")"
    values_clause = ", ".join([one_row] * row_count)
    insert_values = ", ".join(f"s.{c}" for c in columns)
    on_clause = " AND ".join(f"t.{c} = s.{c}" for c in _GRAIN_KEY)
    compare_columns = [c for c in columns if c not in compare_excluded]
    t_tuple = ", ".join(f"t.{c}" for c in compare_columns)
    s_tuple = ", ".join(f"s.{c}" for c in compare_columns)
    return (
        f"MERGE INTO {catalog}.{table} AS t "
        f"USING (VALUES {values_clause}) AS s({col_list}) "
        f"ON {on_clause} AND t.session_started_at >= ? AND t.session_started_at < ? "
        f"WHEN MATCHED AND ({t_tuple}) IS DISTINCT FROM ({s_tuple}) "
        f"THEN ERROR ('{_CONFLICT_TOKEN}: conflicting row for event_id=' || s.event_id) "
        f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({insert_values})"
    )


def _locate(exc: GateError | GrainConflictError, table: str, index: int | None) -> None:
    """Fill the table and row a kernel error lacks; a locator the raise site already set is kept."""
    if exc.table is None:
        exc.table = table
    if exc.row is None:
        exc.row = index


@dataclass(frozen=True)
class PreparedBatch:
    """The pure half's output: derived, rule-checked, intra-batch-collapsed rows ready for execute_prepared."""

    table_spec: EventTableSpec
    rows: list[dict[str, Any]]
    submitted: int
    collapsed: int
    source_index: dict[str, int] = field(default_factory=dict)  # event_id -> index of its first row as sent


def prepare_batch(
    table_spec: EventTableSpec,
    rows: list[dict[str, Any]],
    *,
    tenant_id: str,
    project_id: str,
    now: datetime | None = None,
) -> PreparedBatch:
    """Derive keys, run the gate and every contract row rule, collapse intra-batch duplicates. No SQL.

    Raises GateError (a row rule or type rejection) or GrainConflictError (a differing row under one grain key).
    created_timestamp is minted once per call; a retry is judged against a later one, so a row that passed once
    passes again (the skew rule is monotone).
    """
    _validate_identifier(table_spec.table, "table")
    for col in table_spec.columns:
        _validate_identifier(col, "column")
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() is None:
        raise AppendError("now must be a tz-aware datetime")
    moment = moment.astimezone(timezone.utc)
    created_timestamp = moment.replace(microsecond=(moment.microsecond // 1000) * 1000)
    derived_rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(rows):
        try:
            derived_rows.append(
                _derive_row(
                    table_spec, raw_row, tenant_id=tenant_id, project_id=project_id, created_timestamp=created_timestamp
                )
            )
        except GateError as exc:
            _locate(exc, table_spec.table, index)
            raise
    try:
        deduped_rows, collapsed = collapse_or_reject_duplicates(derived_rows, _GRAIN_KEY, table_spec.compare_excluded)
    except (GateError, GrainConflictError) as exc:
        _locate(exc, table_spec.table, None)
        raise
    source_index: dict[str, int] = {}
    for index, derived in enumerate(derived_rows):
        source_index.setdefault(derived["event_id"], index)
    return PreparedBatch(
        table_spec=table_spec, rows=deduped_rows, submitted=len(rows), collapsed=collapsed, source_index=source_index
    )


def execute_prepared(con: duckdb.DuckDBPyConnection, prepared: PreparedBatch, *, catalog: str) -> AppendResult:
    """Run the one insert-only MERGE for *prepared*. Opens and commits NOTHING: the caller owns the transaction.

    A stored row that differs under the same grain key raises GrainConflictError; nothing is written for the batch.
    """
    _require_utc_connection(con)
    _validate_identifier(catalog, "catalog")
    table_spec = prepared.table_spec
    if not prepared.rows:
        return AppendResult(submitted=prepared.submitted, collapsed=prepared.collapsed, inserted=0)
    ordered_columns = list(table_spec.columns.items())
    lower, upper = _day_bounds([r["session_started_at"] for r in prepared.rows])
    sql = _build_merge_sql(catalog, table_spec.table, ordered_columns, len(prepared.rows), table_spec.compare_excluded)
    params: list[Any] = []
    for row in prepared.rows:
        params.extend(row.get(col) for col, _ in ordered_columns)
    params.extend([lower, upper])
    try:
        result_row = con.execute(sql, params).fetchone()
    except Exception as exc:
        if _CONFLICT_TOKEN in str(exc):
            found = _CONFLICT_EVENT_ID_RE.search(str(exc))
            event_id = found.group(1) if found else None
            raise GrainConflictError(
                str(exc), table=table_spec.table, event_id=event_id, row=prepared.source_index.get(event_id or "")
            ) from exc
        raise
    assert result_row is not None
    (inserted,) = result_row
    return AppendResult(submitted=prepared.submitted, collapsed=prepared.collapsed, inserted=inserted)


def append_events(
    con: duckdb.DuckDBPyConnection,
    table_spec: EventTableSpec,
    rows: list[dict[str, Any]],
    *,
    tenant_id: str,
    project_id: str,
    catalog: str,
    now: datetime | None = None,
) -> AppendResult:
    """Append *rows* to table_spec.table in one transaction, one insert-only MERGE.

    Every KEY_PLANS key and event_id is derived from each row's caller-supplied refs; a
    caller-supplied derived column is rejected. Replaying an identical batch is a no-op
    (inserted=0). Never retries; never mints anything but created_timestamp.
    """
    _require_utc_connection(con)
    _validate_identifier(catalog, "catalog")
    _validate_identifier(table_spec.table, "table")
    for col in table_spec.columns:
        _validate_identifier(col, "column")

    if not rows:
        return AppendResult(submitted=0, collapsed=0, inserted=0)

    prepared = prepare_batch(table_spec, rows, tenant_id=tenant_id, project_id=project_id, now=now)
    con.execute("BEGIN")
    try:
        result = execute_prepared(con, prepared, catalog=catalog)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return result


__all__ = ["AppendResult", "EventTableSpec", "PreparedBatch", "append_events", "execute_prepared", "prepare_batch"]
