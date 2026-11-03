"""DuckLake live partition-layout READ half (Decision 204, PLAN-ducklake-partition-layout-remediation).

READ HALF ONLY (plan-critique r1): the reader boundary holds the same owner-privileged Neon
credential as the writer, so mutating code must never ship in the reader bundle -- see
ducklake_partition_rewrite.py for the MUTATE half, which imports this module but is never imported
back (Decision 80 acyclic-import discipline; the reader bundle carries this module and never that
one).

Reads DuckLake's own catalog metadata (__ducklake_metadata_<alias>) for the LIVE partition layout
of every table the field_semantics registry can classify: ducklake_table (end_snapshot IS NULL for
the live physical row), ducklake_partition_info (end_snapshot IS NULL for the ACTIVE scheme
generation), ducklake_partition_column + ducklake_column (the active scheme's ordered
(transform, column_name) pairs), and ducklake_data_file (end_snapshot IS NULL for live files;
partition_id names which scheme generation wrote each file, per #1278 -- NEVER a per-value bucket).
A live file whose partition_id IS DISTINCT FROM the active scheme id (including NULL, pre-partition)
is a legacy-scheme file: it was laid out under a superseded ALTER ... SET PARTITIONED BY generation
and needs rewrite_legacy_layout (ducklake_partition_rewrite.py) to physically re-lay it.

Imports scd2_schema / control_tables / maintenance_scope / partition_spec only (Decision 80).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common import ducklake_control_tables as control_tables
from src.common import ducklake_maintenance_scope as maintenance_scope
from src.common.ducklake_partition_spec import normalize_partition_spec
from src.common.ducklake_scd2_schema import (
    CATALOG_ALIAS,
    DuckLakeRuntimeError,
    is_event_table,
    load_field_semantics,
    resolve_table_spec,
)


class PartitionLayoutError(DuckLakeRuntimeError):
    """Loud-fail for the read half: a catalog-metadata read error, or a table the field_semantics
    registry cannot classify (Decision 191 -- never silently skipped)."""


@dataclass(frozen=True)
class TableLayout:
    """One physical table's resolved LIVE partition layout, read from catalog metadata."""

    physical: str
    table_class: str  # "scd2" | "append_only" | "control"
    live_transforms: tuple[tuple[str, str], ...]  # DuckLake's own (transform, column_name) pairs, active scheme only
    active_scheme_id: int | None  # ducklake_partition_info.partition_id of the live (end_snapshot IS NULL) scheme
    live_files: int
    legacy_scheme_files: int  # live files whose partition_id != active_scheme_id (or NULL = pre-partition)


@dataclass(frozen=True)
class PartitionDrift:
    """One physical table whose live partition spec disagrees with its declared spec."""

    physical: str
    table_class: str
    declared_spec: str  # the resolved spec TEXT alter_to_declared issues verbatim
    declared: tuple[tuple[str, str], ...]
    live: tuple[tuple[str, str], ...]
    legacy_scheme_files: int


def _declared_spec_text(classified: maintenance_scope.ClassifiedTable, semantics: dict[str, Any]) -> str:
    """Resolve the declared partition spec TEXT for one classified physical table.

    build_registry never emits a "current" side for an append_only table (no write-through
    projection to partition), so `classified.side == "history"` is the only way to reach an
    append_only table here -- table_spec.partition_current is only read for a scd2 "current" side,
    where resolve_table_spec always resolves it (Decision 81 cl.7's required 'current' key)."""
    if classified.table_class == "control":
        spec = control_tables.resolve_control_spec(classified.table_id)
        return spec.partition_key
    if classified.table_class == maintenance_scope.SMOKE_HARNESS_CLASS:
        smoke_spec = resolve_table_spec(None, semantics)
        return smoke_spec.partition_history if classified.side == "history" else smoke_spec.partition_current  # type: ignore[return-value]
    if is_event_table(classified.table_id, semantics):
        # An event entry has no ScdTableSpec (no merge key): its declared history spec is the projection's
        # own partition block (the calendar-day triple), classified like any append_only history table.
        return str(semantics["ops_tables"][classified.table_id]["partition"]["history"])
    table_spec = resolve_table_spec(classified.table_id, semantics)
    return table_spec.partition_history if classified.side == "history" else table_spec.partition_current  # type: ignore[return-value]


def read_partition_layout(
    con: Any,
    *,
    catalog_alias: str = CATALOG_ALIAS,
    semantics: dict[str, Any] | None = None,
) -> dict[str, TableLayout]:
    """Read the LIVE partition layout of every catalog table the registry can classify.

    Enumerates every live physical table (ducklake_table, end_snapshot IS NULL) in the attached
    catalog, classifies each via ducklake_maintenance_scope.build_registry / classify_table
    (Decision 191 -- registry, never name patterns). Unclassified tables are COLLECTED, never
    raised on individually; if any remain after the loop, ONE PartitionLayoutError names all of
    them. A DuckLakeMaintenanceScopeError from the registry, or any raw duckdb.Error from the
    metadata reads, is re-raised as PartitionLayoutError with the original message preserved and
    chained (the reader's warm-connection reopen matches dead-session text in str(exc), so a
    text-dropping wrap would turn a Neon scale-to-zero into a typed 500).
    """
    meta_schema = f"__ducklake_metadata_{catalog_alias}"
    try:
        semantics = semantics if semantics is not None else load_field_semantics()
        registry = maintenance_scope.build_registry(semantics)

        physical_names = [
            r[0]
            for r in con.execute(f"SELECT table_name FROM {meta_schema}.ducklake_table WHERE end_snapshot IS NULL").fetchall()
        ]

        layouts: dict[str, TableLayout] = {}
        unclassified: list[str] = []

        for physical in physical_names:
            classified = maintenance_scope.classify_table(physical, registry)
            if classified is None:
                unclassified.append(physical)
                continue

            table_id_row = con.execute(
                f"SELECT table_id FROM {meta_schema}.ducklake_table WHERE table_name = ? AND end_snapshot IS NULL",
                [physical],
            ).fetchone()
            physical_table_id = int(table_id_row[0])

            active_row = con.execute(
                f"SELECT partition_id FROM {meta_schema}.ducklake_partition_info WHERE table_id = ? AND end_snapshot IS NULL",
                [physical_table_id],
            ).fetchone()
            active_scheme_id = int(active_row[0]) if active_row else None

            live_transforms: tuple[tuple[str, str], ...] = ()
            if active_scheme_id is not None:
                col_rows = con.execute(
                    f"SELECT pc.transform, col.column_name FROM {meta_schema}.ducklake_partition_column pc "
                    f"JOIN {meta_schema}.ducklake_column col ON col.column_id = pc.column_id AND col.table_id = pc.table_id "
                    f"WHERE pc.partition_id = ? AND pc.table_id = ? ORDER BY pc.partition_key_index",
                    [active_scheme_id, physical_table_id],
                ).fetchall()
                live_transforms = tuple((r[0], r[1]) for r in col_rows)

            live_files = int(
                con.execute(
                    f"SELECT count(*) FROM {meta_schema}.ducklake_data_file WHERE table_id = ? AND end_snapshot IS NULL",
                    [physical_table_id],
                ).fetchone()[0]
            )

            if active_scheme_id is None:
                legacy_scheme_files = live_files
            else:
                legacy_scheme_files = int(
                    con.execute(
                        f"SELECT count(*) FROM {meta_schema}.ducklake_data_file "
                        f"WHERE table_id = ? AND end_snapshot IS NULL AND partition_id IS DISTINCT FROM ?",
                        [physical_table_id, active_scheme_id],
                    ).fetchone()[0]
                )

            layouts[physical] = TableLayout(
                physical=physical,
                table_class=classified.table_class,
                live_transforms=live_transforms,
                active_scheme_id=active_scheme_id,
                live_files=live_files,
                legacy_scheme_files=legacy_scheme_files,
            )

        if unclassified:
            raise PartitionLayoutError(
                f"unclassified physical tables discovered in catalog metadata: {sorted(unclassified)} -- "
                "every table field_semantics.ops_tables declares must be discoverable by build_registry "
                "(Decision 191); this invocation's catalog carries a table the registry does not cover"
            )

        return layouts
    except PartitionLayoutError:
        raise
    except maintenance_scope.DuckLakeMaintenanceScopeError as exc:
        raise PartitionLayoutError(f"maintenance-scope registry error while reading partition layout: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 -- any raw duckdb.Error (incl. a dead warm connection) wrapped, message preserved
        raise PartitionLayoutError(f"failed to read live partition layout from catalog metadata: {exc}") from exc


def compare_to_declared(
    layouts: dict[str, TableLayout],
    *,
    semantics: dict[str, Any] | None = None,
) -> list[PartitionDrift]:
    """Compare every layout's live transforms to its declared spec; return one PartitionDrift per mismatch.

    Resolves each table's declared spec via resolve_table_spec (scd2/append_only) or
    resolve_control_spec (control), normalizes it to DuckLake's own representation via
    normalize_partition_spec, and compares tuple-for-tuple against TableLayout.live_transforms.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    registry = maintenance_scope.build_registry(semantics)

    drifts: list[PartitionDrift] = []
    for physical, layout in layouts.items():
        classified = registry.get(physical)
        if classified is None:
            raise PartitionLayoutError(f"cannot resolve declared spec for unclassified physical table {physical!r}")
        declared_spec = _declared_spec_text(classified, semantics)
        declared = normalize_partition_spec(declared_spec)
        if declared != layout.live_transforms:
            drifts.append(
                PartitionDrift(
                    physical=physical,
                    table_class=layout.table_class,
                    declared_spec=declared_spec,
                    declared=declared,
                    live=layout.live_transforms,
                    legacy_scheme_files=layout.legacy_scheme_files,
                )
            )
    return drifts
