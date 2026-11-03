"""Tests for validate_merge_step (the (T) trailer leg and the (A) approved-text leg). Fixture repos only; every
case clears GITHUB_EVENT_NAME / GITHUB_EVENT_BEFORE so a push-event CI job sees the same results."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import _common
from scripts.checks.verification.validate_merge_step import validate_merge_step
from scripts.roadmap import approved_text as at
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
PLAN = fx.plan_rel(SLUG)
BUNDLED = ["rec-1", "rec-2"]
BASE_DOC = "alpha beta gamma delta\n"
BLOCK = {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}


def _run(repo: Path, **kwargs) -> tuple[list[str], str]:
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[PLAN], push_base=None, **kwargs)
    return failed, ""


def test_trailer_in_a_branch_commit_passes(tmp_path, monkeypatch, capsys):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED)
    failed, _ = _run(repo)
    assert failed == []
    assert "PASS: 1 declared plan(s) examined" in capsys.readouterr().out


def test_trailer_only_in_pr_body_fails(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat: no trailer in any commit\n")
    failed, _ = _run(repo)
    assert (
        len(failed) == 1 and "[] != bundled_recommendations" in failed[0] and "add a commit whose message carries" in failed[0]
    )


def test_subset_fails_with_the_add_remedy(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1\n")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "add a commit" in failed[0] and "force-with-lease" not in failed[0]


def test_superset_fails_with_the_rewrite_remedy(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1, rec-2, rec-3\n")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "force-with-lease" in failed[0] and "add a commit" not in failed[0]


def test_trailer_union_over_several_commits_passes(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n\nResolves: rec-1\n")
    fx.commit_file(repo, "more.txt", "x\n", "fix-up\n\nResolves: rec-2\n")
    assert _run(repo)[0] == []


def test_uncommitted_declaration_prints_pending_and_passes(tmp_path, monkeypatch, capsys):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, declared=False, message="feat\n")
    fx.write_plan(repo, SLUG, BUNDLED, True)
    failed, _ = _run(repo)
    assert failed == []
    assert "PENDING trailer: declaration not committed" in capsys.readouterr().out


def test_push_context_skips_the_trailer_leg(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat: squashed\n")
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[PLAN], push_base="abc123")
    assert failed == []


def test_undeclared_plan_is_not_examined(tmp_path, monkeypatch, capsys):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, declared=False, message="feat\n")
    assert _run(repo)[0] == []
    assert "PASS: 0 declared plan(s) examined" in capsys.readouterr().out


def test_empty_bundle_needs_no_trailer(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
    assert _run(repo)[0] == []


def test_default_seams_derive_root_base_and_changed_files(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, BUNDLED, message="feat\n")
    failed: list[str] = []
    validate_merge_step(failed, root=repo)
    assert len(failed) == 1 and "bundled_recommendations" in failed[0]


def _sidecar_repo(tmp_path, monkeypatch, *, block=BLOCK, head_doc="alpha BETA GAMMA delta\n") -> Path:
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_plan(repo, SLUG, [], False)
    fx.write(repo, "doc.md", BASE_DOC)
    fx.write_sidecar(repo, SLUG, [block])
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "base")
    fx.push_main(repo)
    fx.branch(repo)
    fx.write_plan(repo, SLUG, [], True)
    fx.commit_file(repo, "doc.md", head_doc, "feat\n")
    return repo


def test_exact_sidecar_passes(tmp_path, monkeypatch):
    assert _run(_sidecar_repo(tmp_path, monkeypatch))[0] == []


def test_word_mismatch_fails(tmp_path, monkeypatch):
    repo = _sidecar_repo(tmp_path, monkeypatch, head_doc="alpha BETA GAMMA delta extra\n")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "approved text" in failed[0] and "doc.md" in failed[0]


@pytest.mark.parametrize("edit", ["edit", "delete"])
def test_build_may_not_edit_or_delete_the_sidecar(tmp_path, monkeypatch, edit):
    repo = _sidecar_repo(tmp_path, monkeypatch)
    if edit == "edit":
        fx.write_sidecar(repo, SLUG, [{**BLOCK, "after": "BETA GAMMA!"}])
    else:
        (repo / at.sidecar_rel(SLUG)).unlink()
    failed, _ = _run(repo)
    assert len(failed) == 1 and f"sidecar {'changed' if edit == 'edit' else 'deleted'} on the branch" in failed[0]


def test_build_may_not_add_its_own_sidecar(tmp_path, monkeypatch):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, [])
    fx.write_sidecar(repo, SLUG, [BLOCK])
    failed, _ = _run(repo)
    assert len(failed) == 1 and "sidecar added on the branch" in failed[0]


def test_unparseable_sidecar_at_base_fails_loud(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_plan(repo, SLUG, [], False)
    fx.write(repo, at.sidecar_rel(SLUG), "schema_version: [1\n")
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "base")
    fx.push_main(repo)
    fx.branch(repo)
    fx.write_plan(repo, SLUG, [], True)
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "feat")
    failed, _ = _run(repo)
    assert len(failed) == 1 and "sidecar does not parse" in failed[0]


def test_real_tree_run_passes():
    if not _common.origin_main_reachable(_common.ROOT):
        pytest.skip("env: origin/main not reachable")
    failed: list[str] = []
    validate_merge_step(failed)
    assert failed == []
