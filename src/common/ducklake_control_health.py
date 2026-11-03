"""Control-class table health assertion (T2.26 control-table-class-and-counter-conformance;
split out of ducklake_maintenance.py, Decision 128 decompose-by-default, to stay under the
500-SLOC-per-file budget).

The periodic substitute for DQ-runner coverage on a control-class table (dq_scope: exempt,
docs/contracts/ops_entity_counters.yaml): the DQ runner has no reachable read path to a
writer_internal / read_boundary=none table, so control_health -- scheduled every 6h on the admin
ducklake_maintenance function -- IS the coverage. Each invariant is a FAILING condition (raises
DuckLakeMaintenanceError on violation, Decision 55): never a report that a caller could silently
ignore.

Dependency: imports DuckLakeMaintenanceError from ducklake_maintenance (one-directional --
ducklake_maintenance never imports this module back, so the two stay acyclic) and the control-class
registry from ducklake_runtime (the same facade ducklake_maintenance.py itself imports from).
"""

from __future__ import annotations

from typing import Any, Callable

from src.common.ducklake_maintenance import DuckLakeMaintenanceError, _count_files
from src.common.ducklake_runtime import (
    CATALOG_ALIAS,
    control_table_names,
    is_control_table,
    is_event_table,
    load_field_semantics,
    ops_table_names,
    resolve_table_spec,
)

# Per-control-table live-file ceiling. A tunable guardrail constant, same shape as
# ducklake_maintenance.SNAPSHOT_RETAIN_DAYS/GC_BREAKER_FILE_FRACTION -- tuning it to make
# control_health pass is a Decision-55 violation. Must match docs/contracts/ops_entity_counters.yaml's
# health.live_file_ceiling prose (that contract has no schema slot for a live-checked value --
# ContractGovernance is a closed field enumeration and scripts/contracts_schema.py is out of this
# plan's scope, so the Python constant here is the single enforced source; the contract text is the
# human-facing cross-reference). A partitioned, no-op-free control table should accumulate very few
# live files between GC passes; a count above this ceiling signals either the no-op-free
# counter-advance guard regressed or GC has stopped running against the production catalog.
CONTROL_LIVE_FILE_CEILING: dict[str, int] = {
    "ops_entity_counters": 200,
}


def _writer_keyspace_tables() -> list[str]:
    """ops_* tables with a writer-owned entity keyspace (id_keyspace=writer, any id_scheme) -- each
    owns exactly one row in every control-class counter table WHEN it exists in the catalog under
    check (live tables are always expected). Control-class and event-class entries are excluded
    from the scan: neither has a keyspace (resolve_table_spec raises a directed error for them)."""
    tables: list[str] = []
    for t in ops_table_names():
        if is_control_table(t) or is_event_table(t):
            continue
        if resolve_table_spec(t).id_keyspace == "writer":
            tables.append(t)
    return tables


def _present_history_tables(con: Any, catalog: str) -> set[str]:
    """Physical table names in *catalog* (one information_schema read, Decision 88 egress-cheap)."""
    rows = con.execute(f"SELECT table_name FROM information_schema.tables WHERE table_catalog = '{catalog}'").fetchall()
    return {r[0] for r in rows}


def _expected_counter_tables(con: Any, catalog: str) -> tuple[list[str], list[str]]:
    """(expected counter-row tables, absent non-live tables). A status-live writer-keyspace table is
    always expected -- a vanished live table and its counter still fail; a non-live one only when its
    history table exists in the catalog under check, otherwise it is reported as absent, never dropped."""
    semantics = load_field_semantics()
    candidates = _writer_keyspace_tables()
    live = [t for t in candidates if semantics["ops_tables"][t].get("status") == "live"]
    non_live = [t for t in candidates if t not in live]
    present = _present_history_tables(con, catalog) if non_live else set()
    expected = live + [t for t in non_live if resolve_table_spec(t).history_table in present]
    absent = [t for t in non_live if t not in expected]
    return expected, absent


def control_health(
    con: Any,
    *,
    catalog: str = CATALOG_ALIAS,
    metric_sink: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Assert per-control-table invariants: row count, counter floor, live-file ceiling.

    Row count: a control-class table carries exactly one row per writer-keyspace ops table present in
    the catalog under check (live tables always; pre-production ones only once physically present --
    absent ones are reported in `absent_tables`). Counter floor: serial scheme -- each row's
    current_value is at or above the max entity id already allocated in its owning history table (a
    value BELOW it means the counter was stranded, Decision 84 I-2 corruption); ulid scheme -- at or
    above the owning current table's row count. Live-file ceiling: the table's live DuckLake
    file count stays below CONTROL_LIVE_FILE_CEILING (docs/contracts/ops_entity_counters.yaml's
    health.live_file_ceiling prose).

    `metric_sink`, when provided, ALWAYS receives ControlTableInvariantViolation (0 on a clean
    pass) BEFORE any raise, so the metric/alarm signal survives the exception -- the caller
    (src/lambdas/ducklake_maintenance/health_actions.py action_control_health) supplies one bound to the
    DuckLakeMaintenance namespace.
    """
    writer_keyspace_tables, absent_tables = _expected_counter_tables(con, catalog)
    expected_rows = len(writer_keyspace_tables)

    violations: list[str] = []
    per_table: dict[str, Any] = {}

    for table in control_table_names():
        rows = con.execute(f"SELECT counter_name, current_value FROM {catalog}.{table}").fetchall()
        actual_rows = len(rows)
        if actual_rows != expected_rows:
            violations.append(
                f"{table}: row count {actual_rows} != expected {expected_rows} "
                f"(writer-keyspace tables: {sorted(writer_keyspace_tables)})"
            )

        counter_by_name = dict(rows)
        counter_floor_ok = True
        for owning_table in writer_keyspace_tables:
            if owning_table not in counter_by_name:
                continue  # already recorded as a row-count violation above
            spec = resolve_table_spec(owning_table)
            current_value = int(counter_by_name[owning_table])
            if spec.id_scheme == "ulid":
                count_row = con.execute(f"SELECT count(*) FROM {catalog}.{spec.current_table}").fetchone()
                registered = int(count_row[0]) if count_row and count_row[0] is not None else 0
                if current_value < registered:
                    counter_floor_ok = False
                    violations.append(
                        f"{table}[{owning_table}]: current_value {current_value} below registered row count "
                        f"{registered} in {spec.current_table} (ulid floor)"
                    )
                continue
            prefix = spec.entity_id_prefix
            max_row = con.execute(
                f"SELECT coalesce(max(CAST(regexp_extract({spec.merge_key}, '^{prefix}([0-9]+)$', 1) AS BIGINT)), 0) "
                f"FROM {catalog}.{spec.history_table} "
                f"WHERE {spec.merge_key} LIKE '{prefix}%' AND regexp_matches({spec.merge_key}, '^{prefix}[0-9]+$')"
            ).fetchone()
            max_allocated = int(max_row[0]) if max_row and max_row[0] is not None else 0
            if current_value < max_allocated:
                counter_floor_ok = False
                violations.append(
                    f"{table}[{owning_table}]: current_value {current_value} below max allocated id "
                    f"{max_allocated} in {spec.history_table}"
                )

        live_files = _count_files(con, catalog, table)
        ceiling = CONTROL_LIVE_FILE_CEILING.get(table)
        if ceiling is not None and live_files > ceiling:
            violations.append(f"{table}: live file count {live_files} exceeds ceiling {ceiling}")

        per_table[table] = {
            "row_count": actual_rows,
            "expected_row_count": expected_rows,
            "counter_floor_ok": counter_floor_ok,
            "live_file_count": live_files,
            "live_file_ceiling": ceiling,
        }

    if metric_sink is not None:
        metric_sink("ControlTableInvariantViolation", float(len(violations)))

    if violations:
        raise DuckLakeMaintenanceError(
            "control_health: invariant violation(s) -- " + "; ".join(violations) + " (Decision 55: stop and RCA)"
        )

    return {
        "ok": True,
        "action": "control_health",
        "tables": per_table,
        "absent_tables": sorted(absent_tables),
        "violations": [],
    }
