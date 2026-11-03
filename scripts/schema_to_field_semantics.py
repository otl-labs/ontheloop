"""Generator: project Class A contracts -> config/lambda/ducklake/field_semantics.yaml.

Makes field_semantics.yaml a GENERATED projection of the ratified Class A contracts
(docs/contracts/ops_recommendations.yaml, docs/contracts/ops_decisions.yaml) so the
contract is the single source of truth for the DuckLake runtime field map (T2.33).

Non-contract content (smoke section, dormant tables, operational keys) lives in
config/lambda/ducklake/field_semantics.static.yaml (the sidecar). The generator
merges contract projection + sidecar to emit the whole field_semantics.yaml.

CLI:
  Default (no flags): regenerate and WRITE config/lambda/ducklake/field_semantics.yaml.
  --check:            regenerate in-memory, byte-compare against committed file;
                      exit non-zero on drift. NEVER auto-writes (Decision 55).
  --slice mechanical: emit only the mechanical projection (role/sql_type/nullable, no prose).
  --slice semantic:   emit only description/semantics per field (semantic write-guidance surface).

Migration-columns enforcement: every sidecar migration_columns entry must be a subset of
(and type-consistent with) the table's projected columns. Raises on violation (rec-2232).

Reconcile-pending-gate subset enforcement (hotfix follow-up to the 2026-07-24 ops_decisions
incident): every sidecar contract_table_ops[*].pending_reconcile / reconciled column must be a
subset of the table's declared columns (Class A contract fields for the two contract tables;
the sidecar's own columns: block for the four non-contract tables), and a current-side entry is
rejected for any append_only table (no current projection exists to reconcile). Runs over ALL
SIX contract_table_ops entries, not only the two contract-backed ones -- the emission path never
reads reconcile_scope/pending_reconcile/reconciled, so this is the only place they are validated.

maintenance_policy passthrough (compaction-scope-policy-matrix, Decision 191): the sidecar's
per-class per-verb maintenance policy matrix is copied verbatim into the generated projection --
REQUIRED, not conditional (a missing key raises KeyError), since this is the only path that
carries the matrix into the Lambda-bundled asset.

Fail-closed rules:
  - Unmapped iceberg_type raises ValueError (never silently defaults).
  - contract.governance.merge_key must be present for each contract-backed table.
  - No exceptions raised at module import (AGENTS.md safety rule).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.field_semantics_rule_projection import refuse_contract_overlap, validate_sidecar_rules
from src.common.ducklake_partition_spec import (
    parse_partition_by,
    resolve_partition_block,
    validate_projection_partitions,
)

ROOT = Path(__file__).parent.parent
_CONTRACTS_DIR = ROOT / "docs" / "contracts"
_SIDECAR_PATH = ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml"
_OUTPUT_PATH = ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml"

_GENERATED_HEADER = """\
# GENERATED -- DO NOT EDIT (scripts/schema_to_field_semantics.py)
# Source of truth: docs/contracts/ops_recommendations.yaml, docs/contracts/ops_decisions.yaml
# Non-contract content: config/lambda/ducklake/field_semantics.static.yaml (the sidecar)
# To update: edit the contract or sidecar, then run: bin/venv-python -m scripts.schema_to_field_semantics
# Drift gate: bin/venv-python -m scripts.schema_to_field_semantics --check (non-zero on drift)
"""

_CONTRACT_TABLE_IDS = (
    "ops_recommendations",
    "ops_decisions",
    "ops_entity_counters",
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_transcripts",
    "telemetry_agents",
    "ops_tenants",
    "ops_projects",
)
_DORMANT_TABLE_IDS = ("ops_priority_queue", "ops_execution_plans")
# Append-only smoke tables (T1.14): no Class A contract, no current projection
# (write_mode: append_only -> current_table absent); spliced verbatim from the sidecar.
_SMOKE_TABLE_IDS = ("ops_smoke_events",)

_ICEBERG_TO_SQL: dict[str, str] = {
    "string": "VARCHAR",
    "boolean": "BOOLEAN",
    "long": "BIGINT",
    "int": "INTEGER",
    "bigint": "BIGINT",
    "double": "DOUBLE",
    "timestamptz": "TIMESTAMP WITH TIME ZONE",
    "array<string>": "VARCHAR[]",
    "array<long>": "BIGINT[]",
}


def _map_iceberg_type(iceberg_type: str | None, field_name: str) -> str:
    """Map an iceberg_type string to a DuckLake sql_type string. Raises on unmapped."""
    if iceberg_type is None:
        raise ValueError(f"field {field_name!r}: iceberg_type is None -- cannot project sql_type")
    mapped = _ICEBERG_TO_SQL.get(iceberg_type)
    if mapped is None:
        raise ValueError(
            f"field {field_name!r}: iceberg_type {iceberg_type!r} has no sql_type mapping. "
            f"Add it to _ICEBERG_TO_SQL in scripts/schema_to_field_semantics.py (fail-closed)."
        )
    return mapped


def _project_role(derivation: dict[str, Any] | None) -> str:
    """Derive role from the derivation block: derived iff realized=True; input otherwise."""
    if derivation and derivation.get("realized") is True:
        return "derived"
    return "input"


def _enforce_migration_columns_subset(
    table_id: str,
    migration_columns: dict[str, Any],
    projected_columns: dict[str, Any],
) -> None:
    """Raise if any migration_columns entry is not a subset of projected_columns (rec-2232)."""
    for location in ("history", "current"):
        loc_entries = migration_columns.get(location, {})
        if not isinstance(loc_entries, dict):
            continue
        for col_name, col_spec in loc_entries.items():
            if col_name not in projected_columns:
                raise ValueError(
                    f"{table_id}: migration_columns[{location!r}][{col_name!r}] is not in "
                    f"the projected columns -- not a subset (rec-2232 enforcement)"
                )
            if not isinstance(col_spec, dict):
                continue
            proj = projected_columns[col_name]
            mig_sql = col_spec.get("sql_type")
            proj_sql = proj.get("sql_type")
            if mig_sql != proj_sql:
                raise ValueError(
                    f"{table_id}: migration_columns[{location!r}][{col_name!r}].sql_type "
                    f"mismatch: {mig_sql!r} vs projected {proj_sql!r} (rec-2232 type-consistency)"
                )


def _enforce_reconcile_pending_subset(
    table_id: str,
    ops_entry: dict[str, Any],
    declared_columns: set[str],
    write_mode: str,
) -> None:
    """Raise if a contract_table_ops entry's reconcile-governance keys are malformed.

    Mirrors _enforce_migration_columns_subset (rec-2232): every pending_reconcile/reconciled
    column must be a subset of declared_columns. Also enforces reconcile_scope is a recognised
    value, and rejects a current-side entry for an append_only table (its current projection
    does not exist -- ScdTableSpec.current_table=None -- so nothing there could ever reconcile).
    """
    scope = ops_entry.get("reconcile_scope")
    if scope not in ("ducklake", "exempt", "telemetry_catalog"):
        raise ValueError(f"{table_id}: reconcile_scope must be 'ducklake', 'exempt' or 'telemetry_catalog', got {scope!r}")
    if scope == "telemetry_catalog" and not (
        isinstance(ops_entry.get("scope_reason"), str) and ops_entry["scope_reason"].strip()
    ):
        raise ValueError(f"{table_id}: a telemetry_catalog reconcile_scope requires a non-empty scope_reason")

    for block_name in ("pending_reconcile", "reconciled"):
        block = ops_entry.get(block_name)
        if not block:
            continue
        history_cols = list(block.get("history") or [])
        current_cols = list(block.get("current") or [])

        for col_name in history_cols:
            if col_name not in declared_columns:
                raise ValueError(
                    f"{table_id}: {block_name}['history'][{col_name!r}] is not a declared column -- "
                    "not a subset (reconcile-pending-gate subset enforcement)"
                )
        for col_name in current_cols:
            if col_name not in declared_columns:
                raise ValueError(
                    f"{table_id}: {block_name}['current'][{col_name!r}] is not a declared column -- "
                    "not a subset (reconcile-pending-gate subset enforcement)"
                )
        if current_cols and write_mode == "append_only":
            raise ValueError(
                f"{table_id}: {block_name}['current'] names column(s) {current_cols} but this table is "
                "write_mode append_only -- no current projection exists to reconcile (APPEND-ONLY invariant)"
            )


def _project_contract_table(
    table_id: str,
    resolved_fields: dict[str, Any],
    merge_key: str | None,
    ops_config: dict[str, Any],
    *,
    table_class: str = "scd2",
    include_prose: bool = False,
    partition_by: str | None = None,
) -> dict[str, Any]:
    """Project a Class A contract's resolved fields into an ops_tables entry.

    table_class="control" (T2.26) projects the CONTROL shape instead of the SCD2 shape: a single
    physical table (no history_table/current_table pair, no bucket-partition), write_mode:
    control explicit (the SCD2 projection emits no write_mode key at all, so consumers default to
    scd2 -- the control class must be unambiguous), and entity_id_prefix/id_keyspace OMITTED
    entirely rather than defaulted (a control table has no writer-owned entity keyspace).

    table_class="event" (Decision 199, telemetry kernel) dispatches to the history-only
    append_only event projection (scripts/field_semantics_event_projection.py), imported INSIDE
    this branch only (function scope, noqa: PLC0415 -- the src/common/ducklake_writes.py
    mint_write_identity ulid-import precedent) so validate_field_semantics_drift's module-scope
    import of this generator gains no edge into src.telemetry.
    """
    if table_class == "event":
        from scripts.field_semantics_event_projection import project_event_table  # noqa: PLC0415

        return project_event_table(
            table_id,
            resolved_fields,
            ops_config,
            partition_by,
            map_iceberg_type=_map_iceberg_type,
            include_prose=include_prose,
        )

    columns: dict[str, Any] = {}
    derivations: dict[str, Any] = {}
    for fname, fspec in resolved_fields.items():
        derivation = fspec.derivation if hasattr(fspec, "derivation") else fspec.get("derivation")
        role = _project_role(derivation)
        if derivation and derivation.get("prefix_map"):
            derivations[fname] = {
                "inputs": list(derivation["inputs"]),
                "prefix_map": dict(derivation["prefix_map"]),
                "default": derivation["default"],
            }
        sql_type = _map_iceberg_type(
            fspec.iceberg_type if hasattr(fspec, "iceberg_type") else fspec.get("iceberg_type"),
            fname,
        )
        nullable = fspec.nullable if hasattr(fspec, "nullable") else fspec.get("nullable")
        col: dict[str, Any] = {"role": role, "sql_type": sql_type, "nullable": nullable}
        if include_prose:
            desc = fspec.description if hasattr(fspec, "description") else fspec.get("description")
            sem = fspec.semantics if hasattr(fspec, "semantics") else fspec.get("semantics")
            if desc is not None:
                col["description"] = desc
            if sem is not None:
                col["semantics"] = sem
        columns[fname] = col

    migration_columns = ops_config.get("migration_columns")
    if migration_columns:
        _enforce_migration_columns_subset(table_id, migration_columns, columns)

    if table_class == "control":
        entry: dict[str, Any] = {
            "status": ops_config["status"],
            "merge_key": merge_key,
            "write_mode": "control",
            "partition": {"table": merge_key},
            "columns": columns,
        }
        if ops_config.get("entity_id_prefix"):
            entry["entity_id_prefix"] = ops_config["entity_id_prefix"]
        if ops_config.get("id_keyspace"):
            entry["id_keyspace"] = ops_config["id_keyspace"]
        if migration_columns:
            entry["migration_columns"] = migration_columns
        return entry

    history_table = f"{table_id}_history"
    current_table = f"{table_id}_current"
    if not partition_by:
        raise ValueError(f"{table_id}: governance.partition_by is missing -- required for {table_class} tables")
    from scripts.field_semantics_rule_projection import project_row_rules  # noqa: PLC0415
    from src.row_rules.rules import RowRules  # noqa: PLC0415

    columns = project_row_rules(table_id, columns)
    RowRules.from_projection(columns)  # a malformed projection fails the generator, not the Lambda
    history, current = resolve_partition_block(parse_partition_by(partition_by), "scd2")
    partition = {"history": history, "current": current}
    entry = {
        "status": ops_config["status"],
        "merge_key": merge_key,
        "entity_id_prefix": ops_config.get("entity_id_prefix"),
        "id_keyspace": ops_config.get("id_keyspace"),
        "id_scheme": ops_config.get("id_scheme"),
        "write_boundary": ops_config.get("write_boundary"),
        "history_table": history_table,
        "current_table": current_table,
        "partition": partition,
        "columns": columns,
    }
    if derivations:
        entry["derivations"] = derivations
    if migration_columns:
        entry["migration_columns"] = migration_columns
    return entry


def _telemetry_joins() -> list[dict[str, str]]:
    """Every docs/contracts/_joins.yaml join between two telemetry_* tables, child side by the contract's cardinality.

    Module-local on purpose: the deploy's reconcile-gate job runs this generator with pyyaml and pydantic only, so it cannot
    import scripts.telemetry_verify (whose loader reads the same contract independently; a parity test pins the two).
    """
    joins = yaml.safe_load((_CONTRACTS_DIR / "_joins.yaml").read_text(encoding="utf-8"))["joins"]
    out: list[dict[str, str]] = []
    for join_id, spec in joins.items():
        source, target = spec["from"].split(".", 1), spec["to"].split(".", 1)
        if not (source[0].startswith("telemetry_") and target[0].startswith("telemetry_")):
            continue
        child, parent = (target, source) if spec["cardinality"] == "one_to_many" else (source, target)
        out.append(
            {
                "join_id": join_id,
                "child_table": child[0],
                "child_column": child[1],
                "parent_table": parent[0],
                "parent_column": parent[1],
            }
        )
    return sorted(out, key=lambda j: j["join_id"])


def generate(*, include_prose: bool = False) -> dict[str, Any]:
    """Build the full field_semantics document from contracts + sidecar.

    include_prose=True adds description/semantics to each column entry (semantic slice).
    include_prose=False (default) emits only role/sql_type/nullable (mechanical slice).
    """
    from scripts.contracts import load_contract, resolve_refs

    sidecar = yaml.safe_load(_SIDECAR_PATH.read_text(encoding="utf-8"))

    doc: dict[str, Any] = {}
    for key in (
        "tables",
        "fields",
        "derivation_timing",
        "partition_transforms",
        "connection_settings",
        "maintenance_policy",
    ):
        doc[key] = sidecar[key]

    ops_tables: dict[str, Any] = {}
    contract_table_ops: dict[str, Any] = sidecar.get("contract_table_ops", {})

    for table_id in _CONTRACT_TABLE_IDS:
        contract_path = _CONTRACTS_DIR / f"{table_id}.yaml"
        contract_doc = load_contract(contract_path)

        raw_table_class = (contract_doc.governance and contract_doc.governance.table_class) or "scd2"
        table_class = raw_table_class.lower()

        merge_key = contract_doc.governance.merge_key if contract_doc.governance else None
        if table_class != "event" and not merge_key:
            raise ValueError(
                f"{table_id}: governance.merge_key is missing. "
                "Add merge_key to the top-level governance block in the contract before running the generator."
            )

        resolved = resolve_refs(contract_doc, _CONTRACTS_DIR)
        ops_config = contract_table_ops.get(table_id, {})
        partition_by = contract_doc.governance.partition_by if contract_doc.governance else None
        ops_tables[table_id] = _project_contract_table(
            table_id,
            resolved,
            merge_key,
            ops_config,
            table_class=table_class,
            include_prose=include_prose,
            partition_by=partition_by,
        )

    dormant = sidecar.get("dormant_ops_tables", {})
    smoke = sidecar.get("smoke_ops_tables", {})
    refuse_contract_overlap((*_DORMANT_TABLE_IDS, *_SMOKE_TABLE_IDS), _CONTRACTS_DIR)
    validate_sidecar_rules({t: dormant[t] for t in _DORMANT_TABLE_IDS if t in dormant})
    validate_sidecar_rules({t: smoke[t] for t in _SMOKE_TABLE_IDS if t in smoke})
    for table_id in _DORMANT_TABLE_IDS:
        if table_id in dormant:
            ops_tables[table_id] = dormant[table_id]

    for table_id in _SMOKE_TABLE_IDS:
        if table_id in smoke:
            ops_tables[table_id] = smoke[table_id]

    # Standalone subset-validation pass over EVERY contract_table_ops entry (rec-2232 mirror) --
    # not only the two contract-backed tables. Never emitted; raises on violation (Decision 55).
    for table_id, ops_entry in contract_table_ops.items():
        declared_columns = set(ops_tables.get(table_id, {}).get("columns", {}))
        write_mode = ops_tables.get(table_id, {}).get("write_mode", "scd2")
        _enforce_reconcile_pending_subset(table_id, ops_entry, declared_columns, write_mode)

    doc["ops_tables"] = ops_tables
    doc["telemetry_joins"] = _telemetry_joins()
    validate_projection_partitions(doc)
    return doc


def _emit_yaml(doc: dict[str, Any]) -> str:
    """Emit the document as deterministic canonical YAML (header + block style, sorted=False)."""
    body = yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True, width=120)
    return _GENERATED_HEADER + "\n" + body


def generate_mechanical_slice() -> str:
    """Emit only the mechanical projection (role/sql_type/nullable per column, no prose)."""
    doc = generate(include_prose=False)
    return _emit_yaml(doc)


def generate_semantic_slice() -> str:
    """Emit only description/semantics per column (semantic write-guidance surface)."""
    doc = generate(include_prose=True)
    ops = doc.get("ops_tables", {})
    semantic_ops: dict[str, Any] = {}
    for tbl, entry in ops.items():
        cols = entry.get("columns", {})
        semantic_cols: dict[str, Any] = {}
        for col, spec in cols.items():
            semantic_cols[col] = {k: v for k, v in spec.items() if k in ("description", "semantics") and v is not None}
        semantic_ops[tbl] = {"columns": semantic_cols}
    return yaml.dump({"ops_tables": semantic_ops}, default_flow_style=False, sort_keys=False, allow_unicode=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate config/lambda/ducklake/field_semantics.yaml from Class A contracts."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in-memory, byte-compare against committed file; exit non-zero on drift. "
        "Never writes (fail-closed, Decision 55).",
    )
    parser.add_argument(
        "--slice",
        choices=["mechanical", "semantic"],
        default=None,
        help="Emit only the named slice instead of the full document (stdout only).",
    )
    args = parser.parse_args(argv)

    if args.slice == "semantic":
        print(generate_semantic_slice(), end="")
        return 0

    generated = _emit_yaml(generate(include_prose=False))

    if args.slice == "mechanical":
        print(generated, end="")
        return 0

    if args.check:
        try:
            committed = _OUTPUT_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: cannot read committed file {_OUTPUT_PATH}: {exc}", file=sys.stderr)
            return 1
        try:
            rel = _OUTPUT_PATH.relative_to(ROOT)
        except ValueError:
            rel = _OUTPUT_PATH
        if generated == committed:
            print(f"OK: {rel} is up-to-date (no drift).")
            return 0
        print(
            f"DRIFT DETECTED: {rel} differs from the generator output.\n"
            "Run: bin/venv-python -m scripts.schema_to_field_semantics   (to regenerate)\n"
            "Do NOT hand-edit field_semantics.yaml -- it is GENERATED output.",
            file=sys.stderr,
        )
        return 1

    _OUTPUT_PATH.write_text(generated, encoding="utf-8")
    try:
        rel = _OUTPUT_PATH.relative_to(ROOT)
    except ValueError:
        rel = _OUTPUT_PATH
    print(f"Written: {rel}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
