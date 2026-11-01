"""Owner-created, writer-verified telemetry tables (T2.36, slice 2a-2; the owner-provisioning fix, rec-4294).

DuckLake's CREATE TABLE also creates a physical Postgres table per table (ducklake_inlined_data_*), so DDL needs CREATE on the
metadata schema, which the telemetry writer's scoped login never holds. The work is therefore split:
- create_telemetry_tables runs under the catalog OWNER login (ducklake_maintenance_smoke's create_telemetry_tables verb, run by
  the deploy pipeline): it creates and partitions the ABSENT tables and adds a declared, contract-optional (nullable, no
  not-null or required_when rule) column that an existing EVENT table lacks (Decision 222, amending Decision 216 clauses 2 and
  3). It writes no row, never drops, renames or retypes and never sets inlining (Decision 107). A half-present SCD2 pair, a
  partition drift or any other column drift fails loud first and writes nothing.
- provision_telemetry_catalog runs under the writer's scoped login and issues NO DDL: it refuses absent tables by name,
  re-checks drift, and seeds the two counter rows without ever lowering a value (the counter table keeps one write path).
- ownership_report and session_can_create read pg_catalog through DuckLake's attached metadata database, to prove the single
  owner invariant (every relation owned by the schema owner, the scoped login holding no CREATE on the schema or public).
There is no drop, truncate or force-recreate path anywhere here (Decision 55).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.common import ducklake_maintenance_scope as scope
from src.common.ducklake_control_tables import ENTITY_COUNTERS_TABLE, resolve_control_spec
from src.common.ducklake_partition_layout import PartitionDrift, TableLayout, compare_to_declared
from src.common.ducklake_scd2_schema import (
    CATALOG_ALIAS,
    DuckLakeRuntimeError,
    ScdTableSpec,
    is_event_table,
    resolve_table_spec,
)
from src.common.ducklake_tables import create_control_table, create_scd2_tables
from src.telemetry.append import EventTableSpec

DIMENSIONS = ("ops_tenants", "ops_projects")
SEEDED_COUNTERS = DIMENSIONS
_ALWAYS_NOT_NULL = frozenset({"tenant_id", "project_id"})
_META_SCHEMA = f"__ducklake_metadata_{CATALOG_ALIAS}"


SCOPED_LOGIN = "ducklake_telemetry_writer"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class CreateResult:
    tables: tuple[str, ...]
    created: tuple[str, ...]
    added: tuple[str, ...] = ()


@dataclass(frozen=True)
class OwnershipReport:
    foreign_owned: int
    schema_owner_is_current_user: bool
    scoped_create_on_schema: bool
    scoped_create_on_public: bool
    schema_owner_held: bool
    schema_owner: str


@dataclass(frozen=True)
class ProvisionResult:
    tables: tuple[str, ...]
    counters: dict[str, int]
    created: tuple[str, ...] = ()


def event_table_names(semantics: dict[str, Any]) -> tuple[str, ...]:
    return tuple(t for t in semantics.get("ops_tables", {}) if is_event_table(t, semantics))


def _event_ddl(spec: EventTableSpec) -> str:
    not_null = spec.not_null | _ALWAYS_NOT_NULL
    return ", ".join(
        f"{name} {sql_type}" + (" NOT NULL" if name in not_null else "") for name, sql_type in spec.columns.items()
    )


def _declared_columns(semantics: dict[str, Any]) -> dict[str, set[str]]:
    """physical table -> declared column names, for every table this function provisions."""
    declared: dict[str, set[str]] = {}
    for name in event_table_names(semantics):
        declared[name] = set(EventTableSpec.from_projection(name, semantics["ops_tables"][name]).columns)
    for name in DIMENSIONS:
        spec: ScdTableSpec = resolve_table_spec(name, semantics)
        cols = {c for c, _ in spec.ordered_columns}
        declared[spec.history_table] = cols
        declared[str(spec.current_table)] = cols
    declared[ENTITY_COUNTERS_TABLE] = {c for c, _ in resolve_control_spec(ENTITY_COUNTERS_TABLE).ordered_columns}
    return declared


def target_tables(semantics: dict[str, Any]) -> tuple[str, ...]:
    """The nine physical tables this function provisions, sorted."""
    return tuple(sorted(_declared_columns(semantics)))


def existing_tables(con: Any) -> set[str]:
    rows = con.execute(f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{CATALOG_ALIAS}'").fetchall()
    return {str(r[0]) for r in rows}


def _live_layout(con: Any, physical: str, table_class: str) -> TableLayout:
    table_id_row = con.execute(
        f"SELECT table_id FROM {_META_SCHEMA}.ducklake_table WHERE table_name = ? AND end_snapshot IS NULL", [physical]
    ).fetchone()
    table_id = int(table_id_row[0])
    active = con.execute(
        f"SELECT partition_id FROM {_META_SCHEMA}.ducklake_partition_info WHERE table_id = ? AND end_snapshot IS NULL",
        [table_id],
    ).fetchone()
    partition_id = int(active[0]) if active else None
    transforms: tuple[tuple[str, str], ...] = ()
    if partition_id is not None:
        rows = con.execute(
            f"SELECT pc.transform, col.column_name FROM {_META_SCHEMA}.ducklake_partition_column pc "
            f"JOIN {_META_SCHEMA}.ducklake_column col ON col.column_id = pc.column_id AND col.table_id = pc.table_id "
            "WHERE pc.partition_id = ? AND pc.table_id = ? ORDER BY pc.partition_key_index",
            [partition_id, table_id],
        ).fetchall()
        transforms = tuple((str(r[0]), str(r[1])) for r in rows)
    return TableLayout(physical, table_class, transforms, partition_id, 0, 0)


def _live_columns(con: Any, physical: str) -> set[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_catalog = ? AND table_name = ?",
        [CATALOG_ALIAS, physical],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _partition_drifts(con: Any, present: list[str], semantics: dict[str, Any]) -> list[PartitionDrift]:
    registry = scope.build_registry(semantics)
    layouts = {t: _live_layout(con, t, registry[t].table_class) for t in present}
    return compare_to_declared(layouts, semantics=semantics) if layouts else []


def inspect_partitions(con: Any, existing: set[str], semantics: dict[str, Any]) -> dict[str, bool]:
    """Read-only: PRESENT target table -> live partitions equal the declared spec.

    Restricted to the nine target tables, so an unrelated catalog table never fails provisioning or the probe.
    """
    present = sorted(t for t in _declared_columns(semantics) if t in existing)
    drifted = {d.physical for d in _partition_drifts(con, present, semantics)}
    return {t: t not in drifted for t in present}


def _drift_problems(
    con: Any, existing: set[str], semantics: dict[str, Any]
) -> tuple[list[str], dict[str, tuple[set[str], set[str]]]]:
    """(partition problems, physical -> (missing, extra) live-vs-declared column difference) for present target tables."""
    declared = _declared_columns(semantics)
    present = sorted(t for t in declared if t in existing)
    problems = [
        f"{d.physical}: partitions declared {list(d.declared)} live {list(d.live)}"
        for d in _partition_drifts(con, present, semantics)
    ]
    diffs: dict[str, tuple[set[str], set[str]]] = {}
    for physical in present:
        live = _live_columns(con, physical)
        if live != declared[physical]:
            diffs[physical] = (declared[physical] - live, live - declared[physical])
    return problems, diffs


def _refuse(problems: list[str]) -> None:
    raise DuckLakeRuntimeError(
        "provision_telemetry refuses pre-existing tables that drift from their declared spec "
        f"(admin repair under Decision 204; nothing was written): {'; '.join(problems)}"
    )


def check_existing_tables(con: Any, existing: set[str], semantics: dict[str, Any]) -> tuple[str, ...]:
    """Read-only: every pre-existing target table's live partitions and columns equal its declared spec.

    Raises a loud DuckLakeRuntimeError naming the table and the drift, never a value, before anything is written.
    """
    problems, diffs = _drift_problems(con, existing, semantics)
    problems += [f"{p}: columns missing {sorted(m)} extra {sorted(x)}" for p, (m, x) in diffs.items()]
    if problems:
        _refuse(problems)
    return tuple(sorted(t for t in _declared_columns(semantics) if t in existing))


def _optional_additions(semantics: dict[str, Any], table: str, missing: set[str]) -> list[str] | None:
    """The sorted columns of an event table that may be added (all contract-optional), or None when any may not."""
    if table not in event_table_names(semantics):
        return None
    entry = semantics["ops_tables"][table]
    event = EventTableSpec.from_projection(table, entry)
    for name in missing:
        col = entry["columns"][name]
        if (
            name in event.not_null
            or name in _ALWAYS_NOT_NULL
            or col.get("nullable") is not True
            or "not_null" in col
            or "required_when" in col
        ):
            return None
    return sorted(missing)


def _plan_additions(con: Any, existing: set[str], semantics: dict[str, Any]) -> list[tuple[str, str]]:
    """The (table, column) additions the leg may make; every other drift raises before any DDL."""
    problems, diffs = _drift_problems(con, existing, semantics)
    additions: list[tuple[str, str]] = []
    for physical, (missing, extra) in sorted(diffs.items()):
        allowed = None if extra else _optional_additions(semantics, physical, missing)
        if allowed is None:
            problems.append(f"{physical}: columns missing {sorted(missing)} extra {sorted(extra)}")
        else:
            additions += [(physical, c) for c in allowed]
    if problems:
        _refuse(problems)
    return additions


def _seed_counter(con: Any, name: str, floor: int) -> None:
    rows = [
        int(r[0])
        for r in con.execute(
            f"SELECT current_value FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?", [name]
        ).fetchall()
    ]
    if len(rows) == 1 and rows[0] >= floor:
        return
    value = max([floor, *rows])
    con.execute(f"DELETE FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?", [name])
    con.execute(f"INSERT INTO {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} VALUES (?, ?)", [name, value])


def _refuse_half_pairs(existing: set[str], semantics: dict[str, Any]) -> None:
    for name in DIMENSIONS:
        spec = resolve_table_spec(name, semantics)
        if len({spec.history_table in existing, str(spec.current_table) in existing}) == 2:
            raise DuckLakeRuntimeError(
                f"{name}: exactly one of {spec.history_table}/{spec.current_table} exists -- a half-present SCD2 pair "
                "is an admin repair (Decision 55); creating the other half would re-ALTER the existing one"
            )


def create_telemetry_tables(con: Any, *, semantics: dict[str, Any]) -> CreateResult:
    """Create the ABSENT telemetry tables and add declared optional columns an event table lacks (owner login).

    Writes no row; never drops, renames or retypes (Decision 222).
    """
    existing = existing_tables(con)
    _refuse_half_pairs(existing, semantics)
    additions = _plan_additions(con, existing, semantics)
    for table, column in additions:
        sql_type = EventTableSpec.from_projection(table, semantics["ops_tables"][table]).columns[column]
        con.execute(f"ALTER TABLE {CATALOG_ALIAS}.{table} ADD COLUMN {column} {sql_type}")
    if additions:
        check_existing_tables(con, existing, semantics)

    created: list[str] = []
    for name in event_table_names(semantics):
        if name in existing:
            continue
        entry = semantics["ops_tables"][name]
        event = EventTableSpec.from_projection(name, entry)
        con.execute(f"CREATE TABLE {CATALOG_ALIAS}.{event.table} ({_event_ddl(event)})")
        con.execute(f"ALTER TABLE {CATALOG_ALIAS}.{event.table} SET PARTITIONED BY ({entry['partition']['history']})")
        created.append(name)
    for name in DIMENSIONS:
        spec = resolve_table_spec(name, semantics)
        if spec.history_table not in existing:
            create_scd2_tables(con, table=name)
            created.extend([spec.history_table, str(spec.current_table)])
    if ENTITY_COUNTERS_TABLE not in existing:
        create_control_table(con, table=ENTITY_COUNTERS_TABLE)
        created.append(ENTITY_COUNTERS_TABLE)
    return CreateResult(target_tables(semantics), tuple(created), tuple(f"{t}.{c}" for t, c in additions))


def provision_telemetry_catalog(con: Any, *, semantics: dict[str, Any]) -> ProvisionResult:
    """Verify the nine tables exist and match their declared spec, then seed the two counter rows. Issues NO DDL."""
    existing = existing_tables(con)
    _refuse_half_pairs(existing, semantics)
    absent = [t for t in target_tables(semantics) if t not in existing]
    if absent:
        raise DuckLakeRuntimeError(
            f"telemetry tables absent: {absent}. Tables are created only under the catalog owner: "
            "ducklake_maintenance_smoke's create_telemetry_tables on smoke, ducklake_maintenance's "
            "provision_telemetry_catalog on production, before any write; the telemetry writer's scoped login holds no CREATE"
        )
    check_existing_tables(con, existing, semantics)

    for name in SEEDED_COUNTERS:
        current = str(resolve_table_spec(name, semantics).current_table)
        floor = int(con.execute(f"SELECT count(*) FROM {CATALOG_ALIAS}.{current}").fetchone()[0])
        _seed_counter(con, name, floor)
    counters = {
        name: int(
            con.execute(
                f"SELECT current_value FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?", [name]
            ).fetchone()[0]
        )
        for name in SEEDED_COUNTERS
    }
    return ProvisionResult(target_tables(semantics), counters, ())


def _guard(value: str, kind: str) -> str:
    if not _IDENTIFIER.match(value):
        raise DuckLakeRuntimeError(f"unsafe {kind} name refused before any SQL is built")
    return value


def _pg_row(con: Any, inner_sql: str) -> tuple[Any, ...]:
    """The first row of inner_sql run on the postgres database DuckLake attaches for its metadata.

    duckdb_databases() lists only the DuckLake catalog (type ducklake); the metadata catalog is reachable under its own
    __ducklake_metadata_<alias> name, the one the partition-layout reads already use.
    """
    quoted = inner_sql.replace("'", "''")
    row = con.execute(f"SELECT * FROM postgres_query('{_META_SCHEMA}', '{quoted}')").fetchone()
    return tuple(row)


def ownership_report(con: Any, *, meta_schema: str, scoped_logins: tuple[str, ...] = (SCOPED_LOGIN,)) -> OwnershipReport:
    """Read-only: foreign-owned relations, the schema owner (and whether the session holds it), scoped CREATE.

    Each scoped login is resolved with to_regrole, so one that does not exist yet holds no CREATE instead of raising 42704.
    """
    schema = _guard(meta_schema, "meta schema")
    logins = [_guard(login, "login") for login in scoped_logins]

    def holds_create(target: str) -> str:
        reads = [f"COALESCE(has_schema_privilege(to_regrole('{login}'), {target}, 'CREATE'), false)" for login in logins]
        return "(" + " OR ".join(reads or ["false"]) + ")"

    row = _pg_row(
        con,
        "SELECT "
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p', 'S', 'v', 'm') "
        "AND c.relowner <> n.nspowner) AS foreign_owned, "
        "(SELECT n.nspowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) FROM pg_namespace n "
        f"WHERE n.nspname = '{schema}') AS schema_owner_is_current_user, "
        f"{holds_create(f"'{schema}'")} AS scoped_create_on_schema, "
        f"{holds_create("'public'")} AS scoped_create_on_public, "
        "COALESCE((SELECT pg_has_role(current_user, n.nspowner, 'SET') FROM pg_namespace n "
        f"WHERE n.nspname = '{schema}'), false) AS schema_owner_held, "
        f"COALESCE((SELECT n.nspowner::regrole::text FROM pg_namespace n WHERE n.nspname = '{schema}'), '') AS schema_owner",
    )
    return OwnershipReport(int(row[0]), bool(row[1]), bool(row[2]), bool(row[3]), bool(row[4]), str(row[5]))


def session_can_create(con: Any, *, meta_schema: str) -> bool:
    """Whether the connection's own login holds CREATE on the metadata schema (the writer must not)."""
    schema = _guard(meta_schema, "meta schema")
    return bool(_pg_row(con, f"SELECT has_schema_privilege(current_user, '{schema}', 'CREATE') AS can_create")[0])
