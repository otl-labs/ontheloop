"""Both --pre checks on the late-closure route and on the unchanged normal path (fixture repos only), plus
run_differential's base_ref seam."""

from __future__ import annotations

import pytest

from scripts import verification_graduation as vg
from scripts.checks.verification import _implementation_merge as im
from scripts.checks.verification.validate_merge_step import validate_merge_step
from scripts.checks.verification.validate_verification_registry import validate_verification_registry
from tests.fixtures import late_closure_repos as lc
from tests.fixtures import merge_step_repos as fx


def _registry(repo) -> list[str]:
    failed: list[str] = []
    validate_verification_registry(failed, root=repo)
    return failed


def _merge(repo) -> list[str]:
    failed: list[str] = []
    validate_merge_step(failed, root=repo, base="main", changed=[lc.PLAN], push_base=None)
    return failed


def test_run_differential_honours_base_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fx.git(repo, "init", "-b", "main")
    fx.write(repo, "seed.txt", "s\n")
    first = lc.commit_all(repo, "first")
    fx.write(repo, "only-later.txt", "x\n")
    lc.commit_all(repo, "second")
    fx.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    row = lc.shard_row("probe", "only-later.txt")
    default = vg.run_differential(row, repo_root=repo)
    assert not default.admitted and "tautological" in default.reason
    outcome = vg.run_differential(row, repo_root=repo, base_ref=first)
    assert outcome.admitted and first in outcome.reason and "origin/main" not in outcome.reason
    resolved = vg.run_differential(lc.shard_row("probe2", "seed.txt"), repo_root=repo, base_ref="HEAD~1")
    assert not resolved.admitted


def test_admitted_reason_names_the_resolved_sha_for_the_default_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fx.git(repo, "init", "-b", "main")
    fx.write(repo, "a.txt", "a\n")
    sha = lc.commit_all(repo, "base")
    fx.git(repo, "update-ref", "refs/remotes/origin/main", sha)
    fx.write(repo, "b.txt", "b\n")
    lc.commit_all(repo, "head")
    outcome = vg.run_differential(lc.shard_row("p", "b.txt"), repo_root=repo)
    assert outcome.admitted and sha in outcome.reason


def test_late_shard_admitted_against_x_parent(tmp_path, monkeypatch, capsys):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo)
    assert _registry(repo) == []
    out = capsys.readouterr().out
    parent = fx.git(repo, "rev-parse", f"{x}^")
    assert f"admitted: {lc.SHARD_ID}" in out and parent in out


def test_late_shard_is_tautological_without_the_route(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, message="closure\n\nResolves: rec-1\n")
    failed = _registry(repo)
    assert len(failed) == 1 and "tautological" in failed[0] and lc.SHARD_ID in failed[0]


def test_sidecar_plan_shard_stays_on_origin_main(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo)
    assert any("tautological" in f for f in _registry(repo))


def test_modified_shard_still_compares_against_origin_main(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    fx.git(repo, "checkout", "-b", "edit")
    fx.write(repo, "config/agent/verification_registry/entries/chk-seed.yaml", lc.shard_text("chk-seed", "src/impl.py"))
    lc.commit_all(repo, "modify seed shard")
    failed = _registry(repo)
    assert len(failed) == 1 and "chk-seed" in failed[0] and "tautological" in failed[0]


def test_shard_id_added_to_the_plan_by_the_closure_stays_on_origin_main(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    steps = [{"step": 1, "graduation": "graduate", "graduation_check_id": lc.SHARD_ID}]
    steps.append({"step": 2, "graduation": "graduate", "graduation_check_id": "chk-new"})
    lc.closure_branch(repo, shard="chk-new", plan_extra={"verification_plan": steps})
    failed = _registry(repo)
    assert any("chk-new" in f and "tautological" in f for f in failed)


def test_uncommitted_late_closure_flip_prints_pending(tmp_path, monkeypatch, capsys):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, commit=False)
    assert _registry(repo) == []
    assert "PENDING" in capsys.readouterr().out


@pytest.mark.parametrize("bundled", [lc.BUNDLED, []])
def test_normal_path_uncommitted_flip_still_runs_origin_main_differential(tmp_path, monkeypatch, bundled):
    repo, _ = lc.build_main(tmp_path, monkeypatch, bundled=bundled, impl_message="feat: impl with no trailer\n")
    fx.git(repo, "checkout", "-b", "feature")
    lc.write_plan_doc(repo, lc.plan_doc(True, bundled))
    fx.write(repo, lc.SHARD, lc.shard_text())
    failed = _registry(repo)
    assert len(failed) == 1 and "tautological" in failed[0]


def test_late_closure_error_fails_both_checks(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    fx.write(repo, lc.SRC, "x = 2\n")
    second = lc.commit_all(repo, "again\n\nResolves: rec-1\n")
    fx.push_main(repo)
    lc.closure_branch(repo)
    for failed in (_registry(repo), _merge(repo)):
        assert any(lc.PLAN in f and second in f for f in failed)


def test_merge_step_passes_a_closure_only_branch_and_names_x(tmp_path, monkeypatch, capsys):
    repo, x = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo)
    assert _merge(repo) == []
    assert "LATE-CLOSURE" in capsys.readouterr().out
    assert x in im.late_closure_route(lc.PLAN, repo, "main", "HEAD").sha


@pytest.mark.parametrize(
    "kwargs",
    [
        {"message": "closure\n\nResolves: rec-1\n"},
        {"extra_files": {"src/more.py": "y = 1\n"}},
        {"plan_extra": {"intent": "edited"}},
    ],
)
def test_merge_step_fails_as_today_without_a_route(tmp_path, monkeypatch, kwargs, capsys):
    repo, _ = lc.build_main(tmp_path, monkeypatch)
    lc.closure_branch(repo, **kwargs)
    failed = _merge(repo)
    if kwargs.get("message"):
        assert failed == []
    else:
        assert len(failed) == 1 and "!= bundled_recommendations" in failed[0] and "add a commit whose message" in failed[0]
    assert "LATE-CLOSURE" not in capsys.readouterr().out


def test_merge_step_sidecar_plan_fails_leg_t_as_today(tmp_path, monkeypatch):
    repo, _ = lc.build_main(tmp_path, monkeypatch, sidecar=True)
    lc.closure_branch(repo)
    assert any("!= bundled_recommendations" in f for f in _merge(repo))
