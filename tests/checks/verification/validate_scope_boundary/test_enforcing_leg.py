"""TestEnforcingLeg (Decision 131 concern-split decomposition of the former flat
test_validate_scope_boundary.py monolith that used to live one directory up), moved verbatim.

Covers the diff-vs-scope matrix: an unsanctioned path fails, a prohibited plan-field edit fails, a
fully-declared diff passes, each pre-existing sanction_rows trigger kind derives its path
correctly, and an unimplemented trigger kind fails loud. The secrets_baseline_regeneration
trigger kind's own arms live in test_secrets_baseline_row.py.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml as _yaml

from .conftest import (
    _DEFAULT_SANCTION_ROWS,
    _DUMMY_VP_STEP,
    _commit_all,
    _git,
    _init_repo,
    _plan_dict,
    _ResolvedFixture,
    _write_contract,
    _write_plan,
    validate_scope_boundary,
)

_V5_POLICY = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}


def _v5_plan(slug: str, scope: list[dict], vp: list[dict] | None = None, declared: bool = False) -> dict:
    plan = _plan_dict(slug, scope, vp, declared)
    plan.update({"schema_version": 5, "handoff_policy": dict(_V5_POLICY)})
    return plan


def _flip_repo(tmp_path: Path, slug: str, base: dict, working: dict, rows: dict | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_contract(repo, rows)
    rel = f"docs/plans/PLAN-{slug}.yaml"
    (repo / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(_yaml.dump(base), encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
    (repo / rel).write_text(_yaml.dump(working), encoding="utf-8")
    _commit_all(repo, "working")
    return repo, rel


def _normalisation_scope() -> list[dict]:
    return [
        {"file": "scripts/foo.py", "action": "Modify", "purpose": "x", "new_imports": ["scripts.a.b"]},
        {"file": "scripts/bar.py", "action": "Modify", "purpose": "y", "new_imports": []},
    ]


def _normalisation_vp() -> list[dict]:
    return [{**_DUMMY_VP_STEP, "command": "echo hi", "expected": "hi", "expected_literals": ["lit"]}]


def _edit_scope(plan: dict, **changes: object) -> None:
    plan["scope"][0].update(changes)


_PROHIBITED_CASES: dict[str, tuple[Callable[[dict], None], str]] = {
    "scope_file": (lambda p: _edit_scope(p, file="scripts/other.py"), "'scope'"),
    "scope_action": (lambda p: _edit_scope(p, action="Create"), "'scope'"),
    "scope_purpose": (lambda p: _edit_scope(p, purpose="a different purpose"), "'scope'"),
    "scope_new_imports_emptied": (lambda p: _edit_scope(p, new_imports=[]), "'scope'"),
    "scope_new_imports_swapped": (lambda p: _edit_scope(p, new_imports=["scripts.c.d"]), "'scope'"),
    "scope_row_added": (
        lambda p: p["scope"].append({"file": "scripts/new.py", "action": "Create", "purpose": "z", "new_imports": []}),
        "'scope'",
    ),
    "acceptance_criteria": (lambda p: p.update(acceptance_criteria=["a DIFFERENT criterion"]), "'acceptance_criteria'"),
    "vp_command": (lambda p: p["verification_plan"][0].update(command="echo weakened"), "field 'command'"),
    "vp_expected": (lambda p: p["verification_plan"][0].update(expected="weakened"), "field 'expected'"),
    "vp_expected_literals": (
        lambda p: p["verification_plan"][0].update(expected_literals=["other"]),
        "field 'expected_literals'",
    ),
}


class TestEnforcingLeg:
    def test_unsanctioned_path_fails(self, tmp_path: Path) -> None:
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-unsanctioned", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py", "scripts/rogue.py"], root=repo)
        assert any("scripts/rogue.py" in f and "STOP" in f for f in failed)

    def test_deleted_path_outside_scope_still_flagged(self, tmp_path: Path) -> None:
        """A 'D' row from get_status_aware_diff surfaces as a plain path here (status dropped) --
        an out-of-scope deletion must still fail, not be silently invisible."""
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-deleted", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py", "scripts/removed.py"], root=repo)
        assert any("scripts/removed.py" in f for f in failed)

    def test_fully_declared_diff_passes(self, tmp_path: Path) -> None:
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-declared", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert failed == []

    def test_prohibited_plan_field_edit_fails(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo)
        _write_plan(repo, "sb-field-edit", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=False)
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = "docs/plans/PLAN-sb-field-edit.yaml"
        plan = _plan_dict("sb-field-edit", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=True)
        plan["acceptance_criteria"] = ["a DIFFERENT criterion than base"]
        (repo / rel).write_text(_yaml.dump(plan), encoding="utf-8")
        _commit_all(repo, "declare + weaken acceptance_criteria")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert any("prohibited plan-field edit" in f and "acceptance_criteria" in f for f in failed)

    def test_prohibited_vp_step_command_edit_fails(self, tmp_path: Path) -> None:
        """VP-step substitution: a resolved plan's own verification_plan[].command changing from
        base is the CONTENT invariant's other mechanical enforcement arm."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo)
        step = dict(_DUMMY_VP_STEP)
        step["command"] = "echo original"
        _write_plan(
            repo, "sb-step-edit", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], [step], declared=False
        )
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = "docs/plans/PLAN-sb-step-edit.yaml"
        weakened_step = dict(step)
        weakened_step["command"] = "echo weakened"
        plan = _plan_dict(
            "sb-step-edit", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], [weakened_step], declared=True
        )
        (repo / rel).write_text(_yaml.dump(plan), encoding="utf-8")
        _commit_all(repo, "declare + weaken VP step command")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert any("prohibited plan-field edit" in f and "step 1 field 'command'" in f for f in failed)

    def test_prohibited_vp_step_deletion_fails(self, tmp_path: Path) -> None:
        """A declared VP step deleted outright (rather than its command edited) is a strictly
        more effective weakening than substitution and must be caught the same way -- a step
        present at base but absent from the working tree's verification_plan."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo)
        step1 = dict(_DUMMY_VP_STEP)
        step2 = dict(_DUMMY_VP_STEP)
        step2["step"] = 2
        step2["command"] = "echo second"
        _write_plan(
            repo,
            "sb-step-delete",
            [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}],
            [step1, step2],
            declared=False,
        )
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = "docs/plans/PLAN-sb-step-delete.yaml"
        plan = _plan_dict(
            "sb-step-delete", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], [step1], declared=True
        )
        (repo / rel).write_text(_yaml.dump(plan), encoding="utf-8")
        _commit_all(repo, "declare + delete VP step 2 entirely")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert any("prohibited plan-field edit" in f and "step 2" in f and "deleted" in f for f in failed)

    def test_permitted_field_edit_does_not_fail(self, tmp_path: Path) -> None:
        """implementation_declared flipping true is the whole point of the resolved-plan flow --
        never itself a prohibited edit."""
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-permitted", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert not any("prohibited plan-field edit" in f for f in failed)

    def test_graduated_registry_shard_row_sanctions_derived_path(self, tmp_path: Path) -> None:
        repo, rel = _ResolvedFixture().build(
            tmp_path,
            "sb-graduated",
            [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}],
            verification_plan=[
                {
                    "step": 1,
                    "phase": "pre-deploy",
                    "hermetic": True,
                    "action": "a",
                    "command": "true",
                    "expected": "n/a",
                    "fix_if": "n/a",
                    "graduation": "graduate",
                    "graduation_check_id": "sb-my-check",
                }
            ],
        )
        shard = "config/agent/verification_registry/entries/sb-my-check.yaml"
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py", shard], root=repo)
        assert failed == []

    def test_decisions_index_regeneration_row_sanctions_when_decisions_md_in_scope(self, tmp_path: Path) -> None:
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-decisions", [{"file": "docs/DECISIONS.md", "action": "Modify", "purpose": "x"}]
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "docs/DECISIONS.md", "docs/decisions-index.json"], root=repo)
        assert failed == []

    def test_decisions_index_not_sanctioned_when_decisions_md_not_in_scope(self, tmp_path: Path) -> None:
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-no-decisions", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py", "docs/decisions-index.json"], root=repo)
        assert any("docs/decisions-index.json" in f for f in failed)

    def test_unimplemented_sanction_kind_fails_loud(self, tmp_path: Path) -> None:
        rows = dict(_DEFAULT_SANCTION_ROWS)
        rows["mystery_row"] = {"trigger": {"kind": "some_unimplemented_kind"}, "sanctions": {}}
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-mystery", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], sanction_rows=rows
        )
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert any("unimplemented trigger kind" in f and "mystery_row" in f for f in failed)

    def test_missing_contract_fails_loud(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        # No _write_contract() call -- the contract file is simply absent.
        _write_plan(repo, "sb-no-contract", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=False)
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
        rel = _write_plan(
            repo, "sb-no-contract", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=True
        )
        _commit_all(repo, "declare implementation")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert any("could not load" in f and "implement-scope-boundary.yaml" in f for f in failed)

    def test_deleted_resolved_plan_path_is_skipped(self, tmp_path: Path, capsys) -> None:
        """A resolved-but-deleted plan path (only reachable via the plan_paths override -- real
        diff-derived resolution requires the plan to exist on disk) contributes no scope and no
        sanctions, but never raises -- it is printed as SKIP, not a failure in its own right."""
        repo, rel = _ResolvedFixture().build(
            tmp_path, "sb-gone", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        )
        (repo / rel).unlink()

        failed: list[str] = []
        validate_scope_boundary(failed, plan_paths=[rel], changed_files=[], root=repo)
        out = capsys.readouterr().out
        assert failed == []
        assert f"SKIP: {rel} (not present on disk -- deleted in this diff)" in out

    def test_malformed_resolved_plan_fails_loud(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo)
        plans_dir = repo / "docs" / "plans"
        plans_dir.mkdir(parents=True)
        rel = "docs/plans/PLAN-sb-malformed.yaml"
        (plans_dir / "PLAN-sb-malformed.yaml").write_text(
            _yaml.dump({"implementation_declared": True, "slug": "sb-malformed"}), encoding="utf-8"
        )
        _commit_all(repo, "malformed plan")

        failed: list[str] = []
        validate_scope_boundary(failed, plan_paths=[rel], changed_files=[rel], root=repo)
        assert any("could not load plan" in f for f in failed)

    def test_prohibited_field_edits_empty_list_is_noop(self, tmp_path: Path) -> None:
        rows = {k: dict(v) for k, v in _DEFAULT_SANCTION_ROWS.items()}
        rows["implementing_plan_bookkeeping"]["prohibited_field_edits"] = []
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo, rows)
        _write_plan(repo, "sb-no-prohibited", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=False)
        base_sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = "docs/plans/PLAN-sb-no-prohibited.yaml"
        plan = _plan_dict("sb-no-prohibited", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=True)
        plan["acceptance_criteria"] = ["a DIFFERENT criterion than base"]
        (repo / rel).write_text(_yaml.dump(plan), encoding="utf-8")
        _commit_all(repo, "declare + edit acceptance_criteria, but no prohibited_field_edits declared")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert failed == []

    def test_net_new_plan_absent_at_base_skips_prohibited_edit_check(self, tmp_path: Path) -> None:
        """A plan with no prior content at base (net-new, same-PR plan+implementation) has
        nothing to diff a prohibited edit against -- never a failure."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo)
        base_sha = _commit_all(repo, "base (no plan yet)")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        rel = _write_plan(repo, "sb-net-new", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=True)
        _commit_all(repo, "add plan, already declared")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert failed == []

    def test_unparseable_plan_content_at_base_treated_as_absent(self, tmp_path: Path) -> None:
        """Invalid YAML at the base ref for a resolved plan's path is treated the same as
        genuinely absent -- never a raise, never a false-positive prohibited-edit finding."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo)
        plans_dir = repo / "docs" / "plans"
        plans_dir.mkdir(parents=True)
        rel = "docs/plans/PLAN-sb-unparseable.yaml"
        (plans_dir / "PLAN-sb-unparseable.yaml").write_text("not: valid: yaml: [unclosed", encoding="utf-8")
        base_sha = _commit_all(repo, "base with unparseable plan content")
        _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])

        (plans_dir / "PLAN-sb-unparseable.yaml").write_text(
            _yaml.dump(
                _plan_dict("sb-unparseable", [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}], declared=True)
            ),
            encoding="utf-8",
        )
        _commit_all(repo, "replace with valid, declared plan")

        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert failed == []

    def test_empty_new_imports_at_base_is_not_a_scope_edit(self, tmp_path: Path) -> None:
        scope = [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x", "new_imports": []}]
        base = _v5_plan("sb-empty-ni", scope)
        repo, rel = _flip_repo(tmp_path, "sb-empty-ni", base, _v5_plan("sb-empty-ni", scope, declared=True))
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert not any("prohibited plan-field edit" in f for f in failed), failed

    def test_quoted_step_ids_at_base_match_integer_working_steps(self, tmp_path: Path) -> None:
        scope = [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        base = _v5_plan("sb-quoted-ids", scope, [{**_DUMMY_VP_STEP, "step": "1"}])
        working = _v5_plan("sb-quoted-ids", scope, [{**_DUMMY_VP_STEP, "step": 1}], declared=True)
        repo, rel = _flip_repo(tmp_path, "sb-quoted-ids", base, working)
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert not any("deleted" in f or "prohibited plan-field edit" in f for f in failed), failed

    @pytest.mark.parametrize("case", sorted(_PROHIBITED_CASES))
    def test_genuine_prohibited_edit_still_caught_with_empty_new_imports(self, tmp_path: Path, case: str) -> None:
        mutate, needle = _PROHIBITED_CASES[case]
        base = _v5_plan("sb-pin", _normalisation_scope(), _normalisation_vp())
        working = copy.deepcopy(base)
        working["implementation_declared"] = True
        mutate(working)
        rows = {k: copy.deepcopy(v) for k, v in _DEFAULT_SANCTION_ROWS.items()}
        old = rows["implementing_plan_bookkeeping"]["prohibited_field_edits"]
        rows["implementing_plan_bookkeeping"]["prohibited_field_edits"] = [*old, "verification_plan[].expected_literals"]
        repo, rel = _flip_repo(tmp_path, "sb-pin", base, working, rows)
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py", "scripts/other.py"], root=repo)
        assert not any("could not load plan" in f or "fails PlanDocument validation" in f for f in failed), failed
        assert any("prohibited plan-field edit" in f and needle in f for f in failed), failed
        if not case.startswith("scope_"):
            assert not any("'scope'" in f for f in failed), failed

    def test_schema_invalid_base_plan_fails_closed(self, tmp_path: Path) -> None:
        scope = [{"file": "scripts/foo.py", "action": "Modify", "purpose": "x"}]
        base = _v5_plan("sb-bad-base", scope)
        base["plan_type"] = "NOT_A_PLAN_TYPE"
        repo, rel = _flip_repo(tmp_path, "sb-bad-base", base, _v5_plan("sb-bad-base", scope, declared=True))
        failed: list[str] = []
        validate_scope_boundary(failed, changed_files=[rel, "scripts/foo.py"], root=repo)
        assert any("fails PlanDocument validation" in f and rel in f for f in failed), failed
