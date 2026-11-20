"""Implement-PR leg accepts a graduated registry row that is already on main (rec-2837, rec-3600,
rec-5135). Real git history, no injected baseline reader. Helpers come from the package conftest."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks import _common

from .conftest import (
    _commit_all,
    _git,
    _init_repo,
    _step,
    _write_plan,
    _write_registry,
    validate_graduation_completeness,
)


def _row(cid: str, slug: str) -> dict:
    return {
        "check_id": cid,
        "primitive_slot": "command_exit_zero",
        "guard_target": "scripts/example.py",
        "plan_slug": slug,
        "graduated_at": "2026-11-20",
    }


def _steps(cid: str) -> list[dict]:
    return [_step(1, graduation="graduate", graduation_check_id=cid)]


def _base_repo(tmp_path: Path, slug: str, cid: str, rows: list[dict], known_red: list[str] | None = None) -> Path:
    """origin/main holds the undeclared plan and the given rows; HEAD is a non-main branch."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _write_plan(repo, slug, _steps(cid))
    _write_registry(repo, rows)
    if known_red is not None:
        path = repo / "config" / "agent" / "verification_registry" / "known_red.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"schema_version": 1, "entries": [{"check_id": k, "owner_rec": "rec-1"} for k in known_red]}),
            encoding="utf-8",
        )
    base_sha = _commit_all(repo, "base")
    _git(repo, ["update-ref", "refs/remotes/origin/main", base_sha])
    _git(repo, ["checkout", "-q", "-b", "declare"])
    return repo


def _declare(repo: Path, slug: str, cid: str, extra_file: bool = False) -> str:
    rel = _write_plan(repo, slug, _steps(cid), {"implementation_declared": True})
    if extra_file:
        (repo / "other.txt").write_text("x\n", encoding="utf-8")
    _commit_all(repo, "declare")
    return rel


def test_baseline_registry_row_satisfies_graduate_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    slug, cid = "rom-base", "rom-base-check"
    repo = _base_repo(tmp_path, slug, cid, [_row(cid, slug)])
    rel = _declare(repo, slug, cid)
    assert _common.push_context_base(root=repo) is None
    failed: list[str] = []
    validate_graduation_completeness(failed, changed_files=[rel], root=repo)
    assert failed == []
    assert "already on main" in capsys.readouterr().out


def test_implement_leg_accepts_row_graduated_by_earlier_pr_of_same_plan(tmp_path: Path) -> None:
    slug, cid = "rom-earlier", "rom-earlier-check"
    repo = _base_repo(tmp_path, slug, cid, [_row("rom-filler", "rom-filler-plan")])
    _write_registry(repo, [_row(cid, slug)])
    pr1 = _commit_all(repo, "PR-1 adds the row")
    _git(repo, ["update-ref", "refs/remotes/origin/main", pr1])
    rel = _declare(repo, slug, cid, extra_file=True)
    failed: list[str] = []
    validate_graduation_completeness(failed, changed_files=[rel, "other.txt"], root=repo)
    assert failed == []


def test_baseline_row_for_another_plan_slug_still_fails(tmp_path: Path) -> None:
    slug, cid = "rom-slug", "rom-slug-check"
    repo = _base_repo(tmp_path, slug, cid, [_row(cid, "some-other-plan")])
    rel = _declare(repo, slug, cid)
    failed: list[str] = []
    validate_graduation_completeness(failed, changed_files=[rel], root=repo)
    assert len(failed) == 1
    assert "no matching new-in-diff registry row found, and none already on main" in failed[0]
    assert "belongs to plan_slug='some-other-plan'" in failed[0]


def test_known_red_base_row_does_not_satisfy_graduate_step(tmp_path: Path) -> None:
    slug, cid = "rom-red", "rom-red-check"
    repo = _base_repo(tmp_path, slug, cid, [_row(cid, slug)], known_red=[cid])
    rel = _declare(repo, slug, cid)
    failed: list[str] = []
    validate_graduation_completeness(failed, changed_files=[rel], root=repo)
    assert len(failed) == 1
    assert "row is on known_red.yaml; repair it before declaring" in failed[0]


def test_new_in_diff_row_is_labelled_under_push_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    slug, cid = "rom-push", "rom-push-check"
    repo = _base_repo(tmp_path, slug, cid, [_row("rom-filler", "rom-filler-plan")])
    pre_merge = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    _write_registry(repo, [_row(cid, slug)])
    rel = _declare(repo, slug, cid)
    head = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    _git(repo, ["update-ref", "refs/remotes/origin/main", head])
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_BEFORE", pre_merge)
    failed: list[str] = []
    validate_graduation_completeness(failed, changed_files=[rel], root=repo)
    assert failed == []
    assert "new in this diff" in capsys.readouterr().out
