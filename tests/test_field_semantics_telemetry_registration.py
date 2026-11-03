"""Behaviour tests for the telemetry table registration (slice 2a-2 plan 1): the four event tables and the
two SCD2 dimensions in the generated registry. Kept out of tests/test_schema_to_field_semantics.py (499 SLOC).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import scripts.field_semantics_event_projection as proj_mod
from scripts.contracts import load_contract, resolve_refs
from scripts.schema_to_field_semantics import _map_iceberg_type
from src.common import ducklake_scd2_schema as schema
from src.telemetry.append import EventTableSpec
from src.telemetry.identity import KeyPlan

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parent.parent
_EVENT_TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")
_DIMENSIONS = ("ops_tenants", "ops_projects")
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "event_contracts"


@pytest.fixture(scope="module")
def registry() -> dict:
    return yaml.safe_load((_ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml").read_text(encoding="utf-8"))


def test_six_tables_registered(registry) -> None:
    tables = registry["ops_tables"]
    for table in _EVENT_TABLES:
        entry = tables[table]
        assert entry["write_mode"] == "append_only" and entry["history_table"] == table
        assert entry["write_boundary"] == "telemetry_append" and entry["status"] == "pre_production"
        assert entry["table_class"] == "event" and "merge_key" not in entry and "current_table" not in entry
    for table in _DIMENSIONS:
        entry = tables[table]
        assert entry["id_keyspace"] == "writer" and entry["id_scheme"] == "ulid"
        assert not entry.get("entity_id_prefix")
        assert entry["write_boundary"] == "registration" and entry["status"] == "pre_production"
        assert entry["current_table"] == f"{table}_current" and entry["history_table"] == f"{table}_history"
    statuses = {entry.get("status") for entry in tables.values()}
    assert "smoke" in statuses  # reserved for kernel-proof tables ...
    assert {t for t, e in tables.items() if e.get("status") == "smoke"} == {"ops_smoke_events"}  # ... and only those


def test_event_entries_build_kernel_specs(registry) -> None:
    for table in _EVENT_TABLES:
        spec = EventTableSpec.from_projection(table, registry["ops_tables"][table])
        assert spec.table == table


def _fixture_projection(partition_column: str, ops_config: dict) -> dict:
    doc = load_contract(_FIXTURES_DIR / "fixture_events.yaml")
    resolved = resolve_refs(doc, _FIXTURES_DIR)
    key_plans = {
        **proj_mod.KEY_PLANS,
        "fixture_events": {"entity_id": KeyPlan("fixture_events:entity_id", "entity_ref", True)},
    }
    partition_by = f"history=year({partition_column}), month({partition_column}), day({partition_column})"
    with patch.object(proj_mod, "_CONTRACTS_DIR", _FIXTURES_DIR), patch.object(proj_mod, "KEY_PLANS", key_plans):
        return proj_mod.project_event_table(
            "fixture_events", resolved, ops_config, partition_by, map_iceberg_type=_map_iceberg_type
        )


def test_partition_column_derived_from_contract() -> None:
    """The projection's partition_column is the validated partition_by's column, not a literal, and the
    entry carries table_class: event and the sidecar's write_boundary."""
    entry = _fixture_projection("event_timestamp", {"write_boundary": "telemetry_append"})
    assert entry["partition_column"] == "event_timestamp"
    assert entry["partition"]["history"] == "year(event_timestamp), month(event_timestamp), day(event_timestamp)"
    assert entry["table_class"] == "event" and entry["write_boundary"] == "telemetry_append"
    assert _fixture_projection("session_started_at", {})["partition_column"] == "session_started_at"


def test_registry_says_what_not_where(registry) -> None:
    """Registration says what a table is, never which catalog holds it."""
    forbidden = {"catalog", "meta_schema", "data_path", "catalog_alias", "s3_prefix"}
    for table in (*_EVENT_TABLES, *_DIMENSIONS):
        assert not forbidden & set(registry["ops_tables"][table]), table


def test_dimension_specs_carry_every_physical_column(registry) -> None:
    contracts_dir = _ROOT / "docs" / "contracts"
    for table in _DIMENSIONS:
        doc = load_contract(contracts_dir / f"{table}.yaml")
        resolved = resolve_refs(doc, contracts_dir)
        physical = {n for n, f in resolved.items() if not (f.derivation or {}).get("timing") == "read"}
        spec = schema.resolve_table_spec(table, registry)
        assert {c for c, _ in spec.ordered_columns} == physical, table
    projects = registry["ops_tables"]["ops_projects"]["columns"]
    assert projects["purpose"]["role"] == "derived"  # writer-computed: physical, but refused from a caller
    assert projects["tenant_id"]["role"] == "input" and projects["project_id"]["role"] == "input"
    assert "purpose" in {c for c, _ in schema.resolve_table_spec("ops_projects", registry).ordered_columns}
    with pytest.raises(schema.SchemaGateError, match="derived"):
        schema.schema_gate({"project_id": "x", "purpose": "drill"}, registry, table="ops_projects")


def test_ops_projects_registry_carries_the_purpose_prefix_map(registry) -> None:
    """The projected prefix_map is the contract's derivation block, the single source (Decision 210 cl.3)."""
    contracts_dir = _ROOT / "docs" / "contracts"
    resolved = resolve_refs(load_contract(contracts_dir / "ops_projects.yaml"), contracts_dir)
    block = resolved["purpose"].derivation
    projected = registry["ops_tables"]["ops_projects"]["derivations"]["purpose"]
    assert projected == {"inputs": block["inputs"], "prefix_map": block["prefix_map"], "default": block["default"]}
    others = [t for t, e in registry["ops_tables"].items() if t != "ops_projects" and "derivations" in e]
    assert others == []
    assert all(
        "derivation" not in c and "derivations" not in c for c in registry["ops_tables"]["ops_projects"]["columns"].values()
    )
