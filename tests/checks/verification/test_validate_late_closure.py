"""Both --pre checks on the late-closure route and on the unchanged normal path (fixture repos only), plus
run_differential's base_ref seam."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import verification_graduation as vg
from scripts.checks.verification import _implementation_merge as im
from scripts.checks.verification.validate_merge_step import validate_merge_step
from scripts.checks.verification.validate_verification_registry import validate_verification_registry
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


def _registry(repo) -> list[str]:
    failed: list[str] = []
    validate_verification_registry(failed, root=repo)
    return failed


def _merge(repo) -> list[str]:
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[PLAN], push_base=None)
    return failed


def test_run_differential_honours_base_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fx.git(repo, "init", "-b", "main")
    fx.write(repo, "seed.txt", "s\n")
    first = commit_all(repo, "first")
    fx.write(repo, "only-later.txt", "x\n")
    commit_all(repo, "second")
    fx.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    row = shard_row("probe", "only-later.txt")
    default = vg.run_differential(row, repo_root=repo)
    assert not default.admitted and "tautological" in default.reason
    outcome = vg.run_differential(row, repo_root=repo, base_ref=first)
    assert outcome.admitted and first in outcome.reason and "origin/main" not in outcome.reason
    resolved = vg.run_differential(shard_row("probe2", "seed.txt"), repo_root=repo, base_ref="HEAD~1")
    assert not resolved.admitted


def test_admitted_reason_names_the_resolved_sha_for_the_default_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fx.git(repo, "init", "-b", "main")
    fx.write(repo, "a.txt", "a\n")
    sha = commit_all(repo, "base")
    fx.git(repo, "update-ref", "refs/remotes/origin/main", sha)
    fx.write(repo, "b.txt", "b\n")
    commit_all(repo, "head")
    outcome = vg.run_differential(shard_row("p", "b.txt"), repo_root=repo)
    assert outcome.admitted and sha in outcome.reason


def test_late_shard_admitted_against_x_parent(tmp_path, monkeypatch, capsys):
    repo, x = build_main(tmp_path, monkeypatch)
    closure_branch(repo)
    assert _registry(repo) == []
    out = capsys.readouterr().out
    parent = fx.git(repo, "rev-parse", f"{x}^")
    assert f"admitted: {SHARD_ID}" in out and parent in out


def test_late_shard_is_tautological_without_the_route(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, message="closure\n\nResolves: rec-1\n")
    failed = _registry(repo)
    assert len(failed) == 1 and "tautological" in failed[0] and SHARD_ID in failed[0]


def test_sidecar_plan_shard_stays_on_origin_main(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, sidecar=True)
    closure_branch(repo)
    assert any("tautological" in f for f in _registry(repo))


def test_modified_shard_still_compares_against_origin_main(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    fx.git(repo, "checkout", "-b", "edit")
    fx.write(repo, "config/agent/verification_registry/entries/chk-seed.yaml", shard_text("chk-seed", "src/impl.py"))
    commit_all(repo, "modify seed shard")
    failed = _registry(repo)
    assert len(failed) == 1 and "chk-seed" in failed[0] and "tautological" in failed[0]


def test_shard_id_added_to_the_plan_by_the_closure_stays_on_origin_main(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    steps = [{"step": 1, "graduation": "graduate", "graduation_check_id": SHARD_ID}]
    steps.append({"step": 2, "graduation": "graduate", "graduation_check_id": "chk-new"})
    closure_branch(repo, shard="chk-new", plan_extra={"verification_plan": steps})
    failed = _registry(repo)
    assert any("chk-new" in f and "tautological" in f for f in failed)


def test_uncommitted_late_closure_flip_prints_pending(tmp_path, monkeypatch, capsys):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, commit=False)
    assert _registry(repo) == []
    assert "PENDING" in capsys.readouterr().out


@pytest.mark.parametrize("bundled", [BUNDLED, []])
def test_normal_path_uncommitted_flip_still_runs_origin_main_differential(tmp_path, monkeypatch, bundled):
    repo, _ = build_main(tmp_path, monkeypatch, bundled=bundled, impl_message="feat: impl with no trailer\n")
    fx.git(repo, "checkout", "-b", "feature")
    write_plan_doc(repo, plan_doc(True, bundled))
    fx.write(repo, SHARD, shard_text())
    failed = _registry(repo)
    assert len(failed) == 1 and "tautological" in failed[0]


def test_late_closure_error_fails_both_checks(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch)
    fx.write(repo, SRC, "x = 2\n")
    second = commit_all(repo, "again\n\nResolves: rec-1\n")
    fx.push_main(repo)
    closure_branch(repo)
    for failed in (_registry(repo), _merge(repo)):
        assert any(PLAN in f and second in f for f in failed)


def test_merge_step_passes_a_closure_only_branch_and_names_x(tmp_path, monkeypatch, capsys):
    repo, x = build_main(tmp_path, monkeypatch)
    closure_branch(repo)
    assert _merge(repo) == []
    assert "LATE-CLOSURE" in capsys.readouterr().out
    assert x in im.late_closure_route(PLAN, repo, "main", "HEAD").sha


@pytest.mark.parametrize(
    "kwargs",
    [
        {"message": "closure\n\nResolves: rec-1\n"},
        {"extra_files": {"src/more.py": "y = 1\n"}},
        {"plan_extra": {"intent": "edited"}},
    ],
)
def test_merge_step_fails_as_today_without_a_route(tmp_path, monkeypatch, kwargs, capsys):
    repo, _ = build_main(tmp_path, monkeypatch)
    closure_branch(repo, **kwargs)
    failed = _merge(repo)
    if kwargs.get("message"):
        assert failed == []
    else:
        assert len(failed) == 1 and "!= bundled_recommendations" in failed[0] and "add a commit whose message" in failed[0]
    assert "LATE-CLOSURE" not in capsys.readouterr().out


def test_merge_step_sidecar_plan_fails_leg_t_as_today(tmp_path, monkeypatch):
    repo, _ = build_main(tmp_path, monkeypatch, sidecar=True)
    closure_branch(repo)
    assert any("!= bundled_recommendations" in f for f in _merge(repo))
