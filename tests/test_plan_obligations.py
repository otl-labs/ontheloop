"""Tests for scripts/roadmap/plan_obligations.py (plan-obligation-closure)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks._schema import Entry
from scripts.roadmap import plan_lint, plan_obligations
from scripts.roadmap.plan_lint import acceptance
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


class TestLoadObligationMap:
    def test_real_contract_loads(self) -> None:
        rules, error = plan_obligations.load_obligation_map()
        assert error is None
        assert rules
        assert any(rule.get("trigger") for rule in rules)

    def test_missing_contract_reports_error_never_raises(self, tmp_path: Path) -> None:
        rules, error = plan_obligations.load_obligation_map(tmp_path / "absent.yaml")
        assert rules == []
        assert error is not None and "could not read" in error

    def test_malformed_yaml_reports_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [unterminated", encoding="utf-8")
        rules, error = plan_obligations.load_obligation_map(bad)
        assert rules == []
        assert error is not None and "could not parse" in error

    def test_non_mapping_top_level_reports_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("- 1\n- 2\n", encoding="utf-8")
        rules, error = plan_obligations.load_obligation_map(bad)
        assert rules == []
        assert error is not None and "not a YAML mapping" in error

    def test_missing_registration_surfaces_key_reports_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("contract:\n  id: x\n", encoding="utf-8")
        rules, error = plan_obligations.load_obligation_map(bad)
        assert rules == []
        assert error is not None and "registration_surfaces" in error


class TestEvaluatePlan:
    def test_complete_plan_zero_findings(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _base_scope())
        assert plan_obligations.evaluate_plan(path) == []

    def test_missing_manifest_entry_named_by_path(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "scripts/checks/roadmap/_manifest.py"]
        path = _write(tmp_path, data)
        findings = plan_obligations.evaluate_plan(path)
        assert any("scripts/checks/roadmap/_manifest.py" in f for f in findings)

    def test_missing_taxonomy_row_named_by_path(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "config/ci_rca_taxonomy.yaml"]
        path = _write(tmp_path, data)
        findings = plan_obligations.evaluate_plan(path)
        assert any("config/ci_rca_taxonomy.yaml" in f for f in findings)

    def test_missing_mirror_test_named_by_path(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "tests/checks/roadmap/test_validate_x.py"]
        path = _write(tmp_path, data)
        findings = plan_obligations.evaluate_plan(path)
        assert any("tests/checks/roadmap/test_validate_x.py" in f for f in findings)

    def test_sub_v4_plan_grandfathered(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["schema_version"] = 3
        data["scope"] = [{"file": "scripts/checks/roadmap/validate_y.py", "action": "Create", "purpose": "p"}]
        path = _write(tmp_path, data)
        assert plan_obligations.evaluate_plan(path) == []

    def test_non_implementation_plan_grandfathered(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["plan_type"] = "STRATEGIC"
        data["scope"] = [{"file": "scripts/checks/roadmap/validate_y.py", "action": "Create", "purpose": "p"}]
        path = _write(tmp_path, data)
        assert plan_obligations.evaluate_plan(path) == []

    def test_absent_file_reports_finding_never_raises(self, tmp_path: Path) -> None:
        findings = plan_obligations.evaluate_plan(tmp_path / "PLAN-absent.yaml")
        assert findings and "could not read" in findings[0]

    def test_malformed_yaml_reports_finding_never_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "PLAN-bad.yaml"
        bad.write_text("key: [unterminated", encoding="utf-8")
        findings = plan_obligations.evaluate_plan(bad)
        assert findings and "could not parse" in findings[0]

    def test_non_mapping_plan_reports_finding_never_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "PLAN-bad.yaml"
        bad.write_text("- 1\n- 2\n", encoding="utf-8")
        findings = plan_obligations.evaluate_plan(bad)
        assert findings and "not a YAML mapping" in findings[0]

    def test_malformed_contract_surfaces_as_finding_never_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _base_scope())
        with patch.object(plan_obligations, "_CONTRACT_PATH", tmp_path / "absent-contract.yaml"):
            findings = plan_obligations.evaluate_plan(path)
        assert findings and "could not read" in findings[0]

    def test_action_gate_ignores_modify_of_check_module_pattern(self, tmp_path: Path) -> None:
        """A Modify (not Create) row matching the trigger pattern never obligates companions."""
        data = _base_scope()
        data["scope"] = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Modify", "purpose": "p"}]
        path = _write(tmp_path, data)
        assert plan_obligations.evaluate_plan(path) == []


class TestDeriveFindingsEdgeCases:
    """Direct coverage of _derive_findings/_scope_rows' malformed-rule defensive branches --
    a malformed docs/contracts/plan-obligations.yaml entry must be skipped, never raise."""

    def test_scope_rows_non_list_scope_returns_empty(self) -> None:
        assert plan_obligations._scope_rows({"scope": "not-a-list"}) == []

    def test_rule_with_non_dict_trigger_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [{"trigger": "not-a-dict", "requires": []}]
        assert plan_obligations._derive_findings(scope, rules) == []

    def test_rule_with_empty_file_pattern_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [{"trigger": {"file_pattern": ""}, "requires": []}]
        assert plan_obligations._derive_findings(scope, rules) == []

    def test_rule_with_invalid_regex_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [{"trigger": {"file_pattern": "("}, "requires": []}]
        assert plan_obligations._derive_findings(scope, rules) == []

    def test_rule_with_non_list_requires_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [{"trigger": {"file_pattern": r"^scripts/checks/(?P<domain>[^/]+)/.*\.py$"}, "requires": "not-a-list"}]
        assert plan_obligations._derive_findings(scope, rules) == []

    def test_non_dict_requirement_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [{"trigger": {"file_pattern": r"^scripts/checks/(?P<domain>[^/]+)/.*\.py$"}, "requires": ["not-a-dict"]}]
        assert plan_obligations._derive_findings(scope, rules) == []

    def test_requirement_with_empty_path_template_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [
            {
                "trigger": {"file_pattern": r"^scripts/checks/(?P<domain>[^/]+)/.*\.py$"},
                "requires": [{"label": "x", "path_template": ""}],
            }
        ]
        assert plan_obligations._derive_findings(scope, rules) == []

    def test_requirement_with_unresolvable_template_group_skipped(self) -> None:
        scope = [{"file": "scripts/checks/roadmap/validate_x.py", "action": "Create"}]
        rules = [
            {
                "trigger": {"file_pattern": r"^scripts/checks/(?P<domain>[^/]+)/.*\.py$"},
                "requires": [{"label": "x", "path_template": "{undeclared_group}/foo.py"}],
            }
        ]
        assert plan_obligations._derive_findings(scope, rules) == []


class TestNetNewV4ImplementationPlanPaths:
    def test_never_globs_docs_plans(self) -> None:
        with patch.object(plan_obligations._common, "get_status_aware_diff", return_value=[]) as mock_diff:
            with patch.object(Path, "glob") as mock_glob:
                assert plan_obligations.net_new_v4_implementation_plan_paths() == []
                mock_glob.assert_not_called()
        mock_diff.assert_called_once()

    def test_filters_to_added_v4_implementation_plans(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / "docs" / "plans"
        plans_dir.mkdir(parents=True)
        good = plans_dir / "PLAN-fixture-probe.yaml"
        good.write_text(yaml.safe_dump(_base_scope()), encoding="utf-8")
        legacy_data = _base_scope()
        legacy_data["schema_version"] = 3
        legacy_data["slug"] = "legacy"
        legacy_data["plan_path"] = "docs/plans/PLAN-legacy.yaml"
        (plans_dir / "PLAN-legacy.yaml").write_text(yaml.safe_dump(legacy_data), encoding="utf-8")
        diff = [
            ("A", "docs/plans/PLAN-fixture-probe.yaml"),
            ("A", "docs/plans/PLAN-legacy.yaml"),
            ("M", "docs/plans/PLAN-modified-not-added.yaml"),
        ]
        with patch.object(plan_obligations._common, "get_status_aware_diff", return_value=diff):
            with patch.object(plan_obligations, "ROOT", tmp_path):
                paths = plan_obligations.net_new_v4_implementation_plan_paths()
        assert paths == [good]

    def test_absent_or_malformed_diff_entry_never_raises(self) -> None:
        diff = [("A", "docs/plans/PLAN-does-not-exist.yaml")]
        with patch.object(plan_obligations._common, "get_status_aware_diff", return_value=diff):
            assert plan_obligations.net_new_v4_implementation_plan_paths() == []

    def test_non_added_status_skipped(self) -> None:
        diff = [("M", "docs/plans/PLAN-plan-obligation-closure.yaml")]
        with patch.object(plan_obligations._common, "get_status_aware_diff", return_value=diff):
            assert plan_obligations.net_new_v4_implementation_plan_paths() == []

    def test_non_plan_path_skipped(self) -> None:
        diff = [("A", "docs/plans/README.md")]
        with patch.object(plan_obligations._common, "get_status_aware_diff", return_value=diff):
            assert plan_obligations.net_new_v4_implementation_plan_paths() == []


class TestEnforcedElsewherePointerEdgeCases:
    """Defensive-branch coverage for _derive_enforced_elsewhere_pointers (via build_report) --
    a malformed rule among several must never crash the report, and a genuinely well-formed
    sibling rule still produces its pointer."""

    def _write_contract(self, tmp_path: Path, surfaces: dict) -> Path:
        path = tmp_path / "contract.yaml"
        path.write_text(yaml.safe_dump({"registration_surfaces": surfaces}), encoding="utf-8")
        return path

    def test_malformed_sibling_rules_never_crash_and_good_rule_still_reported(self, tmp_path: Path) -> None:
        good_entry = {
            "label": "good enforcer",
            "enforced_by": "validate_check_accounting",
            "why_not_a_scope_row": "fixture reason.",
            "fires_on": ["Create"],
        }
        surfaces = {
            "bad_rule_not_dict": "oops",
            "bad_trigger_not_dict": {"trigger": "nope", "enforced_elsewhere": [good_entry]},
            "bad_pattern_missing": {"trigger": {"action": "Create"}, "enforced_elsewhere": [good_entry]},
            "bad_regex": {"trigger": {"file_pattern": "(", "action": "Create"}, "enforced_elsewhere": [good_entry]},
            "bad_entry_not_dict": {
                "trigger": {"file_pattern": r"^x/(?P<n>.+)\.py$", "action": "Create"},
                "enforced_elsewhere": ["not-a-dict"],
            },
            "good_rule": {
                "trigger": {"file_pattern": r"^x/(?P<n>.+)\.py$", "action": "Create"},
                "enforced_elsewhere": [good_entry],
            },
        }
        contract_path = self._write_contract(tmp_path, surfaces)
        plan_data = _base_scope()
        plan_data["scope"] = [{"file": "x/thing.py", "action": "Create", "purpose": "p"}]
        plan_path = _write(tmp_path, plan_data)
        with patch.object(plan_obligations, "_CONTRACT_PATH", contract_path):
            report = plan_obligations.build_report(plan_path)
        assert "good enforcer" in report
        assert "good_rule" in report

    def test_build_report_pointer_section_absent_on_malformed_contract(self, tmp_path: Path) -> None:
        bad_contract = tmp_path / "bad-contract.yaml"
        bad_contract.write_text("key: [unterminated", encoding="utf-8")
        plan_path = _write(tmp_path, _base_scope())
        with patch.object(plan_obligations, "_CONTRACT_PATH", bad_contract):
            report = plan_obligations.build_report(plan_path)
        assert "enforced elsewhere" not in report


class TestObligationGrammar:
    """Coverage for validate_obligation_grammar's five enforced_elsewhere admission guards."""

    def _contract(self, tmp_path: Path, surfaces: dict) -> Path:
        path = tmp_path / "contract.yaml"
        path.write_text(yaml.safe_dump({"registration_surfaces": surfaces}), encoding="utf-8")
        return path

    def _entry(self, **overrides) -> dict:
        entry = {
            "label": "fixture enforcer",
            "enforced_by": "validate_check_accounting",
            "why_not_a_scope_row": "fixture reason -- no companion file this trigger can derive.",
            "fires_on": ["Create"],
        }
        entry.update(overrides)
        return entry

    def _rule(self, entry: dict) -> dict:
        return {
            "trigger": {"file_pattern": r"^fixture/(?P<n>.+)\.py$", "action": "Create"},
            "requires": [],
            "enforced_elsewhere": [entry],
        }

    def test_enforced_elsewhere_rejects_path_template(self, tmp_path: Path) -> None:
        surfaces = {"fixture_rule": self._rule(self._entry(path_template="some/companion/path.py"))}
        path = self._contract(tmp_path, surfaces)
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("path_template" in f and "G1" in f for f in findings)

    def test_enforced_by_must_be_registered(self, tmp_path: Path) -> None:
        surfaces = {"fixture_rule": self._rule(self._entry(enforced_by="not_a_real_registered_check"))}
        path = self._contract(tmp_path, surfaces)
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("not_a_real_registered_check" in f and "G2" in f for f in findings)

    def test_enforced_by_must_be_dispatched(self, tmp_path: Path) -> None:
        # validate_terraform_try is registered but dispatched in neither pre_sequence() nor
        # full_sequence() (invoked directly elsewhere) -- a stable G3 red-case fixture.
        surfaces = {"fixture_rule": self._rule(self._entry(enforced_by="validate_terraform_try"))}
        path = self._contract(tmp_path, surfaces)
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("validate_terraform_try" in f and "G3" in f for f in findings)

    def test_why_not_a_scope_row_required(self, tmp_path: Path) -> None:
        surfaces = {"fixture_rule": self._rule(self._entry(why_not_a_scope_row=""))}
        path = self._contract(tmp_path, surfaces)
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("G4" in f for f in findings)

    def test_fires_on_must_cover_rule_trigger_action(self, tmp_path: Path) -> None:
        # The round-2 defect shape: a Modify-only enforcer under a Create-only rule.
        surfaces = {"fixture_rule": self._rule(self._entry(fires_on=["Modify"]))}
        path = self._contract(tmp_path, surfaces)
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("G5" in f and "Modify" in f and "Create" in f for f in findings)

    def test_live_contract_is_admissible(self) -> None:
        assert plan_obligations.validate_obligation_grammar() == []

    def test_missing_contract_reports_finding_never_raises(self, tmp_path: Path) -> None:
        findings = plan_obligations.validate_obligation_grammar(tmp_path / "absent.yaml")
        assert findings and "could not read" in findings[0]

    def test_malformed_yaml_reports_finding_never_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [unterminated", encoding="utf-8")
        findings = plan_obligations.validate_obligation_grammar(bad)
        assert findings and "could not parse" in findings[0]

    def test_non_mapping_top_level_reports_finding(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("- 1\n- 2\n", encoding="utf-8")
        findings = plan_obligations.validate_obligation_grammar(bad)
        assert findings and "not a YAML mapping" in findings[0]

    def test_missing_registration_surfaces_key_reports_finding(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("contract:\n  id: x\n", encoding="utf-8")
        findings = plan_obligations.validate_obligation_grammar(bad)
        assert findings and "registration_surfaces" in findings[0]

    def test_rule_value_not_a_mapping_rejected(self, tmp_path: Path) -> None:
        path = self._contract(tmp_path, {"bad_rule": "not-a-dict"})
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("bad_rule" in f and "not a mapping" in f for f in findings)

    def test_requires_entry_missing_path_template_rejected(self, tmp_path: Path) -> None:
        rule = {
            "trigger": {"file_pattern": r"^fixture/(?P<n>.+)\.py$", "action": "Create"},
            "requires": [{"label": "some companion", "path_template": ""}],
        }
        path = self._contract(tmp_path, {"fixture_rule": rule})
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("some companion" in f and "G1" in f for f in findings)

    def test_requires_non_list_skipped_without_finding(self, tmp_path: Path) -> None:
        rule = {"trigger": {"file_pattern": r"^fixture/(?P<n>.+)\.py$", "action": "Create"}, "requires": "not-a-list"}
        path = self._contract(tmp_path, {"fixture_rule": rule})
        assert plan_obligations.validate_obligation_grammar(path) == []

    def test_requires_list_with_non_dict_entry_skipped(self, tmp_path: Path) -> None:
        rule = {
            "trigger": {"file_pattern": r"^fixture/(?P<n>.+)\.py$", "action": "Create"},
            "requires": ["not-a-dict"],
        }
        path = self._contract(tmp_path, {"fixture_rule": rule})
        assert plan_obligations.validate_obligation_grammar(path) == []

    def test_enforced_by_absent_entirely_rejected(self, tmp_path: Path) -> None:
        entry = self._entry()
        del entry["enforced_by"]
        surfaces = {"fixture_rule": self._rule(entry)}
        path = self._contract(tmp_path, surfaces)
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("names no enforced_by" in f and "G2" in f for f in findings)

    def test_enforced_elsewhere_entry_not_a_mapping_rejected(self, tmp_path: Path) -> None:
        rule = {
            "trigger": {"file_pattern": r"^fixture/(?P<n>.+)\.py$", "action": "Create"},
            "requires": [],
            "enforced_elsewhere": ["not-a-dict"],
        }
        path = self._contract(tmp_path, {"fixture_rule": rule})
        findings = plan_obligations.validate_obligation_grammar(path)
        assert any("fixture_rule" in f and "not a mapping" in f for f in findings)


class TestEnforcedElsewhereReport:
    def test_new_check_module_report_names_accounting_enforcer(self, tmp_path: Path) -> None:
        """rec-3747 behavioural oracle: the pointer section fires even on a closure-complete
        plan (no unmet `requires` findings) -- that's exactly the live miss rec-3747 recorded."""
        path = _write(tmp_path, _base_scope())
        report = plan_obligations.build_report(path)
        assert "no unmet registration obligations" in report
        assert "validate_check_accounting" in report
        assert "Decision 170" in report


class TestWorkflowRule:
    def test_new_workflow_requires_taxonomy_row(self, tmp_path: Path) -> None:
        data = {
            "schema_version": 4,
            "plan_type": "IMPLEMENTATION",
            "slug": "fixture-probe",
            "plan_path": "docs/plans/PLAN-fixture-probe.yaml",
            "scope": [
                {"file": ".github/workflows/new-thing.yml", "action": "Create", "purpose": "p"},
            ],
        }
        path = _write(tmp_path, data)
        findings = plan_obligations.evaluate_plan(path)
        assert any("config/ci_rca_taxonomy.yaml" in f for f in findings)
        assert any(".github/workflows/new-thing.yml" in f for f in findings)

    def test_nested_or_yaml_extension_workflow_not_matched(self, tmp_path: Path) -> None:
        """The trigger pattern matches only top-level *.yml (never nested paths or *.yaml),
        mirroring scripts.ci_rca.taxonomy.enumerate_workflow_name_paths' own glob."""
        data = {
            "schema_version": 4,
            "plan_type": "IMPLEMENTATION",
            "slug": "fixture-probe",
            "plan_path": "docs/plans/PLAN-fixture-probe.yaml",
            "scope": [
                {"file": ".github/workflows/nested/new-thing.yml", "action": "Create", "purpose": "p"},
                {"file": ".github/workflows/other-thing.yaml", "action": "Create", "purpose": "p"},
            ],
        }
        path = _write(tmp_path, data)
        assert plan_obligations.evaluate_plan(path) == []


class TestMainCli:
    def test_cli_always_exits_zero_on_unmet_obligations(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "config/ci_rca_taxonomy.yaml"]
        path = _write(tmp_path, data)
        assert plan_obligations.main(["--plan", str(path)]) == 0

    def test_cli_always_exits_zero_on_absent_plan(self, tmp_path: Path) -> None:
        assert plan_obligations.main(["--plan", str(tmp_path / "PLAN-absent.yaml")]) == 0

    def test_cli_subprocess_never_mutates_tree(self) -> None:
        before = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", cwd=plan_obligations.ROOT
        ).stdout
        rc = subprocess.run(
            [
                "bin/venv-python",
                "-m",
                "scripts.roadmap.plan_obligations",
                "--plan",
                "docs/plans/PLAN-plan-obligation-closure.yaml",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=plan_obligations.ROOT,
        ).returncode
        after = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", cwd=plan_obligations.ROOT
        ).stdout
        assert rc == 0
        assert before == after


def _report(tmp_path: Path, plan: dict, *, gating: dict[str, bool] | None = None) -> str:
    fx.write_contract(tmp_path, gating=gating)
    path = _write(tmp_path, plan)
    with patch.object(plan_obligations, "ROOT", tmp_path), patch("scripts.roadmap.plan_lint.ROOT", tmp_path):
        return plan_obligations.build_report(path)


def test_near_cap_structural_scope_row_is_reported(tmp_path: Path) -> None:
    fx.write_structural_registry(tmp_path, limit=10)
    fx.write(tmp_path, "docs/contracts/c.yaml", "a: 1\n" * 10)
    with patch.object(_common, "ROOT", tmp_path):
        report = _report(tmp_path, fx.make_plan(("docs/contracts/c.yaml", "Modify", "p")))
    assert "derived findings (advisory" in report
    assert "docs/contracts/c.yaml: 10 of 10 effective lines (contracts), headroom 0 NEAR_CAP" in report


def test_near_cap_report_names_unbuilt_plans_editing_the_same_file(tmp_path: Path) -> None:
    rows = [{"file": "docs/contracts/c.yaml", "action": "Modify", "purpose": "p"}]
    fx.write_yaml(tmp_path, "docs/plans/PLAN-other.yaml", {"schema_version": 4, "scope": rows})
    fx.git_commit(tmp_path)
    report = _report(tmp_path, fx.make_plan(("docs/contracts/c.yaml", "Modify", "p")))
    assert "docs/plans/PLAN-other.yaml (unbuilt) also scopes docs/contracts/c.yaml" in report


def test_scoped_import_edge_and_baselined_check_obligate_companions(tmp_path: Path) -> None:
    fx.write_closure_repo(tmp_path)
    fx.write_baseline(tmp_path, ["validate_thing"])
    fake = Entry(
        name="validate_thing",
        module="scripts.checks.fake.validate_thing",
        attr="validate_thing",
        pre=True,
        pre_globs=("scripts/checks/fake/**",),
    )
    module = "scripts/checks/fake/validate_thing.py"
    plan = fx.make_plan((module, "Modify", "p"))
    plan["scope"][0]["new_imports"] = ["scripts.hublib"]
    with patch.object(registry, "_ALL_ENTRIES", {fake.name: fake}):
        report = _report(tmp_path, plan)
    assert "scripts/checks/fake/_manifest.py is required in scope" in report
    assert "config/check_accounting_baseline.yaml is required in scope" in report


def test_undecidable_bundled_rec_acceptance_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fetched = ([{"id": "rec-1", "acceptance": "true"}, {"id": "rec-2", "acceptance": "grep -q a b"}], ["rec-3"])
    assert acceptance.main(["rec-1", "rec-2", "rec-3"], fetch=lambda _ids: fetched) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("rec-1: acceptance UNDECIDABLE") and lines[1] == "rec-2: acceptance decidable"
    assert lines[2] == "rec-3: not found by the reader"
    plan = fx.make_plan(("scripts/x.py", "Modify", "p"), bundled_recommendations=["rec-1"])
    with patch.dict(plan_lint._DISPATCH, {"acceptance_decidable": lambda _ctx: lines[:1]}):
        assert lines[0] in _report(tmp_path, plan)


def test_class_d_contract_scope_row_obligates_amendment_entry(tmp_path: Path) -> None:
    fx.write_class_d(tmp_path, "docs/contracts/d.yaml", with_log=False)
    fx.write_contract(tmp_path)
    path = _write(tmp_path, fx.make_plan(("docs/contracts/d.yaml", "Modify", "adds a field")))
    with patch.object(plan_obligations, "ROOT", tmp_path):
        findings = plan_obligations.evaluate_plan(path)
    assert len(findings) == 1 and "amendment_log" in findings[0] and findings[0].startswith(path.name)


def test_registry_shard_naming_a_scoped_test_is_reported(tmp_path: Path) -> None:
    rel = fx.write_shard(tmp_path, "keep-me", "tests/test_a.py::TestX::test_y")
    fx.write_contract(tmp_path)
    path = _write(tmp_path, fx.make_plan(("tests/test_a.py", "Modify", "p")))
    with patch.object(plan_obligations, "ROOT", tmp_path):
        assert rel in plan_obligations.evaluate_plan(path)[0]
