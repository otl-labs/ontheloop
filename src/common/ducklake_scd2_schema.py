"""SCD2 schema layer -- pure, I/O-free SQL-generation and validation (T2.19 / CD.33, Decision 81).

Facade (Decision 124 / 128): the named-read registry lives in ducklake_named_reads and the write-verb registry in
ducklake_write_verbs; both are re-exported here so `src.common.ducklake_scd2_schema.<name>` is unchanged.

The seam between this module and ducklake_runtime is the pure/impure boundary:
  - ducklake_scd2_schema: WHAT the schema IS and how to render its SQL (I/O-free, DB-free).
  - ducklake_runtime: WHAT the schema DOES (connection, transaction, OCC, reads, metrics).

Dependency is strictly one-directional: runtime imports schema, never the reverse.
Pure functions here are DB-free and unit-testable by asserting on SQL strings alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.common.ducklake_named_reads import (  # noqa: F401 -- facade re-exports
    NAMED_READS,
    NAMED_READS_VERSION,
    NamedRead,
    _params_schema,
    describe_named_reads,
)
from src.common.ducklake_partition_spec import PartitionSpecError, resolve_partition_block
from src.common.ducklake_write_verbs import VERB_REGISTRY, WriteVerb, describe_write_verbs  # noqa: F401 -- facade re-exports

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATALOG_ALIAS = "ops_catalog"

# Representative SCD2 smoke-table pair (real ops_* business schema is T2.19).
SMOKE_HISTORY_TABLE = "ducklake_smoke_history"
SMOKE_CURRENT_TABLE = "ducklake_smoke_current"

_FIELD_SEMANTICS_ENV = "DUCKLAKE_FIELD_SEMANTICS_PATH"
_DEFAULT_FIELD_SEMANTICS_PATH = Path(__file__).resolve().parents[2] / "config" / "lambda" / "ducklake" / "field_semantics.yaml"

# SQL-type -> Python type for the schema gate's input-field validation. Extended for the real ops_*
# column types (T2.19): arrays (tags/dependencies/related_decisions -> list), integers
# (decision_id/execution_steps/rank -> int), booleans (automatable -> bool). DuckDB array types are
# spelled `<base>[]` (e.g. VARCHAR[], BIGINT[]); they map to a Python list at the gate.
_PY_TYPE_FOR_SQL: dict[str, type] = {
    "VARCHAR": str,
    "TIMESTAMP WITH TIME ZONE": datetime,
    "BIGINT": int,
    "INTEGER": int,
    "BOOLEAN": bool,
    "VARCHAR[]": list,
    "BIGINT[]": list,
    "INTEGER[]": list,
}


# ---------------------------------------------------------------------------
# Exceptions -- all loud-fail (Decision 55)
# ---------------------------------------------------------------------------


class DuckLakeRuntimeError(RuntimeError):
    """Base for all DuckLake runtime loud-fail conditions."""


class SchemaGateError(DuckLakeRuntimeError):
    """Raised when a write record fails the schema gate (unknown/derived/missing/mis-typed field)."""


class ReferentialError(DuckLakeRuntimeError):
    """Raised when an update targets a merge-key absent from the current projection (CD.33 cl.8 / D-5).

    The in-transaction existence check replaces the prior permissive upsert-on-absent: an update of a
    non-existent record loud-fails instead of silently creating a partial row.
    """


class AppendOnlyUpdateError(DuckLakeRuntimeError):
    """Raised when require_exists=True is passed for an append_only table (Decision 70 / write-once lifecycle).

    Append-only tables are write-once; the update path is illegal. Record state changes as new events
    with a distinct merge_key.
    """


class StatusTransitionError(DuckLakeRuntimeError):
    """Raised when an update would silently REACTIVATE a resolved rec (closed/declined/superseded -> open).

    Distinct from scripts/contracts_enforcement.check_status_transition (the contract-lifecycle check)
    to avoid a same-name collision (Decision 103: the DAG is a resolved-reactivation guard, not a
    forward-only/terminal whitelist -- every live transition, incl. failed->open executor restart and
    *->superseded / open|failed->declined postmortem purge, passes; only a resolved->open reactivation
    is rejected).
    """


# ---------------------------------------------------------------------------
# Write identity and result dataclasses (minted by runtime, schema contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteIdentity:
    """The deterministic identity minted ONCE per write op, reused on every OCC retry.

    ulid: monotonic ULID, the history logical PK + idempotency dedup key.
    timestamp: high-precision write timestamp, stable across retries (SCD2 ordering).
    """

    ulid: str
    timestamp: datetime


@dataclass(frozen=True)
class WriteResult:
    """Outcome of a write_scd2 call. occ_retries + commit_ms drive the CloudWatch metrics."""

    ulid: str
    rec_id: str
    occ_retries: int
    commit_ms: float
    created_timestamp: datetime
    last_updated_timestamp: datetime


# ---------------------------------------------------------------------------
# Field-semantics contract -- the single source the gate + derivations + tests read
# ---------------------------------------------------------------------------


def _field_semantics_path() -> Path:
    """Resolve the field-semantics YAML path (env override for Lambda-bundle relocation)."""
    override = os.environ.get(_FIELD_SEMANTICS_ENV)
    return Path(override) if override else _DEFAULT_FIELD_SEMANTICS_PATH


@lru_cache(maxsize=4)
def _load_field_semantics_cached(path_str: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))


def load_field_semantics(path: str | Path | None = None) -> dict[str, Any]:
    """Load + cache the field-semantics contract. Pass `path` to override (tests)."""
    resolved = Path(path) if path is not None else _field_semantics_path()
    return _load_field_semantics_cached(str(resolved))


# ---------------------------------------------------------------------------
# Table spec -- the single resolved shape that drives the gate, DDL, MERGE, and reads.
# table=None selects the smoke pair (T2.17 back-compat); a name selects an ops_tables entry (T2.19).
# ---------------------------------------------------------------------------

# Derived SCD2-envelope columns minted by the runtime (never caller-supplied). Physical column order
# is: ulid first, then the input columns (merge_key first), then created/last_updated last.
_DERIVED_LEAD = "ulid"
_DERIVED_TAIL = ("created_timestamp", "last_updated_timestamp")


@dataclass(frozen=True)
class ScdTableSpec:
    """Resolved SCD2 table shape. Drives create/gate/write/read uniformly for smoke + ops_* tables."""

    table: str | None  # None = smoke
    history_table: str
    current_table: str | None  # None for append_only tables (no write-through projection)
    merge_key: str
    fields: dict[str, Any]  # column name -> {role, sql_type, nullable}; the schema-gate contract
    ordered_columns: tuple[tuple[str, str], ...]  # (name, sql_type) in physical (DDL/INSERT) order
    partition_history: str
    partition_current: str | None  # None for append_only tables (no current projection to partition)
    entity_id_prefix: str | None = None  # canonical id shape <prefix>NNN (None = no canonical keyspace)
    id_keyspace: str = "caller"  # "writer" => file_ops allocates + write_ops advances the counter (Decision 84 I-2)
    write_mode: str = "scd2"  # "scd2" | "append_only"; append_only skips the current write-through projection
    id_scheme: str = "serial"  # "serial" | "ulid": how a writer-keyspace id is minted (counter semantics, control_health)


def _order_columns(fields: dict[str, Any], merge_key: str) -> tuple[tuple[str, str], ...]:
    """Return ((name, sql_type), ...) in physical order: ulid, merge_key, other inputs, writer-derived, created, updated.

    A stable, deterministic order so the DDL, the MERGE source SELECT, the INSERT VALUES list, and the
    read projection all agree without a separate ordering source. A `role: derived` column outside the
    SCD2 envelope (a writer-computed attribute such as ops_projects.purpose) is physical: it follows the
    inputs, and schema_gate still refuses it from a caller.
    """
    inputs = [c for c, s in fields.items() if s.get("role") == "input"]
    other_inputs = [c for c in inputs if c != merge_key]
    envelope = {_DERIVED_LEAD, *_DERIVED_TAIL}
    writer_derived = [c for c, s in fields.items() if s.get("role") == "derived" and c not in envelope and c != merge_key]
    ordered = [_DERIVED_LEAD, merge_key, *other_inputs, *writer_derived, *_DERIVED_TAIL]
    return tuple((c, fields[c]["sql_type"]) for c in ordered)


def is_event_table(table: str | None, semantics: dict[str, Any] | None = None) -> bool:
    """True iff the ops_tables entry for *table* carries table_class: event (append_only with a merge key is not one)."""
    if table is None:
        return False
    semantics = semantics if semantics is not None else load_field_semantics()
    entry = semantics.get("ops_tables", {}).get(table)
    return entry is not None and entry.get("table_class") == "event"


def table_write_boundary(table: str | None, semantics: dict[str, Any] | None = None) -> str | None:
    """The ops_tables entry's write_boundary (e.g. telemetry_append, registration), read without resolving the entry."""
    if table is None:
        return None
    semantics = semantics if semantics is not None else load_field_semantics()
    entry = semantics.get("ops_tables", {}).get(table)
    return None if entry is None else entry.get("write_boundary")


def resolve_table_spec(table: str | None = None, semantics: dict[str, Any] | None = None) -> ScdTableSpec:
    """Resolve the SCD2 spec for *table* (None = smoke). Loud-fail on an unknown ops table name."""
    semantics = semantics if semantics is not None else load_field_semantics()
    if table is None:
        partition_history, partition_current = resolve_partition_block(semantics.get("partition_transforms", {}), "scd2")
        return ScdTableSpec(
            table=None,
            history_table=SMOKE_HISTORY_TABLE,
            current_table=SMOKE_CURRENT_TABLE,
            merge_key="rec_id",
            fields=semantics["fields"],
            ordered_columns=_order_columns(semantics["fields"], "rec_id"),
            partition_history=partition_history,
            partition_current=partition_current,
        )
    ops_tables = semantics.get("ops_tables", {})
    spec = ops_tables.get(table)
    if spec is None:
        raise SchemaGateError(f"unknown ops table {table!r}: not in field_semantics ops_tables (have {sorted(ops_tables)})")
    write_mode = spec.get("write_mode", "scd2")
    if write_mode == "control":
        # Directed error, not a widened spec (T2.26): a control-class table has no history/current
        # pair, so ScdTableSpec.history_table (non-Optional; mypy is ratchet-enforced) can never
        # represent it. Resolve control specs through ducklake_control_tables.resolve_control_spec
        # instead -- checked BEFORE spec["merge_key"]/spec["columns"] below, which a control-class
        # projection need not even carry.
        raise SchemaGateError(
            f"{table!r} is a control-class table (write_mode=control): resolve_table_spec does not "
            "serve control-class tables -- use ducklake_control_tables.resolve_control_spec instead "
            "(it is also refused at both the write and read boundaries, Decision 55)"
        )
    if spec.get("table_class") == "event":
        raise SchemaGateError(
            f"{table!r} is an event-class table (table_class=event): it has no merge key or SCD2 pair, so "
            "resolve_table_spec does not serve it -- its spec is the event projection loaded through "
            "src.telemetry.append.EventTableSpec.from_projection"
        )
    merge_key = spec["merge_key"]
    fields = spec["columns"]
    part = spec.get("partition", {})
    try:
        partition_history, partition_current = resolve_partition_block(part, write_mode)
    except PartitionSpecError as exc:  # re-raised as SchemaGateError (Decision 55: runtime defense in depth)
        raise SchemaGateError(f"{table!r}: {exc}") from exc
    return ScdTableSpec(
        table=table,
        history_table=spec["history_table"],
        current_table=spec.get("current_table") if write_mode == "scd2" else None,
        merge_key=merge_key,
        fields=fields,
        ordered_columns=_order_columns(fields, merge_key),
        partition_history=partition_history,
        partition_current=partition_current,
        entity_id_prefix=spec.get("entity_id_prefix"),
        id_keyspace=spec.get("id_keyspace", "caller"),
        write_mode=write_mode,
        id_scheme=spec.get("id_scheme") or "serial",
    )


def check_append_only_guard(spec: ScdTableSpec, require_exists: bool) -> None:
    """Loud-fail when an update (require_exists=True) targets an append_only table (Decision 70).

    Append-only tables are write-once; require_exists=True implies an intent to update an existing
    record, which is illegal. Record state changes as new events with a distinct merge_key.
    """
    if spec.write_mode == "append_only" and require_exists:
        raise AppendOnlyUpdateError(
            f"table {spec.table!r} is append_only: require_exists=True is illegal "
            "(Decision 70 / write-once lifecycle). Record state changes as new events with a new merge_key."
        )


def ops_table_names(semantics: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Return the configured ops_* table names (live + dormant)."""
    semantics = semantics if semantics is not None else load_field_semantics()
    return tuple(semantics.get("ops_tables", {}).keys())


def _column_ddl(spec: ScdTableSpec) -> str:
    """Compose the CREATE-TABLE column list from the spec (NOT NULL on non-nullable columns)."""
    parts: list[str] = []
    for name, sql_type in spec.ordered_columns:
        nullable = bool(spec.fields[name].get("nullable", True))
        parts.append(f"{name} {sql_type}" + ("" if nullable else " NOT NULL"))
    return ", ".join(parts)


def _build_merge_history_sql(spec: ScdTableSpec) -> str:
    cols = [c for c, _ in spec.ordered_columns]
    select = ", ".join(f"? AS {c}" for c in cols)
    col_list = ", ".join(cols)
    values = ", ".join(f"s.{c}" for c in cols)
    # Explicit INSERT (col_list) so the mapping is by name, not by physical position.
    # This makes MERGE safe across schema migrations that add columns via ALTER TABLE ADD COLUMN
    # (which appends at the physical end regardless of the spec's logical order).
    return (
        f"MERGE INTO {CATALOG_ALIAS}.{spec.history_table} AS t "
        f"USING (SELECT {select}) AS s "
        "ON t.ulid = s.ulid "
        f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({values})"
    )


def _build_merge_current_sql(spec: ScdTableSpec) -> str:
    cols = [c for c, _ in spec.ordered_columns]
    select = ", ".join(f"? AS {c}" for c in cols)
    col_list = ", ".join(cols)
    values = ", ".join(f"s.{c}" for c in cols)
    # created_timestamp is carried (never re-stamped on update); the merge key is the ON predicate.
    update_cols = [c for c in cols if c not in (spec.merge_key, "created_timestamp")]
    set_clause = ", ".join(f"{c} = s.{c}" for c in update_cols)
    return (
        f"MERGE INTO {CATALOG_ALIAS}.{spec.current_table} AS t "
        f"USING (SELECT {select}) AS s "
        f"ON t.{spec.merge_key} = s.{spec.merge_key} "
        f"WHEN MATCHED THEN UPDATE SET {set_clause} "
        f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({values})"
    )


def _build_select_existing_created_sql(spec: ScdTableSpec, *, include_status: bool = False) -> str:
    """SELECT the existing row's created_timestamp (+ status, when *include_status*) by merge key.

    `include_status` reuses this SAME existing-row fetch to read the current status for the DAG
    check (write_scd2's require_exists path) -- no second Neon round-trip (Decision 88).
    """
    cols = "created_timestamp, status" if include_status else "created_timestamp"
    return f"SELECT {cols} FROM {CATALOG_ALIAS}.{spec.current_table} WHERE {spec.merge_key} = ?"


def _write_params(spec: ScdTableSpec, record: dict[str, Any], identity: WriteIdentity, created_ts: datetime) -> list[Any]:
    """Bind the ordered-column values for the MERGE source row (derived minted, inputs from record)."""
    params: list[Any] = []
    for name, _ in spec.ordered_columns:
        if name == "ulid":
            params.append(identity.ulid)
        elif name == "created_timestamp":
            params.append(created_ts)
        elif name == "last_updated_timestamp":
            params.append(identity.timestamp)
        else:
            params.append(record.get(name))
    return params


# ---------------------------------------------------------------------------
# Rec status DAG -- resolved-reactivation guard (Decision 103 / Decision 55).
#
# NOT a forward-only/terminal-status whitelist: every LIVE transition passes (failed->open executor
# restart; *->superseded and open/failed->declined postmortem purge; open->{closed,failed,declined,
# superseded}; same-status writes). The ONE thing rejected is a RESOLVED rec (closed/declined/
# superseded) being silently REACTIVATED (-> open). Unrecognised status vocabulary is skipped
# narrowly (Decision 55) -- never silently treated as an illegal transition.
# ---------------------------------------------------------------------------

_ENFORCED_REC_STATUSES = frozenset({"open", "closed", "failed", "declined", "superseded"})
_RESOLVED_REC_STATUSES = frozenset({"closed", "declined", "superseded"})
_ACTIVE_REC_STATUSES = frozenset({"open"})

# table -> {enforced, resolved, active} status vocabulary the DAG is defined over. A table absent
# from this registry has no declared DAG -- check_rec_status_transition is then a permissive no-op.
STATUS_TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    "ops_recommendations": {
        "enforced": _ENFORCED_REC_STATUSES,
        "resolved": _RESOLVED_REC_STATUSES,
        "active": _ACTIVE_REC_STATUSES,
    },
}


def check_rec_status_transition(table: str, existing_status: Any, new_status: Any) -> None:
    """Raise StatusTransitionError iff *table* declares a DAG and this is a resolved->active reactivation.

    Permissive-skip (never raises) when: *table* has no declared DAG, either status is outside the
    declared enforced vocabulary, or the transition is not resolved->active (this also covers
    same-status writes, since resolved and active are disjoint).
    """
    dag = STATUS_TRANSITIONS.get(table)
    if dag is None:
        return
    if existing_status not in dag["enforced"] or new_status not in dag["enforced"]:
        return
    if existing_status in dag["resolved"] and new_status in dag["active"]:
        raise StatusTransitionError(
            f"{table}: illegal status transition {existing_status!r} -> {new_status!r} -- a resolved rec "
            "must not be silently reactivated (Decision 103). Record the reopen as a new event/rec instead."
        )


# ---------------------------------------------------------------------------
# Schema gate -- loud-fail on unknown / derived / missing / mis-typed input fields
# ---------------------------------------------------------------------------


def schema_gate(record: dict[str, Any], semantics: dict[str, Any] | None = None, *, table: str | None = None) -> None:
    """Validate a caller-supplied write record against the contract. Loud-fail (Decision 55).

    `table=None` validates against the smoke `fields` map (T2.17 back-compat); a table name validates
    against that ops_tables entry's `columns` map (T2.19).

    Rejects (raises SchemaGateError):
      - any key not present in the contract (unknown field),
      - any key whose role is `derived` (the caller must not supply derived values),
      - any required (`nullable: false`) input field that is missing, null, or empty,
      - any input field whose value is not the contract's declared SQL type.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    fields: dict[str, Any] = semantics["fields"] if table is None else resolve_table_spec(table, semantics).fields

    for key in record:
        spec = fields.get(key)
        if spec is None:
            raise SchemaGateError(f"unknown field {key!r}: not in the field-semantics contract")
        if spec["role"] == "derived":
            raise SchemaGateError(f"field {key!r} is derived: the runtime mints it; the caller must not supply it")

    for name, spec in fields.items():
        if spec["role"] != "input":
            continue
        nullable = bool(spec.get("nullable", True))
        present = name in record
        value = record.get(name)
        if not nullable and (not present or value is None):
            raise SchemaGateError(f"required input field {name!r} is missing or null")
        if present and value is not None:
            expected = _PY_TYPE_FOR_SQL.get(spec["sql_type"])
            if expected is not None and not isinstance(value, expected):
                raise SchemaGateError(f"field {name!r} expected {expected.__name__}, got {type(value).__name__}")
            if expected is str and not nullable and value == "":
                raise SchemaGateError(f"required input field {name!r} is empty")
