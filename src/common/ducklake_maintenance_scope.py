"""Pure, connection-free maintenance scope resolution (compaction-scope-policy-matrix, T2.18).

Replaces the retired suffix-matching naming-convention predicate action_merge_ops used to
discover which tables the production compaction cadence touches with catalog enumeration crossed
against a declared per-class, per-verb policy matrix (config/lambda/ducklake/
field_semantics.static.yaml -> maintenance_policy).

Every table is classified via the SAME registry the runtime already carries -- field_semantics'
ops_tables entries, keyed by each table's own declared `write_mode` and `history_table`/
`current_table` names -- never a string pattern matched against a physical table name. No I/O:
every function here operates on already-fetched catalog rows / already-loaded registry data, so
each predicate is directly unit-testable (the split Decision 188 used for
src/common/ducklake_maintenance_ops.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.ducklake_scd2_schema import load_field_semantics

# The declared maintenance-verb universe. DECLARED independently of maintenance_policy's own keys
# -- scripts/checks/contracts/validate_maintenance_policy_matrix.py imports this constant (never
# derives the verb universe from the matrix itself) so the exhaustiveness gate cannot become a
# blind oracle that is trivially exhaustive against its own matrix.
# reconcile_partitions / rewrite_partition_layout join as PER-TABLE verbs (Decision 204, amends
# Decision 192 cl.2's literal ("merge_ops",) pin while keeping its rule: catalog-wide verbs such
# as gc_ops stay out -- see the matrix's own "CATALOG-WIDE VERBS TAKE NO CELL" header).
VERB_UNIVERSE: tuple[str, ...] = (
    "merge_ops",
    "reconcile_partitions",
    "rewrite_partition_layout",
    "rewrite_current_deletes",
)

# The production-resident smoke-harness pair's maintenance class (Decision 191 amendment,
# PLAN-ducklake-smoke-harness-classification): sourced from field_semantics' `tables:` block
# (declared data), never inferred from a "smoke" name pattern.
SMOKE_HARNESS_CLASS = "smoke_harness"


class DuckLakeMaintenanceScopeError(RuntimeError):
    """Loud-fail for a maintenance-scope invariant violation (Decision 55): a table classified to
    a class the policy matrix has no cell for, or a false cell missing its required reason.
    validate_maintenance_policy_matrix should catch either before this ever runs in production."""


@dataclass(frozen=True)
class ClassifiedTable:
    """One physical table's resolved classification."""

    physical_name: str
    table_id: str
    table_class: str  # "scd2" | "append_only" | "control" (any write_mode value declared in ops_tables)
    side: str  # "history" | "current" | "table" (control's single physical table)


def build_registry(semantics: dict[str, Any] | None = None) -> dict[str, ClassifiedTable]:
    """Map every physical table name declared in field_semantics.ops_tables to its classification.

    Derived from each ops_tables entry's own declared write_mode + history_table/current_table (or,
    for a control-class entry, the table_id itself as its one physical table) -- never from a
    naming-convention pattern matched against physical table names.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    ops_tables = semantics.get("ops_tables", {})
    out: dict[str, ClassifiedTable] = {}
    for table_id, spec in ops_tables.items():
        write_mode = spec.get("write_mode", "scd2")
        if write_mode == "control":
            out[table_id] = ClassifiedTable(table_id, table_id, "control", "table")
            continue
        history_table = spec.get("history_table")
        if history_table:
            out[history_table] = ClassifiedTable(history_table, table_id, write_mode, "history")
        if write_mode != "append_only":
            current_table = spec.get("current_table")
            if current_table:
                out[current_table] = ClassifiedTable(current_table, table_id, write_mode, "current")

    smoke_tables = semantics.get("tables")
    if smoke_tables:
        for side in ("history", "current"):
            side_spec = smoke_tables.get(side)
            if not side_spec:
                continue
            physical_name = side_spec["name"]
            if physical_name in out:
                raise DuckLakeMaintenanceScopeError(
                    f"smoke_harness table {physical_name!r} (declared under `tables.{side}.name`) collides "
                    "with a physical table name already declared under ops_tables -- registry membership "
                    "must be unambiguous"
                )
            out[physical_name] = ClassifiedTable(physical_name, physical_name, SMOKE_HARNESS_CLASS, side)
    return out


def class_universe(semantics: dict[str, Any] | None = None) -> frozenset[str]:
    """The DECLARED table-class universe: every distinct write_mode value across ops_tables
    (absent write_mode defaults to "scd2"), plus smoke_harness when field_semantics declares a
    `tables:` block. Never read from maintenance_policy's own keys."""
    semantics = semantics if semantics is not None else load_field_semantics()
    ops_tables = semantics.get("ops_tables", {})
    universe = {spec.get("write_mode", "scd2") for spec in ops_tables.values()}
    if semantics.get("tables"):
        universe.add(SMOKE_HARNESS_CLASS)
    return frozenset(universe)


def classify_table(physical_name: str, registry: dict[str, ClassifiedTable]) -> ClassifiedTable | None:
    """Resolve *physical_name* (an information_schema table name) to its classification.

    Returns None when the catalog enumeration discovered a table absent from the registry
    (unclassifiable) -- the caller collects these rather than raising per-table (see resolve_scope).
    """
    return registry.get(physical_name)


def load_policy(semantics: dict[str, Any] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    """Load the maintenance_policy matrix from field_semantics.yaml.

    Raises when absent: the generator passthrough (scripts/schema_to_field_semantics.py) is
    REQUIRED, not conditional, so an absent matrix here means the sidecar wasn't regenerated --
    fail closed rather than silently treating every table as unclassified.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    policy = semantics.get("maintenance_policy")
    if not policy:
        raise DuckLakeMaintenanceScopeError(
            "maintenance_policy is absent from field_semantics.yaml -- edit "
            "config/lambda/ducklake/field_semantics.static.yaml and regenerate via "
            "scripts.schema_to_field_semantics (never hand-edit field_semantics.yaml)"
        )
    return policy


@dataclass(frozen=True)
class ScopeResolution:
    """The per-invocation outcome of resolve_scope."""

    to_merge: tuple[str, ...]
    skipped: tuple[dict[str, str], ...]
    unclassified: tuple[str, ...]


def resolve_scope(
    physical_tables: list[str],
    *,
    verb: str,
    policy: dict[str, dict[str, dict[str, Any]]],
    registry: dict[str, ClassifiedTable],
) -> ScopeResolution:
    """Classify every enumerated physical table and apply the policy cell for *verb*.

    Unclassifiable tables are COLLECTED, never raised on individually -- the caller
    (action_merge_ops) still merges every classified table and raises the aggregate after its own
    loop, naming both unclassified tables and any per-table merge failures (Decision 188 pt 3
    extension, this plan's Decision).
    """
    to_merge: list[str] = []
    skipped: list[dict[str, str]] = []
    unclassified: list[str] = []
    for physical_name in physical_tables:
        classified = classify_table(physical_name, registry)
        if classified is None:
            unclassified.append(physical_name)
            continue
        cell = policy.get(classified.table_class, {}).get(verb)
        if not isinstance(cell, dict) or "apply" not in cell:
            raise DuckLakeMaintenanceScopeError(
                f"maintenance_policy has no cell for class={classified.table_class!r} verb={verb!r} "
                f"(table {physical_name!r}) -- every class needs a cell for every verb "
                "(validate_maintenance_policy_matrix should have caught this before deploy)"
            )
        if cell["apply"]:
            to_merge.append(physical_name)
        else:
            reason = cell.get("reason")
            if not reason:
                raise DuckLakeMaintenanceScopeError(
                    f"maintenance_policy[{classified.table_class!r}][{verb!r}] is apply=false but "
                    "carries no reason (validate_maintenance_policy_matrix should have caught this)"
                )
            skipped.append({"table": physical_name, "table_class": classified.table_class, "reason": reason})
    return ScopeResolution(tuple(to_merge), tuple(skipped), tuple(unclassified))


@dataclass(frozen=True)
class ReconciliationResult:
    """The catalog-vs-registry comparison resolve_scope's caller reports alongside per_table."""

    registered_absent: tuple[dict[str, str], ...]
    catalog_unregistered: tuple[str, ...]


def reconcile_catalog(
    physical_tables: list[str],
    *,
    semantics: dict[str, Any] | None = None,
    registry: dict[str, ClassifiedTable] | None = None,
) -> ReconciliationResult:
    """Compare the CATALOG (the enumerated physical tables) against the registry.

    `registered_absent`: a registry table_id never seen in this enumeration, dispositioned by the
    registry's own `status` field (live | dormant | smoke) -- a dormant/smoke table's physical
    absence from the PRODUCTION catalog is expected, not a defect; only a `status: live` entry's
    absence is notable. `catalog_unregistered`: mirrors resolve_scope's unclassified set -- a
    catalog table with no registry entry at all. The universe compared against is the CATALOG
    (the caller's enumerated tables), never the registry alone.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    registry = registry if registry is not None else build_registry(semantics)
    ops_tables = semantics.get("ops_tables", {})
    seen_table_ids = {registry[t].table_id for t in physical_tables if t in registry}
    registered_absent: list[dict[str, str]] = [
        {"table_id": table_id, "status": spec.get("status", "unknown")}
        for table_id, spec in ops_tables.items()
        if table_id not in seen_table_ids
    ]
    catalog_unregistered = tuple(t for t in physical_tables if t not in registry)
    return ReconciliationResult(tuple(registered_absent), catalog_unregistered)
