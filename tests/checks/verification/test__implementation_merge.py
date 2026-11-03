"""Fixture-repo tests for the late-closure resolver (scripts/checks/verification/_implementation_merge.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.verification import _implementation_merge as im
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
PLAN = fx.plan_rel(SLUG)
BUNDLED = ["rec-1"]
SHARD_ID = "chk-a"
SHARD = f"config/agent/verification_registry/entries/{SHARD_ID}.yaml"
SRC = "src/impl.py"


def plan_doc(declared: bool | None, bundled: list[str] | None = None, extra: dict | None = None) -> dict:
    doc = {
        "slug": SLUG,
        "bundled_recommendations": BUNDLED if bundled is None else bundled,
        "scope": [{"file": SRC}, {"file": PLAN}],
        "verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": SHARD_ID}],
    }
    if declared is not None:
        doc["implementation_declared"] = declared
    return doc | (extra or {})


def write_plan_doc(repo: Path, doc: dict) -> None:
    fx.write(repo, PLAN, yaml.safe_dump(doc, sort_keys=False))


def shard_row(check_id: str = SHARD_ID, path: str = SRC, slug: str = SLUG) -> dict:
    return {
        "check_id": check_id,
        "primitive_slot": "file_presence",
        "guard_target": path,
        "plan_slug": slug,
        "graduated_at": "2026-11-03",
        "check_spec": {"path": path, "mode": "exists"},
    }


def shard_text(check_id: str = SHARD_ID, path: str = SRC) -> str:
    return yaml.safe_dump(shard_row(check_id, path), sort_keys=False)


def commit_all(repo: Path, message: str) -> str:
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", message)
    return fx.git(repo, "rev-parse", "HEAD")


def build_main(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
    *,
    bundled: list[str] | None = None,
    impl_message: str | None = "feat\n\nResolves: rec-1\n",
    impl_file: str = SRC,
    impl_declared: bool | None = None,
    sidecar: bool = False,
) -> tuple[Path, str | None]:
    """Main = seed, plan commit (undeclared), implementation commit X. Returns (repo, X sha or None)."""
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    write_plan_doc(repo, plan_doc(False, bundled))
    fx.write(repo, "config/agent/verification_registry/entries/chk-seed.yaml", shard_text("chk-seed", "README.md"))
    if sidecar:
        fx.write_sidecar(repo, SLUG, [{"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}])
    commit_all(repo, "plan")
    sha = None
    if impl_message is not None:
        fx.write(repo, impl_file, "x = 1\n")
        if impl_declared is not None:
            write_plan_doc(repo, plan_doc(impl_declared, bundled))
        sha = commit_all(repo, impl_message)
    fx.push_main(repo)
    return repo, sha


def closure_branch(
    repo: Path,
    *,
    shard: str = SHARD_ID,
    extra_files: dict[str, str] | None = None,
    plan_extra: dict | None = None,
    message: str = "closure\n",
    bundled: list[str] | None = None,
    commit: bool = True,
) -> str | None:
    fx.branch(repo, "closure")
    write_plan_doc(repo, plan_doc(True, bundled, plan_extra))
    fx.write(repo, f"config/agent/verification_registry/entries/{shard}.yaml", shard_text(shard))
    for rel, text in (extra_files or {}).items():
        fx.write(repo, rel, text)
    return commit_all(repo, message) if commit else None


def _find(repo, ref="main"):
    return im.find_implementation_merge(PLAN, repo, ref)


def _route(repo, **kwargs):
    return im.late_closure_route(PLAN, repo, "main", head=kwargs.pop("head", "HEAD"))


def test_unique_anchor_is_found(tmp_path, monkeypatch):
    repo, x = build_main(tmp_path, monkeypatch)
    found = _find(repo)
    assert found is not None and found.sha == x and found.parent == fx.git(repo, "rev-parse", f"{x}^")
    assert len(found.sha) == 40 and len(found.parent) == 40


def test_zero_anchors_is_none(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, impl_message="feat: no trailer\n")
    assert _find(repo) is None


def test_two_anchors_raise_naming_plan_and_commits(tmp_path, monkeypatch):
    repo, x = build_main(tmp_path, monkeypatch)
    fx.write(repo, SRC, "x = 2\n")
    y = commit_all(repo, "again\n\nResolves: rec-1\n")
    with pytest.raises(im.LateClosureError) as exc:
        _find(repo)
    assert PLAN in str(exc.value) and x in str(exc.value) and y in str(exc.value)


def test_plan_only_anchor_diff_is_none(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, impl_file="docs/plans/other.txt")
    assert _find(repo) is None


def test_anchor_diff_outside_scope_is_none(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, impl_file="src/unrelated.py")
    assert _find(repo) is None


def test_plan_declared_at_the_anchor_is_none(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, impl_declared=True)
    assert _find(repo) is None


def test_empty_bundled_is_none_despite_trailerless_scope_commits(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, bundled=[], impl_message="feat: no trailer\n")
    fx.write(repo, SRC, "x = 3\n")
    commit_all(repo, "more scope work")
    assert _find(repo) is None


def test_trailer_set_unequal_to_bundled_is_none(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, impl_message="feat\n\nResolves: rec-1, rec-2\n")
    assert _find(repo) is None


def test_trailer_before_the_plan_was_added_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write(repo, SRC, "x = 1\n")
    commit_all(repo, "feat\n\nResolves: rec-1\n")
    write_plan_doc(repo, plan_doc(False))
    commit_all(repo, "plan")
    assert _find(repo, "HEAD") is None


def test_squash_adding_plan_and_trailer_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    write_plan_doc(repo, plan_doc(False))
    fx.write(repo, SRC, "x = 1\n")
    commit_all(repo, "feat\n\nResolves: rec-1\n")
    assert _find(repo, "HEAD") is None


def test_absent_plan_is_none(tmp_path, monkeypatch):
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    assert _find(repo, "HEAD") is None


def test_non_git_tree_reads_as_no_route(tmp_path):
    assert im.find_implementation_merge(PLAN, tmp_path, "main") is None
    assert im.late_closure_route(PLAN, tmp_path, "main", head="HEAD") is None
    assert im.late_closure_route(PLAN, tmp_path / "missing", "main", head=None) is None


def test_closure_only_branch_routes_to_x(tmp_path, monkeypatch):
    repo, x = build_main(tmp_path, monkeypatch)
    closure_branch(repo)
    found = _route(repo)
    assert found is not None and found.sha == x


def test_working_tree_mode_routes_with_untracked_shard(tmp_path, monkeypatch):
    repo, x = build_main(tmp_path, monkeypatch)
    closure_branch(repo, commit=False)
    found = im.late_closure_route(PLAN, repo, "main", head=None)
    assert found is not None and found.sha == x


def test_branch_trailer_gives_no_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, message="closure\n\nResolves: rec-1\n")
    assert _route(repo) is None


def test_extra_changed_file_gives_no_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, extra_files={"src/more.py": "y = 1\n"})
    assert _route(repo) is None


def test_foreign_shard_id_gives_no_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, shard="chk-foreign")
    assert _route(repo) is None


def test_sidecar_plan_gets_no_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, sidecar=True)
    closure_branch(repo)
    assert _route(repo) is None


@pytest.mark.parametrize(
    "plan_extra",
    [
        {"verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": SHARD_ID}, {"step": 2}]},
        {"verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": "chk-edited"}]},
        {"bundled_recommendations": ["rec-1", "rec-9"]},
        {"intent": "edited"},
    ],
)
def test_any_other_plan_edit_gives_no_route(tmp_path, monkeypatch, plan_extra):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, plan_extra=plan_extra)
    assert _route(repo) is None


def test_empty_bundled_gives_no_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, bundled=[])
    closure_branch(repo, bundled=[])
    assert _route(repo) is None


def test_plan_already_declared_at_base_gives_no_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    write_plan_doc(repo, plan_doc(True))
    commit_all(repo, "declare on main")
    closure_branch(repo, message="shard only\n")
    assert _route(repo) is None


def test_two_candidates_raise_only_for_a_closure(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    fx.write(repo, SRC, "x = 2\n")
    commit_all(repo, "again\n\nResolves: rec-1\n")
    closure_branch(repo)
    with pytest.raises(im.LateClosureError):
        _route(repo)
