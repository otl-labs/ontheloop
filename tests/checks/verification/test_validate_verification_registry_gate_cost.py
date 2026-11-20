"""Gate-cost leg of validate_verification_registry (rec-5372): one shared base worktree, one baseline read."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import verification_graduation as vg
from scripts.checks.verification import _graduation_git as gg
from scripts.checks.verification import validate_verification_registry as mod

ADMITTED = vg.DifferentialOutcome(admitted=True, reason="admitted -- test double")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _row(check_id: str) -> dict:
    return {
        "check_id": check_id,
        "primitive_slot": "command_exit_zero",
        "guard_target": "scripts/foo.py",
        "plan_slug": "p",
        "graduated_at": "2026-11-05",
        "check_spec": {"command": ["bash", "-c", "true"]},
    }


def _write(repo: Path, row: dict) -> None:
    path = repo / vg.REGISTRY_ENTRIES_REL / f"{row['check_id']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(row, sort_keys=False), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_BEFORE", raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    _write(root, _row("live-one"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))
    for cid in ("new-a", "new-b", "new-c"):
        _write(root, _row(cid))
    return root


def _validate(root: Path, fake) -> list[str]:
    failed: list[str] = []
    with mock.patch.object(vg, "run_differential", fake):
        mod.validate_verification_registry(failed, root=root)
    return failed


class TestWorktreeReuse:
    def test_added_entries_share_one_worktree(self, repo: Path) -> None:
        adds: list[list[str]] = []
        real = gg.run_git

        def spy(args, cwd):
            if args[:2] == ["worktree", "add"]:
                adds.append(args)
            return real(args, cwd)

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            assert worktree_for is not None
            worktree_for(base_ref)
            return ADMITTED

        with mock.patch.object(gg, "run_git", spy):
            failed = _validate(repo, fake)
        assert failed == []
        assert len(adds) == 1

    def test_baseline_read_once(self, repo: Path) -> None:
        with mock.patch.object(vg, "entries_at_ref", wraps=vg.entries_at_ref) as spy:
            _validate(repo, lambda *a, **k: ADMITTED)
        assert spy.call_count == 1

    def test_untracked_file_from_one_leg_is_gone_before_the_next(self, repo: Path) -> None:
        seen: list[bool] = []

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            path = worktree_for(base_ref)
            seen.append((path / "planted.txt").exists())
            (path / "planted.txt").write_text("x", encoding="utf-8")
            return ADMITTED

        assert _validate(repo, fake) == []
        assert seen == [False, False, False]

    def test_failed_restore_is_a_registry_failure_not_an_admission(self, repo: Path) -> None:
        calls = {"n": 0}
        real = gg.run_git

        def fail_checkout(args, cwd):
            if args[:2] == ["checkout", "-f"]:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="restore boom")
            return real(args, cwd)

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            calls["n"] += 1
            worktree_for(base_ref)
            return ADMITTED

        with mock.patch.object(gg, "run_git", fail_checkout):
            failed = _validate(repo, fake)
        assert calls["n"] == 3
        assert sum("differential" in f and "error" in f and "restore boom" in f for f in failed) == 2

    def test_candidates_run_one_at_a_time(self, repo: Path) -> None:
        open_now: list[int] = [0]
        peak: list[int] = [0]

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            open_now[0] += 1
            peak[0] = max(peak[0], open_now[0])
            worktree_for(base_ref)
            open_now[0] -= 1
            return ADMITTED

        assert _validate(repo, fake) == []
        assert peak[0] == 1

    def test_unresolved_baseline_calls_helpers_without_the_keyword(self, repo: Path) -> None:
        with (
            mock.patch.object(vg, "entries_at_ref", return_value=None),
            mock.patch.object(mod, "_added_entries", return_value=[]) as added,
            mock.patch.object(mod, "_modified_entries", return_value=[]) as modified,
        ):
            _validate(repo, lambda *a, **k: ADMITTED)
        assert added.call_args.kwargs == {} and modified.call_args.kwargs == {}

    def test_head_shard_read_parses_through_parse_yaml(self, repo: Path) -> None:
        with mock.patch.object(mod, "parse_yaml", wraps=mod.parse_yaml) as spy:
            _validate(repo, lambda *a, **k: ADMITTED)
        assert spy.call_count == 4

    def test_validation_still_passes_for_a_clean_added_set(self, repo: Path) -> None:
        assert _validate(repo, lambda *a, **k: ADMITTED) == []
