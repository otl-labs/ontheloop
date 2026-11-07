"""Tests for validate_data_model_standard() -- PLAN-scd2-modeling-defaults.

Covers: a merge_key-bearing storage-substrate entry missing grain fails, a complete entry
passes, a group entry without merge_key is skipped (no false positive), a missing/malformed
data-modeling-standard.yaml fails, the diff gate skips when neither trigger file changed, and
the check is wired into both presubmit tiers + resolves via scripts.checks.registry (Decision
169, -k wiring). Follows the tmp_path + contracts_dir/changed_files override pattern used by
test_validate_contract_drift.py.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.contracts.validate_data_model_standard import validate_data_model_standard

_COMPLETE_STANDARD = {
    "version": 1,
    "rules": [{"id": "grain-first", "statement": "name the grain first"}],
    "write_modes": {"scd2": {}, "append_only": {}},
    "indexes": [{"target": "docs/contracts/storage-substrate.yaml"}],
    "arrival_expectations": {"kinds": {}, "tables": {}},
}


def _write_yaml(path: Path, data: object) -> None:
    path.write_text(yaml.dump(data), encoding="utf-8")


class TestStorageSubstrateGate:
    def test_merge_key_entry_missing_grain_fails(self, tmp_path: Path) -> None:
        substrate = {"tables": {"ops_priority_queue": {"merge_key": "rec_id", "write_mode": "scd2"}}}
        _write_yaml(tmp_path / "storage-substrate.yaml", substrate)

        failed: list[str] = []
        validate_data_model_standard(failed, contracts_dir=tmp_path, changed_files=["docs/contracts/storage-substrate.yaml"])

        assert any("ops_priority_queue" in f for f in failed)

    def test_complete_entry_passes(self, tmp_path: Path) -> None:
        substrate = {
            "tables": {"ops_priority_queue": {"merge_key": "rec_id", "grain": "one row per rec_id", "write_mode": "scd2"}}
        }
        _write_yaml(tmp_path / "storage-substrate.yaml", substrate)

        failed: list[str] = []
        validate_data_model_standard(failed, contracts_dir=tmp_path, changed_files=["docs/contracts/storage-substrate.yaml"])

        assert failed == []

    def test_group_entry_without_merge_key_skipped(self, tmp_path: Path) -> None:
        substrate = {"tables": {"telemetry_tables": {"note": "group entry, no merge_key"}}}
        _write_yaml(tmp_path / "storage-substrate.yaml", substrate)

        failed: list[str] = []
        validate_data_model_standard(failed, contracts_dir=tmp_path, changed_files=["docs/contracts/storage-substrate.yaml"])

        assert failed == []

    def test_missing_tables_mapping_fails(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "storage-substrate.yaml", {"version": 1})

        failed: list[str] = []
        validate_data_model_standard(failed, contracts_dir=tmp_path, changed_files=["docs/contracts/storage-substrate.yaml"])

        assert any("tables" in f for f in failed)

    def test_read_error_reported(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "storage-substrate.yaml"
        path.write_text("tables: {}", encoding="utf-8")

        def _raise(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _raise)

        failed: list[str] = []
        validate_data_model_standard(failed, contracts_dir=tmp_path, changed_files=["docs/contracts/storage-substrate.yaml"])

        assert any("could not read" in f for f in failed)


class TestStandardContractGate:
    def test_missing_file_fails(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_data_model_standard(
            failed, contracts_dir=tmp_path, changed_files=["docs/contracts/data-modeling-standard.yaml"]
        )

        assert any("not found" in f for f in failed)

    def test_non_mapping_standard_fails(self, tmp_path: Path) -> None:
        (tmp_path / "data-modeling-standard.yaml").write_text("- a\n- b\n", encoding="utf-8")

        failed: list[str] = []
        validate_data_model_standard(
            failed, contracts_dir=tmp_path, changed_files=["docs/contracts/data-modeling-standard.yaml"]
        )

        assert any("not a YAML mapping" in f for f in failed)

    def test_contract_bearing_key_now_passes(self, tmp_path: Path) -> None:
        # INVERTED (migration-step-3-grandfathering): data-modeling-standard.yaml now legitimately
        # carries a Class D `contract:` envelope (its own evaluator is
        # {check: validate_data_model_standard} -- this check self-hosts). The pre-migration
        # rejection, whose text was "must stay non-ritual so the CD.25 drift gate keeps skipping it", is gone;
        # this check now only enforces the required sections, regardless of contract: presence.
        data = dict(_COMPLETE_STANDARD)
        data["contract"] = {"id": "data-modeling-standard", "class": "D", "contract_version": 1, "status": "active"}
        _write_yaml(tmp_path / "data-modeling-standard.yaml", data)

        failed: list[str] = []
        validate_data_model_standard(
            failed, contracts_dir=tmp_path, changed_files=["docs/contracts/data-modeling-standard.yaml"]
        )

        assert failed == [], failed

    def test_missing_section_fails(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "data-modeling-standard.yaml", {"version": 1, "rules": []})

        failed: list[str] = []
        validate_data_model_standard(
            failed, contracts_dir=tmp_path, changed_files=["docs/contracts/data-modeling-standard.yaml"]
        )

        assert any("missing required section" in f for f in failed)

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        (tmp_path / "data-modeling-standard.yaml").write_text("rules: [unterminated", encoding="utf-8")

        failed: list[str] = []
        validate_data_model_standard(
            failed, contracts_dir=tmp_path, changed_files=["docs/contracts/data-modeling-standard.yaml"]
        )

        assert any("could not parse" in f for f in failed)

    def test_complete_standard_passes(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "data-modeling-standard.yaml", _COMPLETE_STANDARD)

        failed: list[str] = []
        validate_data_model_standard(
            failed, contracts_dir=tmp_path, changed_files=["docs/contracts/data-modeling-standard.yaml"]
        )

        assert failed == []


class TestDiffGate:
    def test_neither_file_changed_skips(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_data_model_standard(failed, contracts_dir=tmp_path, changed_files=[])

        assert failed == []

    def test_both_files_changed_runs_both_checks(self, tmp_path: Path) -> None:
        _write_yaml(
            tmp_path / "storage-substrate.yaml",
            {"tables": {"ops_priority_queue": {"merge_key": "rec_id"}}},
        )
        _write_yaml(tmp_path / "data-modeling-standard.yaml", _COMPLETE_STANDARD)

        failed: list[str] = []
        validate_data_model_standard(
            failed,
            contracts_dir=tmp_path,
            changed_files=[
                "docs/contracts/storage-substrate.yaml",
                "docs/contracts/data-modeling-standard.yaml",
            ],
        )

        assert any("ops_priority_queue" in f for f in failed)
        assert len(failed) == 1


class TestWiring:
    def test_wiring_registered_in_both_sequences(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}

        assert "validate_data_model_standard" in pre_names
        assert "validate_data_model_standard" in full_names

    def test_wiring_resolves_via_the_registry(self) -> None:
        resolved = registry.resolve("validate_data_model_standard")

        assert callable(resolved)
        assert resolved is validate_data_model_standard


_SUBSTRATE = "docs/contracts/storage-substrate.yaml"
_STANDARD = "docs/contracts/data-modeling-standard.yaml"


def _declared(contracts_dir: Path, changed_files: list[str]) -> tuple[list[str], registry._Declaration | None]:
    registry.pop_declaration()
    failed: list[str] = []
    validate_data_model_standard(failed, contracts_dir=contracts_dir, changed_files=changed_files)
    return failed, registry.pop_declaration()


class TestExaminedDeclaration:
    """Decision 170: skipped when no trigger file changed, else examined over the entries judged."""

    def test_neither_file_changed_declares_skipped(self, tmp_path: Path) -> None:
        failed, decl = _declared(tmp_path, [])
        assert failed == []
        assert decl is not None
        assert decl.kind == "skipped"
        assert decl.reason == "neither storage-substrate.yaml nor data-modeling-standard.yaml changed"
        assert registry.build_outcome("validate_data_model_standard", "check", decl, appended_to_failed=False).status == (
            "skipped"
        )

    def test_substrate_counts_every_table_entry_judged(self, tmp_path: Path) -> None:
        tables = {
            "a": {"merge_key": "id", "grain": "one row per id", "write_mode": "scd2"},
            "b": {"merge_key": "id", "grain": "one row per id", "write_mode": "append_only"},
            "group": {"note": "group entry, no merge_key"},
        }
        _write_yaml(tmp_path / "storage-substrate.yaml", {"tables": tables})
        failed, decl = _declared(tmp_path, [_SUBSTRATE])
        assert failed == []
        assert decl is not None
        assert (decl.kind, decl.count, decl.unit) == ("examined", 3, "entries")
        assert registry.build_outcome("validate_data_model_standard", "check", decl, appended_to_failed=False).status == (
            "enforced"
        )

    def test_substrate_offender_still_counts_the_judged_tables(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "storage-substrate.yaml", {"tables": {"a": {"merge_key": "id"}, "b": {}}})
        failed, decl = _declared(tmp_path, [_SUBSTRATE])
        assert len(failed) == 1
        assert decl is not None
        assert (decl.kind, decl.count) == ("examined", 2)

    def test_empty_tables_mapping_declares_an_empty_domain(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "storage-substrate.yaml", {"tables": {}})
        failed, decl = _declared(tmp_path, [_SUBSTRATE])
        assert failed == []
        assert decl is not None
        assert (decl.kind, decl.count) == ("examined", 0)
        assert registry.build_outcome("validate_data_model_standard", "check", decl, appended_to_failed=False).status == (
            "vacuous"
        )

    def test_standard_counts_every_required_section(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "data-modeling-standard.yaml", _COMPLETE_STANDARD)
        failed, decl = _declared(tmp_path, [_STANDARD])
        assert failed == []
        assert decl is not None
        assert (decl.kind, decl.count, decl.unit) == ("examined", 4, "entries")

    def test_standard_without_arrival_expectations_fails_as_a_missing_section(self, tmp_path: Path) -> None:
        standard = {k: v for k, v in _COMPLETE_STANDARD.items() if k != "arrival_expectations"}
        _write_yaml(tmp_path / "data-modeling-standard.yaml", standard)
        failed, _ = _declared(tmp_path, [_STANDARD])
        assert len(failed) == 1 and "arrival_expectations" in failed[0]

    def test_missing_section_still_counts_the_judged_sections(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "data-modeling-standard.yaml", {"rules": []})
        failed, decl = _declared(tmp_path, [_STANDARD])
        assert len(failed) == 1
        assert decl is not None
        assert (decl.kind, decl.count) == ("examined", 4)

    def test_both_files_changed_sum_their_judged_entries(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "storage-substrate.yaml", {"tables": {"a": {}, "b": {}}})
        _write_yaml(tmp_path / "data-modeling-standard.yaml", _COMPLETE_STANDARD)
        failed, decl = _declared(tmp_path, [_SUBSTRATE, _STANDARD])
        assert failed == []
        assert decl is not None
        assert (decl.kind, decl.count) == ("examined", 6)

    def test_files_that_never_reach_the_judgment_count_zero(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path / "storage-substrate.yaml", {"version": 1})
        (tmp_path / "data-modeling-standard.yaml").write_text("- a\n", encoding="utf-8")
        failed, decl = _declared(tmp_path, [_SUBSTRATE, _STANDARD])
        assert len(failed) == 2
        assert decl is not None
        assert (decl.kind, decl.count) == ("examined", 0)
        assert registry.build_outcome("validate_data_model_standard", "check", decl, appended_to_failed=True).status == (
            "failed"
        )

    def test_live_contracts_declare_every_live_table_and_section(self) -> None:
        from scripts.checks._common import ROOT

        contracts_dir = ROOT / "docs" / "contracts"
        live = yaml.safe_load((contracts_dir / "storage-substrate.yaml").read_text(encoding="utf-8"))
        failed, decl = _declared(contracts_dir, [_SUBSTRATE, _STANDARD])
        assert failed == []
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == len(live["tables"]) + 4 > 4
