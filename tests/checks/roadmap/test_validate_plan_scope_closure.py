"""Tests for validate_plan_scope_closure() -- plan-obligation-closure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks import _common, registry
from scripts.checks.roadmap.validate_plan_scope_closure import validate_plan_scope_closure
from scripts.roadmap import plan_obligations
from tests.fixtures import plan_lint_fixtures as fx


def _base_scope() -> dict:
    return {
        "schema_version": 4,
        "plan_type": "IMPLEMENTATION",
        "slug": "fixture-probe",
        "plan_path": "docs/plans/PLAN-fixture-probe.yaml",
        "scope": [
            {"file": "scripts/checks/roadmap/validate_x.py", "action": "Create", "purpose": "p"},
            {"file": "scripts/checks/roadmap/_manifest.py", "action": "Modify", "purpose": "p"},
            {"file": "config/ci_rca_taxonomy.yaml", "action": "Modify", "purpose": "p"},
            {"file": "tests/checks/roadmap/test_validate_x.py", "action": "Create", "purpose": "p"},
        ],
    }


def _write(tmp_path: Path, data: dict, name: str = "PLAN-fixture-probe.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


class TestClosure:
    def test_registered_and_sequenced_in_both_tiers(self) -> None:
        registry.resolve("validate_plan_scope_closure")
        pre = {step.name for step in registry.pre_sequence()}
        full = {step.name for step in registry.full_sequence()}
        assert "validate_plan_scope_closure" in pre
        assert "validate_plan_scope_closure" in full

    def test_complete_plan_passes(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _base_scope())
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[path])
        assert failed == []

    def test_names_each_omission_by_path_in_failed(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "scripts/checks/roadmap/_manifest.py"]
        path = _write(tmp_path, data)
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[path])
        assert failed and any("scripts/checks/roadmap/_manifest.py" in entry for entry in failed)

    def test_grandfathers_sub_v4_plan(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["schema_version"] = 3
        data["scope"] = [{"file": "scripts/checks/roadmap/validate_y.py", "action": "Create", "purpose": "p"}]
        path = _write(tmp_path, data)
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[path])
        assert failed == []

    def test_explicit_empty_plan_paths_never_derives_from_diff(self) -> None:
        with patch.object(plan_obligations, "net_new_v4_implementation_plan_paths") as mock_derive:
            failed: list[str] = []
            validate_plan_scope_closure(failed, plan_paths=[])
            assert failed == []
            mock_derive.assert_not_called()

    def test_none_plan_paths_derives_from_diff(self) -> None:
        with patch.object(plan_obligations, "net_new_v4_implementation_plan_paths", return_value=[]) as mock_derive:
            failed: list[str] = []
            validate_plan_scope_closure(failed, plan_paths=None)
            assert failed == []
            mock_derive.assert_called_once()

    def test_absent_file_reported_never_raises(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[tmp_path / "PLAN-absent.yaml"])
        assert failed and "could not read" in failed[0]

    def test_dispatch_recording_attributes_each_finding_to_this_check(self, tmp_path: Path) -> None:
        """scripts.checks.validation_result.dispatch_recording attributes every label a check
        appends to `failed` back to the check's registered name -- exercised directly here since
        this check appends one label per omission, not a single summary string."""
        from scripts.checks import validation_result

        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "config/ci_rca_taxonomy.yaml"]
        path = _write(tmp_path, data)
        failed: list[str] = []
        validation_result._ATTRIBUTIONS.clear()
        validation_result.dispatch_recording(
            "validate_plan_scope_closure", failed, lambda f: validate_plan_scope_closure(f, plan_paths=[path])
        )
        assert failed
        assert all(a["check"] == "validate_plan_scope_closure" for a in validation_result._ATTRIBUTIONS)
        validation_result._ATTRIBUTIONS.clear()


class TestGrammarLeg:
    """The grammar leg runs unconditionally, BEFORE plan discovery -- a contract-only diff (no
    plan file present) still fails on a malformed docs/contracts/plan-obligations.yaml body."""

    def test_malformed_contract_fails_with_no_plans_in_diff(self, tmp_path: Path) -> None:
        bad_contract = tmp_path / "bad-contract.yaml"
        bad_contract.write_text("key: [unterminated", encoding="utf-8")
        with patch.object(plan_obligations, "_CONTRACT_PATH", bad_contract):
            failed: list[str] = []
            validate_plan_scope_closure(failed, plan_paths=[])
        assert failed
        assert any("could not parse" in entry for entry in failed)

    def test_live_contract_never_fails_the_grammar_leg(self) -> None:
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[])
        assert failed == []


class TestAccountingDeclaration:
    """dec-170: every reachable exit of validate_plan_scope_closure declares examined() exactly
    once, never skipped() -- the grammar leg makes the contract always-examined, so there is no
    could-not-examine path to declare skipped() on."""

    def test_no_plans_exit_declares_examined_once(self) -> None:
        with patch.object(registry, "examined") as mock_examined, patch.object(registry, "skipped") as mock_skipped:
            failed: list[str] = []
            validate_plan_scope_closure(failed, plan_paths=[])
        mock_examined.assert_called_once_with(1, unit="artefacts")
        mock_skipped.assert_not_called()

    def test_evaluating_exit_declares_examined_with_full_count(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _base_scope())
        with patch.object(registry, "examined") as mock_examined, patch.object(registry, "skipped") as mock_skipped:
            failed: list[str] = []
            validate_plan_scope_closure(failed, plan_paths=[path])
        mock_examined.assert_called_once_with(2, unit="artefacts")
        mock_skipped.assert_not_called()

    def test_malformed_contract_still_declares_examined_not_skipped(self, tmp_path: Path) -> None:
        bad_contract = tmp_path / "bad-contract.yaml"
        bad_contract.write_text("key: [unterminated", encoding="utf-8")
        with patch.object(plan_obligations, "_CONTRACT_PATH", bad_contract):
            with patch.object(registry, "examined") as mock_examined, patch.object(registry, "skipped") as mock_skipped:
                failed: list[str] = []
                validate_plan_scope_closure(failed, plan_paths=[])
        mock_examined.assert_called_once_with(1, unit="artefacts")
        mock_skipped.assert_not_called()


class TestGatingUnchanged:
    """An enforced_elsewhere entry never reaches `failed` -- the registered check's gating verdict
    is frozen at its pre-change values (Decision 181 CONTENT invariant: never weakened, and never
    silently strengthened into an unpassable gate either). The registration verdict is unchanged; derived gating rows
    add to it (TestDerivedFindingsGate)."""

    def test_enforced_elsewhere_omission_alone_never_fails(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _base_scope())
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[path])
        assert failed == []

    def test_requires_omission_matches_pre_change_label(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "config/ci_rca_taxonomy.yaml"]
        path = _write(tmp_path, data)
        failed: list[str] = []
        validate_plan_scope_closure(failed, plan_paths=[path])
        assert failed == [
            (
                "PLAN-fixture-probe.yaml: missing ci_rca_taxonomy function_to_category row "
                "(config/ci_rca_taxonomy.yaml) -- required because scripts/checks/roadmap/validate_x.py "
                "is a new check module"
            )
        ]


class TestDerivedFindingsGate:
    """Gating derived_findings rows join the registration verdict for a net-new plan; advisory rows never do."""

    def _run(self, tmp_path: Path, plan: dict, gating: dict[str, bool] | None = None) -> list[str]:
        fx.write_contract(tmp_path, gating=gating)
        path = _write(tmp_path, plan)
        failed: list[str] = []
        with patch.object(plan_obligations, "ROOT", tmp_path):
            validate_plan_scope_closure(failed, plan_paths=[path])
        return [f for f in failed if "grammar" not in f]

    def test_gating_derived_finding_fails_the_check(self, tmp_path: Path) -> None:
        fx.write_class_d(tmp_path, "docs/contracts/d.yaml", with_log=False)
        failed = self._run(tmp_path, fx.make_plan(("docs/contracts/d.yaml", "Modify", "adds a field")))
        assert len(failed) == 1 and "amendment_log" in failed[0]

    def test_advisory_only_finding_never_fails(self, tmp_path: Path) -> None:
        fx.write(tmp_path, "docs/contracts/c.yaml", "a: 1\n")
        plan = fx.make_plan(("docs/contracts/c.yaml", "Modify", "p"))
        assert self._run(tmp_path, plan) == []

    def test_a_flipped_row_gates_the_same_finding(self, tmp_path: Path) -> None:
        fx.write_structural_registry(tmp_path, limit=1)
        fx.write(tmp_path, "docs/contracts/c.yaml", "a: 1\n")
        plan = fx.make_plan(("docs/contracts/c.yaml", "Modify", "p"))
        with patch.object(_common, "ROOT", tmp_path):
            assert self._run(tmp_path, plan) == []
            assert len(self._run(tmp_path, plan, gating={"size_headroom": True})) == 1
