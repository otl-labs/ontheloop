"""Edge cases per plan_lint derivation on tmp-path fixture repos."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks import registry
from scripts.checks._schema import Entry
from scripts.roadmap import plan_lint
from tests.fixtures import plan_lint_fixtures as fx

_FAKE = Entry(
    name="validate_thing",
    module="scripts.checks.fake.validate_thing",
    attr="validate_thing",
    pre=True,
    pre_globs=("scripts/checks/fake/**",),
)
_MODULE = "scripts/checks/fake/validate_thing.py"


def _derive(root: Path, plan: dict, *, report: bool = False) -> plan_lint.LintResult:
    fx.write_contract(root)
    with patch.object(registry, "_ALL_ENTRIES", {_FAKE.name: _FAKE}):
        return plan_lint.derive(plan, root, report=report)


def _import_plan(target: str, *extra: tuple[str, str, str]) -> dict:
    plan = fx.make_plan((_MODULE, "Modify", "p"), *extra)
    plan["scope"][0]["new_imports"] = [target]
    return plan


class TestPreGlobClosure:
    def test_edge_to_an_existing_module_requires_the_manifest(self, tmp_path: Path) -> None:
        fx.write_closure_repo(tmp_path)
        result = _derive(tmp_path, _import_plan("scripts.hublib"))
        assert len(result.gating) == 1
        assert "scripts/hublib.py" in result.gating[0] and "scripts/checks/fake/_manifest.py" in result.gating[0]

    def test_manifest_in_scope_discharges_it(self, tmp_path: Path) -> None:
        fx.write_closure_repo(tmp_path)
        plan = _import_plan("scripts.hublib", ("scripts/checks/fake/_manifest.py", "Modify", "p"))
        assert _derive(tmp_path, plan).gating == []

    @pytest.mark.parametrize("created", ["scripts/newlib.py", "scripts/newpkg/__init__.py"])
    def test_edge_to_a_not_yet_created_module(self, tmp_path: Path, created: str) -> None:
        fx.write_closure_repo(tmp_path)
        module = created.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
        result = _derive(tmp_path, _import_plan(module, (created, "Create", "p")))
        assert len(result.gating) == 1 and created in result.gating[0]

    def test_already_covered_edge_gives_nothing(self, tmp_path: Path) -> None:
        fx.write_closure_repo(tmp_path)
        fx.write(tmp_path, "scripts/checks/fake/helper.py", "X = 1\n")
        assert _derive(tmp_path, _import_plan("scripts.checks.fake.helper")).gating == []

    def test_no_import_view_is_built_without_new_imports(self, tmp_path: Path) -> None:
        fx.write_closure_repo(tmp_path)
        plan = fx.make_plan((_MODULE, "Modify", "p"))
        with patch("scripts.dependency_graph.build_graph", side_effect=AssertionError("built")):
            assert _derive(tmp_path, plan, report=False).gating == []

    def test_report_lists_a_reached_module_with_no_new_imports(self, tmp_path: Path) -> None:
        fx.write_closure_repo(tmp_path)
        result = _derive(tmp_path, fx.make_plan((_MODULE, "Modify", "p")), report=True)
        assert any(_MODULE in line and "validate_thing" in line for line in result.advisory)


class TestAccountingBaseline:
    def test_baselined_module_modify_requires_the_baseline_file(self, tmp_path: Path) -> None:
        fx.write_baseline(tmp_path, ["validate_thing"])
        result = _derive(tmp_path, fx.make_plan((_MODULE, "Modify", "p")))
        assert len(result.gating) == 1 and "validate_thing" in result.gating[0]

    def test_baseline_file_in_scope_or_unbaselined_check_is_clean(self, tmp_path: Path) -> None:
        fx.write_baseline(tmp_path, ["validate_thing"])
        plan = fx.make_plan((_MODULE, "Modify", "p"), ("config/check_accounting_baseline.yaml", "Modify", "p"))
        assert _derive(tmp_path, plan).gating == []
        fx.write_baseline(tmp_path, ["validate_other"])
        assert _derive(tmp_path, fx.make_plan((_MODULE, "Modify", "p"))).gating == []


class TestClassD:
    def test_class_d_without_an_amendment_log_yet_is_named(self, tmp_path: Path) -> None:
        fx.write_class_d(tmp_path, "docs/contracts/d.yaml", with_log=False)
        result = _derive(tmp_path, fx.make_plan(("docs/contracts/d.yaml", "Modify", "adds a field")))
        assert len(result.gating) == 1 and "amendment_log" in result.gating[0]

    def test_purpose_naming_the_entry_discharges_it(self, tmp_path: Path) -> None:
        fx.write_class_d(tmp_path, "docs/contracts/d.yaml", with_log=True)
        assert _derive(tmp_path, fx.make_plan(("docs/contracts/d.yaml", "Modify", "one new amendment_log entry"))).gating == []

    def test_class_c_contract_is_left_alone(self, tmp_path: Path) -> None:
        fx.write_yaml(tmp_path, "docs/contracts/c.yaml", {"contract": {"class": "C"}, "amendment_log": []})
        assert _derive(tmp_path, fx.make_plan(("docs/contracts/c.yaml", "Modify", "p"))).gating == []


class TestRegistryShard:
    def test_shard_naming_a_modified_test_needs_a_disposition(self, tmp_path: Path) -> None:
        rel = fx.write_shard(tmp_path, "keep-me", "tests/test_a.py::test_x")
        result = _derive(tmp_path, fx.make_plan(("tests/test_a.py", "Modify", "p")))
        assert len(result.gating) == 1 and rel in result.gating[0]
        assert _derive(tmp_path, fx.make_plan(("tests/test_a.py", "Delete", "p"))).gating

    def test_shard_in_scope_or_kept_by_check_id_is_clean(self, tmp_path: Path) -> None:
        rel = fx.write_shard(tmp_path, "keep-me", "tests/test_a.py::test_x")
        assert _derive(tmp_path, fx.make_plan(("tests/test_a.py", "Modify", "p"), (rel, "Modify", "p"))).gating == []
        kept = fx.make_plan(("tests/test_a.py", "Modify", "shards kept: keep-me"))
        assert _derive(tmp_path, kept).gating == []

    def test_unrelated_test_row_is_clean(self, tmp_path: Path) -> None:
        fx.write_shard(tmp_path, "keep-me", "tests/test_a.py::test_x")
        assert _derive(tmp_path, fx.make_plan(("tests/test_b.py", "Modify", "p"))).gating == []


class TestHeadroom:
    @pytest.mark.parametrize(("lines", "near"), [(94, False), (95, False), (96, True)])
    def test_near_cap_boundary_is_exactly_five_percent(self, tmp_path: Path, lines: int, near: bool) -> None:
        fx.write(tmp_path, "scripts/big.py", "x = 1\n" * lines)
        fx.write_yaml(tmp_path, "config/sloc_budgets.yaml", {"budgets": {"scripts/big.py": 100}})
        result = _derive(tmp_path, fx.make_plan(("scripts/big.py", "Modify", "p")), report=True)
        assert ("NEAR_CAP" in result.advisory[0]) is near

    def test_prose_row_carries_the_ratchet_note(self, tmp_path: Path) -> None:
        fx.write(tmp_path, "AGENTS.md", "abc")
        fx.write_yaml(tmp_path, "config/prose_budgets.yaml", {"S1": {"AGENTS.md": 3}})
        result = _derive(tmp_path, fx.make_plan(("AGENTS.md", "Modify", "p")), report=True)
        assert "NEAR_CAP" in result.advisory[0] and "ratchet" in result.advisory[0]

    def test_unbuilt_plan_older_than_thirty_days_or_declared_is_not_named(self, tmp_path: Path) -> None:
        rows = [{"file": "scripts/a.py", "action": "Modify", "purpose": "p"}]
        fx.write_yaml(tmp_path, "docs/plans/PLAN-old.yaml", {"schema_version": 4, "scope": rows})
        fx.git_commit(tmp_path, date="2020-01-01T00:00:00")
        fx.write_yaml(tmp_path, "docs/plans/PLAN-new.yaml", {"schema_version": 4, "scope": rows})
        fx.write_yaml(
            tmp_path, "docs/plans/PLAN-done.yaml", {"schema_version": 4, "scope": rows, "implementation_declared": True}
        )
        fx.write_yaml(tmp_path, "docs/plans/PLAN-fixture.yaml", {"schema_version": 4, "scope": rows})
        fx.git_commit(tmp_path)
        plan = fx.make_plan(("scripts/a.py", "Modify", "p"), plan_path="docs/plans/PLAN-fixture.yaml")
        lines = _derive(tmp_path, plan, report=True).advisory
        assert [x for x in lines if "unbuilt" in x] == [
            "docs/plans/PLAN-new.yaml (unbuilt) also scopes scripts/a.py; whichever builds second rebases"
        ]


class TestFrozenCompanions:
    def test_frozen_companion_is_named(self, tmp_path: Path) -> None:
        fx.write_contract(
            tmp_path, frozen=[{"source": "scripts/session/preflight.py", "test": "tests/t.py", "breaks_when": "a key moves"}]
        )
        plan = fx.make_plan(("scripts/session/preflight.py", "Modify", "p"), ("scripts/other.py", "Modify", "p"))
        lines = plan_lint.derive(plan, tmp_path, report=True).advisory
        assert [x for x in lines if "tests/t.py" in x] == ["scripts/session/preflight.py: tests/t.py breaks when a key moves"]


class TestAcceptanceRunner:
    def _ctx(self, tmp_path: Path, recs: list[str]) -> plan_lint.Ctx:
        return plan_lint.Ctx({"bundled_recommendations": recs}, [], tmp_path, [], True)

    def test_returns_the_childs_printed_lines(self) -> None:
        ctx = self._ctx(plan_lint.ROOT, ["rec-1", "rec-2"])
        assert plan_lint._acceptance_lines(ctx, "tests.fixtures.plan_lint_fixtures") == [
            "rec-1: fixture line",
            "rec-2: fixture line",
        ]

    def test_non_zero_exit_and_timeout_are_unreachable(self) -> None:
        ctx = self._ctx(plan_lint.ROOT, ["FAIL"])
        assert plan_lint._acceptance_lines(ctx, "tests.fixtures.plan_lint_fixtures")[0].startswith("UNREACHABLE")
        slow = self._ctx(plan_lint.ROOT, ["SLEEP"])
        with patch.object(plan_lint, "_ACCEPTANCE_TIMEOUT_S", 1):
            assert plan_lint._acceptance_lines(slow, "tests.fixtures.plan_lint_fixtures")[0].startswith("UNREACHABLE")

    def test_spawns_nothing_without_bundled_recs(self, tmp_path: Path) -> None:
        with patch("subprocess.run", side_effect=AssertionError("spawned")):
            assert plan_lint._acceptance_lines(self._ctx(tmp_path, [])) == []


def test_report_false_runs_no_advisory_row(tmp_path: Path) -> None:
    fx.write(tmp_path, "scripts/big.py", "x = 1\n")
    with patch("subprocess.run", side_effect=AssertionError("ran")):
        assert (
            _derive(tmp_path, fx.make_plan(("scripts/big.py", "Modify", "p"), bundled_recommendations=["rec-1"])).advisory
            == []
        )
