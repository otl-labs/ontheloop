"""Fixture-repo tests for the late-closure resolver (scripts/checks/verification/_implementation_merge.py)."""

from __future__ import annotations

import pytest

from scripts.checks.verification import _implementation_merge as im
from tests.fixtures import late_closure_repos as lc
from tests.fixtures import merge_step_repos as fx


def _find(repo, ref="main"):
    return im.find_implementation_merge(lc.PLAN, repo, ref)


def _route(repo, **kwargs):
    return im.late_closure_route(lc.PLAN, repo, "main", head=kwargs.pop("head", "HEAD"))


def test_unique_anchor_is_found(tmp_path, monkeypatch):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    found = _find(repo)
    assert found is not None and found.sha == x and found.parent == fx.git(repo, "rev-parse", f"{x}^")
    assert len(found.sha) == 40 and len(found.parent) == 40


def test_zero_anchors_is_none(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, impl_message="feat: no trailer\n")
    assert _find(repo) is None


def test_two_anchors_raise_naming_plan_and_commits(tmp_path, monkeypatch):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    fx.write(repo, lc.SRC, "x = 2\n")
    y = lc.commit_all(repo, "again\n\nResolves: rec-1\n")
    with pytest.raises(im.LateClosureError) as exc:
        _find(repo)
    assert lc.PLAN in str(exc.value) and x in str(exc.value) and y in str(exc.value)


def test_plan_only_anchor_diff_is_none(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, impl_file="docs/plans/other.txt")
    assert _find(repo) is None


def test_anchor_diff_outside_scope_is_none(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, impl_file="src/unrelated.py")
    assert _find(repo) is None


def test_plan_declared_at_the_anchor_is_none(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, impl_declared=True)
    assert _find(repo) is None


def test_empty_bundled_is_none_despite_trailerless_scope_commits(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, bundled=[], impl_message="feat: no trailer\n")
    fx.write(repo, lc.SRC, "x = 3\n")
    lc.commit_all(repo, "more scope work")
    assert _find(repo) is None


def test_trailer_set_unequal_to_bundled_is_none(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, impl_message="feat\n\nResolves: rec-1, rec-2\n")
    assert _find(repo) is None


def test_trailer_before_the_plan_was_added_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write(repo, lc.SRC, "x = 1\n")
    lc.commit_all(repo, "feat\n\nResolves: rec-1\n")
    lc.write_plan_doc(repo, lc.plan_doc(False))
    lc.commit_all(repo, "plan")
    assert _find(repo, "HEAD") is None


def test_squash_adding_plan_and_trailer_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    lc.write_plan_doc(repo, lc.plan_doc(False))
    fx.write(repo, lc.SRC, "x = 1\n")
    lc.commit_all(repo, "feat\n\nResolves: rec-1\n")
    assert _find(repo, "HEAD") is None


def test_absent_plan_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    assert _find(repo, "HEAD") is None


def test_non_git_tree_reads_as_no_route(tmp_path):
    assert im.find_implementation_merge(lc.PLAN, tmp_path, "main") is None
    assert im.late_closure_route(lc.PLAN, tmp_path, "main", head="HEAD") is None
    assert im.late_closure_route(lc.PLAN, tmp_path / "missing", "main", head=None) is None


def test_closure_only_branch_routes_to_x(tmp_path, monkeypatch):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo)
    found = _route(repo)
    assert found is not None and found.sha == x


def test_working_tree_mode_routes_with_untracked_shard(tmp_path, monkeypatch):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, commit=False)
    found = im.late_closure_route(lc.PLAN, repo, "main", head=None)
    assert found is not None and found.sha == x


def test_branch_trailer_gives_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, message="closure\n\nResolves: rec-1\n")
    assert _route(repo) is None


def test_extra_changed_file_gives_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, extra_files={"src/more.py": "y = 1\n"})
    assert _route(repo) is None


def test_foreign_shard_id_gives_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, shard="chk-foreign")
    assert _route(repo) is None


def test_sidecar_plan_gets_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo)
    assert _route(repo) is None


@pytest.mark.parametrize(
    "plan_extra",
    [
        {"verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": lc.SHARD_ID}, {"step": 2}]},
        {"verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": "chk-edited"}]},
        {"bundled_recommendations": ["rec-1", "rec-9"]},
        {"intent": "edited"},
    ],
)
def test_any_other_plan_edit_gives_no_route(tmp_path, monkeypatch, plan_extra):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, plan_extra=plan_extra)
    assert _route(repo) is None


def test_empty_bundled_gives_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, bundled=[])
    lc.closure_branch(repo, bundled=[])
    assert _route(repo) is None


def test_plan_already_declared_at_base_gives_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.write_plan_doc(repo, lc.plan_doc(True))
    lc.commit_all(repo, "declare on main")
    lc.closure_branch(repo, message="shard only\n")
    assert _route(repo) is None


def test_two_candidates_raise_only_for_a_closure(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    fx.write(repo, lc.SRC, "x = 2\n")
    lc.commit_all(repo, "again\n\nResolves: rec-1\n")
    lc.closure_branch(repo)
    with pytest.raises(im.LateClosureError):
        _route(repo)


def test_unparseable_plan_at_ref_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write(repo, lc.PLAN, "key: [unclosed\n")
    lc.commit_all(repo, "broken plan")
    assert _find(repo, "HEAD") is None


def test_no_adding_commit_reads_as_none(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    real = im._git
    monkeypatch.setattr(im, "_git", lambda root, *a: "" if "--diff-filter=A" in a else real(root, *a))
    assert _find(repo) is None


def test_working_tree_git_failure_reads_as_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, commit=False)
    real = im._git
    monkeypatch.setattr(im, "_git", lambda root, *a: None if a[0] == "ls-files" else real(root, *a))
    assert im.late_closure_route(lc.PLAN, repo, "main", head=None) is None


def test_head_plan_not_declared_true_gives_no_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, plan_extra={"implementation_declared": "yes"})
    assert _route(repo) is None
