"""Unit tests for scripts/schema_to_field_semantics.py (T2.33).

Hermetic: uses tmp_path fixtures for any file I/O; no network calls; no database.
Covers:
  - iceberg->sql type mapping (all mapped types + unmapped raises)
  - role rule: realized:true -> derived, realized:false -> input, no derivation -> input
  - SCD2 envelope fields (ulid/created/last_updated are derived)
  - effort/priority/risk/automatable remain input (realized:false)
  - merge_key and partition synthesis
  - history/current table name synthesis
  - mechanical slice has no prose (no description/semantics)
  - semantic slice carries description/semantics
  - --check detects injected drift without writing
  - migration_columns subset enforcement raises on non-subset/mistyped entry (rec-2232)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Module import (direct load to avoid sys.path pollution)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
_MODULE_PATH = _ROOT / "scripts" / "schema_to_field_semantics.py"
_spec = importlib.util.spec_from_file_location("schema_to_field_semantics", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_map_iceberg_type = _mod._map_iceberg_type
_project_role = _mod._project_role
_enforce_migration_columns_subset = _mod._enforce_migration_columns_subset
_enforce_reconcile_pending_subset = _mod._enforce_reconcile_pending_subset
_project_contract_table = _mod._project_contract_table
_emit_yaml = _mod._emit_yaml
generate = _mod.generate
generate_mechanical_slice = _mod.generate_mechanical_slice
generate_semantic_slice = _mod.generate_semantic_slice
main = _mod.main


# ---------------------------------------------------------------------------
# Iceberg -> sql_type mapping
# ---------------------------------------------------------------------------
class TestIcebergToSqlMapping:
    @pytest.mark.parametrize(
        ("iceberg_type", "sql_type"),
        [
            ("string", "VARCHAR"),
            ("boolean", "BOOLEAN"),
            ("long", "BIGINT"),
            ("int", "INTEGER"),
            ("bigint", "BIGINT"),
            ("double", "DOUBLE"),
            ("timestamptz", "TIMESTAMP WITH TIME ZONE"),
            ("array<string>", "VARCHAR[]"),
            ("array<long>", "BIGINT[]"),
        ],
    )
    def test_mapped_types(self, iceberg_type: str, sql_type: str) -> None:
        assert _map_iceberg_type(iceberg_type, "f") == sql_type

    def test_unmapped_raises(self) -> None:
        with pytest.raises(ValueError, match="no sql_type mapping"):
            _map_iceberg_type("uuid", "bad_field")

    def test_none_iceberg_type_raises(self) -> None:
        with pytest.raises(ValueError, match="iceberg_type is None"):
            _map_iceberg_type(None, "bad_field")


# ---------------------------------------------------------------------------
# Role derivation rule
# ---------------------------------------------------------------------------
class TestProjectRole:
    def test_no_derivation_is_input(self) -> None:
        assert _project_role(None) == "input"

    def test_empty_derivation_is_input(self) -> None:
        assert _project_role({}) == "input"

    def test_realized_true_is_derived(self) -> None:
        assert _project_role({"realized": True}) == "derived"

    def test_realized_false_is_input(self) -> None:
        assert _project_role({"realized": False}) == "input"

    def test_realized_missing_is_input(self) -> None:
        assert _project_role({"formula": "some formula"}) == "input"

    def test_derived_requires_exact_true(self) -> None:
        assert _project_role({"realized": 1}) == "input"  # 1 != True (strict check)
        assert _project_role({"realized": "true"}) == "input"  # string "true" is not True


# ---------------------------------------------------------------------------
# Migration columns subset enforcement
# ---------------------------------------------------------------------------
class TestMigrationColumnsSubset:
    def _projected(self) -> dict[str, Any]:
        return {
            "id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
            "title": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
        }

    def test_valid_subset_passes(self) -> None:
        _enforce_migration_columns_subset(
            "t",
            {"history": {"title": {"role": "input", "sql_type": "VARCHAR", "nullable": True}}},
            self._projected(),
        )

    def test_unknown_column_raises(self) -> None:
        with pytest.raises(ValueError, match="not in the projected columns"):
            _enforce_migration_columns_subset(
                "t",
                {"history": {"__bogus__": {"role": "input", "sql_type": "VARCHAR", "nullable": True}}},
                self._projected(),
            )

    def test_sql_type_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="sql_type mismatch"):
            _enforce_migration_columns_subset(
                "t",
                {"history": {"id": {"role": "input", "sql_type": "BIGINT", "nullable": False}}},
                self._projected(),
            )

    def test_both_history_and_current_checked(self) -> None:
        # current entry is valid, history entry is not -- should still raise
        with pytest.raises(ValueError, match="not in the projected columns"):
            _enforce_migration_columns_subset(
                "t",
                {
                    "history": {"__bad__": {"role": "input", "sql_type": "VARCHAR", "nullable": True}},
                    "current": {"title": {"role": "input", "sql_type": "VARCHAR", "nullable": True}},
                },
                self._projected(),
            )

    def test_non_dict_location_entry_is_skipped(self) -> None:
        # If the value under "history" or "current" is not a dict, continue (line 92)
        _enforce_migration_columns_subset(
            "t",
            {"history": ["not", "a", "dict"]},
            self._projected(),
        )

    def test_non_dict_col_spec_is_skipped(self) -> None:
        # If col_spec is not a dict, skip type-consistency check (line 100)
        _enforce_migration_columns_subset(
            "t",
            {"history": {"id": None}},
            self._projected(),
        )


# ---------------------------------------------------------------------------
# Reconcile-pending-gate subset enforcement (hotfix follow-up to the 2026-07-24
# ops_decisions incident) -- mirrors _enforce_migration_columns_subset (rec-2232),
# but runs over ALL SIX contract_table_ops entries, not only the two contract tables.
# ---------------------------------------------------------------------------
class TestReconcileScopeSubset:
    def _declared(self) -> set[str]:
        return {"id", "intent"}

    def test_valid_scope_and_declared_columns_passes(self) -> None:
        _enforce_reconcile_pending_subset(
            "ops_decisions",
            {
                "reconcile_scope": "ducklake",
                "pending_reconcile": {"history": ["intent"], "current": ["intent"]},
                "reconciled": {"history": {}, "current": {}},
            },
            self._declared(),
            "scd2",
        )

    def test_invalid_reconcile_scope_raises(self) -> None:
        entry = {"pending_reconcile": {}, "reconciled": {}}
        with pytest.raises(ValueError, match="telemetry_catalog"):
            _enforce_reconcile_pending_subset("t", {**entry, "reconcile_scope": "bogus"}, self._declared(), "scd2")
        with pytest.raises(ValueError, match="scope_reason"):
            _enforce_reconcile_pending_subset("t", {**entry, "reconcile_scope": "telemetry_catalog"}, self._declared(), "scd2")

    def test_undeclared_column_under_pending_reconcile_raises(self) -> None:
        with pytest.raises(ValueError, match="not a declared column"):
            _enforce_reconcile_pending_subset(
                "ops_decisions",
                {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": ["typo"], "current": []},
                    "reconciled": {"history": {}, "current": {}},
                },
                self._declared(),
                "scd2",
            )

    def test_undeclared_column_under_reconciled_raises(self) -> None:
        with pytest.raises(ValueError, match="not a declared column"):
            _enforce_reconcile_pending_subset(
                "ops_decisions",
                {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                    "reconciled": {"history": {"typo": {"at": "x"}}, "current": {}},
                },
                self._declared(),
                "scd2",
            )

    def test_undeclared_column_under_current_side_raises(self) -> None:
        with pytest.raises(ValueError, match="not a declared column"):
            _enforce_reconcile_pending_subset(
                "ops_decisions",
                {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": ["typo"]},
                    "reconciled": {"history": {}, "current": {}},
                },
                self._declared(),
                "scd2",
            )

    def test_current_side_entry_on_append_only_table_raises(self) -> None:
        with pytest.raises(ValueError, match="append_only"):
            _enforce_reconcile_pending_subset(
                "ops_smoke_events",
                {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": ["event_type"]},
                    "reconciled": {"history": {}, "current": {}},
                },
                {"event_type"},
                "append_only",
            )

    def test_current_side_reconciled_entry_on_append_only_table_raises(self) -> None:
        with pytest.raises(ValueError, match="append_only"):
            _enforce_reconcile_pending_subset(
                "ops_smoke_events",
                {
                    "reconcile_scope": "ducklake",
                    "pending_reconcile": {"history": [], "current": []},
                    "reconciled": {"history": {}, "current": {"event_type": {"at": "x"}}},
                },
                {"event_type"},
                "append_only",
            )

    def test_history_only_on_append_only_table_passes(self) -> None:
        _enforce_reconcile_pending_subset(
            "ops_smoke_events",
            {
                "reconcile_scope": "ducklake",
                "pending_reconcile": {"history": ["event_type"], "current": []},
                "reconciled": {"history": {}, "current": {}},
            },
            {"event_type"},
            "append_only",
        )

    def test_empty_blocks_are_skipped(self) -> None:
        _enforce_reconcile_pending_subset(
            "ops_decisions",
            {"reconcile_scope": "ducklake", "pending_reconcile": {}, "reconciled": {}},
            self._declared(),
            "scd2",
        )


class TestReconcileScopeGeneratorIntegration:
    """generate() runs the subset-validation pass over ALL SIX contract_table_ops entries."""

    def test_real_sidecar_passes_generate(self) -> None:
        doc = generate()
        assert "ops_tables" in doc  # would have raised during generate() if any entry were invalid

    def test_generate_raises_on_undeclared_pending_reconcile_column(self, tmp_path: Path) -> None:
        sidecar_path = _ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml"
        sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
        sidecar["contract_table_ops"]["ops_decisions"]["pending_reconcile"]["history"].append("__bogus_column__")
        tmp_sidecar = tmp_path / "field_semantics.static.yaml"
        tmp_sidecar.write_text(yaml.dump(sidecar), encoding="utf-8")

        with patch.object(_mod, "_SIDECAR_PATH", tmp_sidecar):
            with pytest.raises(ValueError, match="not a declared column"):
                generate()

    def test_generate_raises_on_current_side_entry_for_append_only_table(self, tmp_path: Path) -> None:
        sidecar_path = _ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml"
        sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
        sidecar["contract_table_ops"]["ops_smoke_events"]["pending_reconcile"]["current"] = ["event_type"]
        tmp_sidecar = tmp_path / "field_semantics.static.yaml"
        tmp_sidecar.write_text(yaml.dump(sidecar), encoding="utf-8")

        with patch.object(_mod, "_SIDECAR_PATH", tmp_sidecar):
            with pytest.raises(ValueError, match="append_only"):
                generate()


# ---------------------------------------------------------------------------
# Integration: generate() against the real contracts + sidecar
# ---------------------------------------------------------------------------
class TestGenerateIntegration:
    """These tests call generate() which reads real files from the repo."""

    def test_ops_recommendations_columns_present(self) -> None:
        doc = generate()
        cols = doc["ops_tables"]["ops_recommendations"]["columns"]
        assert "ulid" in cols
        assert "id" in cols
        assert "_contract_version" in cols
        assert len(cols) == 26

    def test_ops_decisions_columns_present(self) -> None:
        doc = generate()
        cols = doc["ops_tables"]["ops_decisions"]["columns"]
        assert "ulid" in cols
        assert "id" in cols
        assert "_contract_version" in cols
        assert "intent" in cols
        assert len(cols) == 19

    def test_envelope_fields_are_derived(self) -> None:
        doc = generate()
        for tbl in ("ops_recommendations", "ops_decisions"):
            cols = doc["ops_tables"][tbl]["columns"]
            assert cols["ulid"]["role"] == "derived", f"{tbl}.ulid should be derived"
            assert cols["created_timestamp"]["role"] == "derived", f"{tbl}.created_timestamp should be derived"
            assert cols["last_updated_timestamp"]["role"] == "derived", f"{tbl}.last_updated_timestamp should be derived"

    def test_deferred_derivation_fields_are_input(self) -> None:
        doc = generate()
        cols = doc["ops_tables"]["ops_recommendations"]["columns"]
        for fname in ("effort", "priority", "automatable", "risk"):
            assert cols[fname]["role"] == "input", f"{fname} should be input (realized:false)"

    def test_merge_key_projected(self) -> None:
        doc = generate()
        assert doc["ops_tables"]["ops_recommendations"]["merge_key"] == "id"
        assert doc["ops_tables"]["ops_decisions"]["merge_key"] == "id"

    def test_project_contract_table_raises_on_missing_partition_by(self) -> None:
        resolved_fields = {
            "id": {"derivation": None, "iceberg_type": "string", "nullable": False},
        }
        with pytest.raises(ValueError, match="governance.partition_by is missing"):
            _project_contract_table(
                "ops_widgets",
                resolved_fields,
                "id",
                {"status": "live"},
                table_class="scd2",
                partition_by=None,
            )

    def test_partition_synthesized(self) -> None:
        doc = generate()
        rec_part = doc["ops_tables"]["ops_recommendations"]["partition"]
        assert rec_part["history"] == "year(created_timestamp), month(created_timestamp), day(created_timestamp)"
        assert rec_part["current"] == "bucket(8, id)"

    def test_table_names_synthesized(self) -> None:
        doc = generate()
        rec = doc["ops_tables"]["ops_recommendations"]
        assert rec["history_table"] == "ops_recommendations_history"
        assert rec["current_table"] == "ops_recommendations_current"
        dec = doc["ops_tables"]["ops_decisions"]
        assert dec["history_table"] == "ops_decisions_history"
        assert dec["current_table"] == "ops_decisions_current"

    def test_dormant_tables_present(self) -> None:
        doc = generate()
        for tbl in ("ops_priority_queue",):
            assert tbl in doc["ops_tables"], f"{tbl} should be in ops_tables"
            assert doc["ops_tables"][tbl]["status"] == "dormant"

    def test_execution_plans_live(self) -> None:
        """ops_execution_plans migrated at T2.26 (c9): present, but no longer dormant."""
        doc = generate()
        assert "ops_execution_plans" in doc["ops_tables"], "ops_execution_plans should be in ops_tables"
        assert doc["ops_tables"]["ops_execution_plans"]["status"] == "live"

    def test_smoke_section_present(self) -> None:
        doc = generate()
        for key in ("tables", "fields", "derivation_timing", "partition_transforms", "connection_settings"):
            assert key in doc, f"smoke section key {key!r} missing"

    def test_smoke_ops_tables_spliced_append_only(self) -> None:
        """ops_smoke_events is spliced verbatim from smoke_ops_tables: append_only, no current_table (T1.14)."""
        doc = generate()
        assert "ops_smoke_events" in doc["ops_tables"], "ops_smoke_events should be spliced into ops_tables"
        entry = doc["ops_tables"]["ops_smoke_events"]
        assert entry["write_mode"] == "append_only"
        assert entry["status"] == "smoke"
        assert entry["merge_key"] == "event_id"
        assert entry["history_table"] == "ops_smoke_events_history"
        assert "current_table" not in entry, "append_only tables must not carry a current projection"
        assert "current" not in entry["partition"], "append_only tables have history-only partitioning"

    def test_mechanical_slice_has_no_prose(self) -> None:
        doc = generate(include_prose=False)
        for tbl_entry in doc["ops_tables"].values():
            for col_spec in tbl_entry.get("columns", {}).values():
                assert "description" not in col_spec, "mechanical slice must not contain description"
                assert "semantics" not in col_spec, "mechanical slice must not contain semantics"

    def test_semantic_slice_has_prose(self) -> None:
        """--slice semantic output must contain description/semantics for non-dormant tables."""
        doc = generate(include_prose=True)
        rec_cols = doc["ops_tables"]["ops_recommendations"]["columns"]
        # id field has description and semantics in the contract
        assert "description" in rec_cols["id"] or "semantics" in rec_cols["id"], (
            "semantic slice should expose description or semantics for ops_recommendations.id"
        )

    def test_sql_types_from_mapping(self) -> None:
        doc = generate()
        cols = doc["ops_tables"]["ops_recommendations"]["columns"]
        assert cols["automatable"]["sql_type"] == "BOOLEAN"
        assert cols["execution_steps"]["sql_type"] == "BIGINT"
        assert cols["created_timestamp"]["sql_type"] == "TIMESTAMP WITH TIME ZONE"
        assert cols["dependencies"]["sql_type"] == "VARCHAR[]"


# ---------------------------------------------------------------------------
# maintenance_policy passthrough (compaction-scope-policy-matrix, Decision 191)
# ---------------------------------------------------------------------------
def test_maintenance_policy_passthrough() -> None:
    """The sidecar's maintenance_policy matrix is carried VERBATIM into the generated projection
    -- the generator's key whitelist previously dropped any unknown sidecar key silently, so the
    matrix never reached the Lambda-bundled asset while --check still reported no drift."""
    doc = generate()
    assert "maintenance_policy" in doc, "maintenance_policy missing from the generated projection"
    sidecar = yaml.safe_load(
        (_ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml").read_text(encoding="utf-8")
    )
    assert doc["maintenance_policy"] == sidecar["maintenance_policy"]
    for cls in ("scd2", "append_only", "control"):
        assert cls in doc["maintenance_policy"], f"class {cls!r} missing from generated maintenance_policy"
        assert "merge_ops" in doc["maintenance_policy"][cls]


# test_maintenance_policy_absent_from_sidecar_raises moved to
# tests/test_field_semantics_event_projection.py (Decision 128 decompose-by-default, alongside
# TestGenerateMissingMergeKey -- this file was over its 500-SLOC budget; neither test carries a
# VP-step node_id reference).


# ---------------------------------------------------------------------------
# Control-class projection (T2.26 control-table-class-and-counter-conformance)
# ---------------------------------------------------------------------------
def test_control_class_projection() -> None:
    """A control contract (ops_entity_counters) projects without SCD2 assumptions: a single
    table, partition on the key column, write_mode: control explicit (the SCD2 projection emits
    no write_mode key at all, so consumers default to scd2), and no entity_id_prefix / id_keyspace."""
    doc = generate()
    entry = doc["ops_tables"]["ops_entity_counters"]
    assert entry["write_mode"] == "control"
    assert entry["status"] == "live"
    assert entry["merge_key"] == "counter_name"
    assert entry["partition"] == {"table": "counter_name"}
    assert "history_table" not in entry
    assert "current_table" not in entry
    assert "entity_id_prefix" not in entry
    assert "id_keyspace" not in entry
    cols = entry["columns"]
    assert set(cols) == {"counter_name", "current_value"}
    assert cols["counter_name"]["sql_type"] == "VARCHAR"
    assert cols["counter_name"]["nullable"] is False
    assert cols["current_value"]["sql_type"] == "BIGINT"
    assert cols["current_value"]["nullable"] is False


def test_control_class_projection_carries_optional_fields_when_present() -> None:
    """_project_contract_table's control-class branch is written generally (entity_id_prefix /
    id_keyspace / migration_columns are OMITTED, never defaulted, per the docstring) -- exercise
    the carry-if-present branches directly, even though no live control contract sets them today."""
    resolved_fields = {
        "counter_name": {"derivation": None, "iceberg_type": "string", "nullable": False},
    }
    migration_columns = {"history": {"counter_name": {"role": "input", "sql_type": "VARCHAR", "nullable": False}}}
    entry = _project_contract_table(
        "ops_entity_counters",
        resolved_fields,
        "counter_name",
        {
            "status": "live",
            "entity_id_prefix": "cnt-",
            "id_keyspace": "writer",
            "migration_columns": migration_columns,
        },
        table_class="control",
    )
    assert entry["entity_id_prefix"] == "cnt-"
    assert entry["id_keyspace"] == "writer"
    assert entry["migration_columns"] == migration_columns


# ---------------------------------------------------------------------------
# --check mode: detects drift without writing
# ---------------------------------------------------------------------------
class TestCheckMode:
    def test_check_passes_when_up_to_date(self, tmp_path: Path) -> None:
        generated = _emit_yaml(generate(include_prose=False))
        output = tmp_path / "field_semantics.yaml"
        output.write_text(generated, encoding="utf-8")

        with patch.object(_mod, "_OUTPUT_PATH", output):
            rc = main(["--check"])
        assert rc == 0

    def test_check_detects_drift_without_writing(self, tmp_path: Path) -> None:
        generated = _emit_yaml(generate(include_prose=False))
        drifted = generated + "\n# injected drift\n"
        output = tmp_path / "field_semantics.yaml"
        output.write_text(drifted, encoding="utf-8")

        with patch.object(_mod, "_OUTPUT_PATH", output):
            rc = main(["--check"])
        assert rc != 0
        # Verify the file was NOT auto-overwritten
        assert output.read_text(encoding="utf-8") == drifted, (
            "--check must not auto-write; file should still contain the injected drift"
        )

    def test_check_missing_file_fails(self, tmp_path: Path) -> None:
        output = tmp_path / "nonexistent.yaml"
        with patch.object(_mod, "_OUTPUT_PATH", output):
            rc = main(["--check"])
        assert rc != 0


# ---------------------------------------------------------------------------
# write mode
# ---------------------------------------------------------------------------
class TestWriteMode:
    def test_write_mode_writes_file(self, tmp_path: Path) -> None:
        output = tmp_path / "fs_out.yaml"
        with patch.object(_mod, "_OUTPUT_PATH", output):
            rc = main([])
        assert rc == 0
        assert output.exists()
        data = yaml.safe_load(output.read_text(encoding="utf-8"))
        assert "ops_tables" in data

    def test_write_mode_idempotent(self, tmp_path: Path) -> None:
        output = tmp_path / "fs_out.yaml"
        with patch.object(_mod, "_OUTPUT_PATH", output):
            main([])
            first_content = output.read_text(encoding="utf-8")
            main([])
            second_content = output.read_text(encoding="utf-8")
        assert first_content == second_content, "Generator must be byte-stable (idempotent)"


# ---------------------------------------------------------------------------
# Slice wrappers and main() --slice branches
# ---------------------------------------------------------------------------
class TestSlices:
    def test_generate_mechanical_slice_is_yaml_str(self) -> None:
        result = generate_mechanical_slice()
        assert isinstance(result, str)
        data = yaml.safe_load(result)
        assert "ops_tables" in data
        # mechanical slice is routed through _emit_yaml, so header is present
        assert "GENERATED" in result

    def test_generate_semantic_slice_returns_only_prose(self) -> None:
        result = generate_semantic_slice()
        assert isinstance(result, str)
        data = yaml.safe_load(result)
        assert "ops_tables" in data
        # Each column entry should only carry description/semantics (or be empty)
        for tbl_entry in data["ops_tables"].values():
            for col, spec in tbl_entry.get("columns", {}).items():
                assert set(spec.keys()) <= {"description", "semantics"}, (
                    f"{col} has unexpected keys in semantic slice: {set(spec.keys())}"
                )

    def test_main_slice_mechanical_stdout(self, capsys: pytest.CaptureFixture) -> None:
        rc = main(["--slice", "mechanical"])
        assert rc == 0
        out = capsys.readouterr().out
        data = yaml.safe_load(out)
        assert "ops_tables" in data

    def test_main_slice_semantic_stdout(self, capsys: pytest.CaptureFixture) -> None:
        rc = main(["--slice", "semantic"])
        assert rc == 0
        out = capsys.readouterr().out
        data = yaml.safe_load(out)
        assert "ops_tables" in data


# The non-event-class merge_key-missing branch stays covered here (per-file coverage runs only this
# file's own mapped tests, Decision 131); TestGenerateMissingMergeKey (the mocked-generic-doc version,
# no table_class set) moved to tests/test_field_semantics_event_projection.py (Decision 128
# decompose-by-default -- this file was over its 500-SLOC budget).
def test_non_event_class_missing_merge_key_raises() -> None:
    mock_doc = MagicMock()
    mock_doc.governance = MagicMock()
    mock_doc.governance.merge_key = None
    mock_doc.governance.table_class = "scd2"
    with patch("scripts.contracts.load_contract", return_value=mock_doc):
        with pytest.raises(ValueError, match="governance.merge_key is missing"):
            generate()


# ---------------------------------------------------------------------------
# table_class: event dispatch (Decision 199, telemetry kernel). Module-level (not inside a
# Test* class): the graduated VP step 8 node_id cites this exact bare form.
# ---------------------------------------------------------------------------
def test_event_class_dispatches_to_event_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    # tests/fixtures/event_contracts/ is the shared minimal event fixture (also used by
    # tests/test_field_semantics_event_projection.py's own mapped-file tests).
    import scripts.field_semantics_event_projection as proj_mod
    from scripts.contracts import load_contract, resolve_refs
    from src.telemetry.identity import KEY_PLANS, KeyPlan

    fixtures_dir = _ROOT / "tests" / "fixtures" / "event_contracts"
    doc = load_contract(fixtures_dir / "fixture_events.yaml")
    resolved = resolve_refs(doc, fixtures_dir)
    assert doc.governance.merge_key is None

    fixture_plan = {"entity_id": KeyPlan("fixture_events:entity_id", "entity_ref", True)}
    monkeypatch.setattr(proj_mod, "KEY_PLANS", {**KEY_PLANS, "fixture_events": fixture_plan})

    entry = _project_contract_table(
        "fixture_events", resolved, None, {}, table_class="event", partition_by=doc.governance.partition_by
    )
    assert entry["write_mode"] == "append_only"
    assert entry["history_table"] == "fixture_events"
    assert "merge_key" not in entry
    assert entry["columns"]["retry_count"]["sql_type"] == "BIGINT"
