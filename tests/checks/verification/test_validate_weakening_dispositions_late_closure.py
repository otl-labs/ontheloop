"""rec-5441 acceptance: the weakening-disposition gate judges the diff, not the plan flip."""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.verification.validate_weakening_dispositions import validate_weakening_dispositions
from tests.fixtures.weakening_dispositions_repo import (
    ASSERT_KEY,
    PLAN,
    STRONG_TEST,
    WEAK_TEST,
    commit_all,
    init_repo,
    planned_entry,
    write,
    write_plan,
)


def _judge(root: Path, base: str, *, push_base: str | None = None) -> list[str]:
    failed: list[str] = []
    with (
        mock.patch("scripts.checks._common.status_diff_base", return_value=base),
        mock.patch("scripts.checks._common.push_context_base", return_value=push_base),
    ):
        validate_weakening_dispositions(failed, root=root)
    return failed


def test_late_closure_flip_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"tests/t.py": WEAK_TEST})
    write_plan(root, PLAN, [planned_entry()], intent="a")
    base = commit_all(root, "build")
    write_plan(root, PLAN, [planned_entry()], intent="a", implementation_declared=True)
    assert _judge(root, base) == []


def test_late_closure_with_registry_shard_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"tests/t.py": WEAK_TEST})
    write_plan(root, PLAN, [planned_entry()], intent="a")
    base = commit_all(root, "build")
    write_plan(root, PLAN, [planned_entry()], intent="a", implementation_declared=True)
    write(root, "config/agent/verification_registry/entries/x-check.yaml", "check_id: x-check\n")
    assert _judge(root, base) == []


def test_unflipped_build_without_plan_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = init_repo(root, {"tests/t.py": STRONG_TEST, "scripts/x.py": "a = 1\n"})
    write(root, "scripts/x.py", "a = 2\n")
    write(root, "tests/t.py", WEAK_TEST)
    failed = _judge(root, base)
    assert any("touches no plan" in f and ASSERT_KEY in f for f in failed)


def test_unflipped_build_missing_entry_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"tests/t.py": STRONG_TEST})
    write_plan(root, PLAN, [], intent="a")
    base = commit_all(root, "plan")
    write_plan(root, PLAN, [], intent="b")
    write(root, "tests/t.py", WEAK_TEST)
    failed = _judge(root, base)
    assert any("no recorded disposition" in f for f in failed)


def test_unflipped_build_with_entry_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"tests/t.py": STRONG_TEST})
    write_plan(root, PLAN, [], intent="a")
    base = commit_all(root, "plan")
    write_plan(root, PLAN, [planned_entry()], intent="a")
    write(root, "tests/t.py", WEAK_TEST)
    assert _judge(root, base) == []


def test_added_entry_naming_no_candidate_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = init_repo(root, {"scripts/x.py": "a = 1\n"})
    write(root, "scripts/x.py", "a = 2\n")
    write_plan(root, PLAN, [planned_entry("[assertion-removed] tests/other.py::test_z")])
    failed = _judge(root, base)
    assert any("stale" in f for f in failed)


def test_plan_only_diff_may_prerecord_entries(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = init_repo(root)
    write_plan(root, PLAN, [planned_entry("[assertion-removed] tests/other.py::test_z")])
    assert _judge(root, base) == []


def test_base_entries_never_rematched(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"tests/t.py": STRONG_TEST})
    old = planned_entry("[assertion-removed] tests/old.py::test_old")
    write_plan(root, PLAN, [old], intent="a")
    base = commit_all(root, "plan")
    write_plan(root, PLAN, [old, planned_entry(cite="acceptance_criteria[1]")], intent="a")
    write(root, "tests/t.py", WEAK_TEST)
    assert _judge(root, base) == []


def test_changed_base_entry_naming_no_candidate_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    key = "[assertion-removed] tests/old.py::test_old"
    init_repo(root, {"scripts/x.py": "a = 1\n"})
    write_plan(root, PLAN, [planned_entry(key, "acceptance_criteria[0]")])
    base = commit_all(root, "plan")
    write_plan(root, PLAN, [planned_entry(key, "acceptance_criteria[1]")])
    write(root, "scripts/x.py", "a = 2\n")
    failed = _judge(root, base)
    assert any("stale" in f and key in f for f in failed)


def test_tests_only_planless_diff_is_report_only(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = tmp_path / "repo"
    base = init_repo(root, {"tests/t.py": STRONG_TEST})
    write(root, "tests/t.py", WEAK_TEST)
    registry.pop_declaration()
    assert _judge(root, base) == []
    assert "rec-4442" in capsys.readouterr().out
    decl = registry.pop_declaration()
    assert decl is not None
    assert decl.kind == "examined"
    assert decl.count == 1


def test_duplicate_entry_across_touched_plans_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"tests/t.py": STRONG_TEST})
    write_plan(root, PLAN, [], intent="a")
    write_plan(root, "docs/plans/PLAN-y.yaml", [], intent="a")
    base = commit_all(root, "plans")
    write_plan(root, PLAN, [planned_entry()], intent="a")
    write_plan(root, "docs/plans/PLAN-y.yaml", [planned_entry()], intent="a")
    write(root, "tests/t.py", WEAK_TEST)
    failed = _judge(root, base)
    assert any("has 2 recorded entries" in f for f in failed)


def test_shared_base_key_without_candidate_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root)
    shared = planned_entry("[assertion-removed] tests/old.py::test_old")
    write_plan(root, PLAN, [shared], intent="a")
    write_plan(root, "docs/plans/PLAN-y.yaml", [shared], intent="a")
    base = commit_all(root, "plans")
    write_plan(root, PLAN, [shared], intent="b")
    write_plan(root, "docs/plans/PLAN-y.yaml", [shared], intent="b")
    assert _judge(root, base) == []


def test_push_range_judges_each_merge_alone(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = tmp_path / "repo"
    base = init_repo(root, {"tests/t.py": STRONG_TEST})
    write(root, "tests/t.py", WEAK_TEST)
    first = commit_all(root, "tests-only")
    write_plan(root, PLAN, [planned_entry("[assertion-removed] tests/other.py::test_z")])
    second = commit_all(root, "plan-only")
    registry.pop_declaration()
    assert _judge(root, base, push_base=base) == []
    out = capsys.readouterr().out
    assert f"judged {first[:8]}" in out
    assert f"judged {second[:8]}" in out
    decl = registry.pop_declaration()
    assert decl is not None
    assert (decl.count, decl.unit) == (2, "first_parent_commits")
