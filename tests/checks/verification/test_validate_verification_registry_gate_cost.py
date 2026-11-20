"""Gate-cost behaviour of validate_verification_registry (rec-5372): one baseline read, one shared base worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts import verification_graduation as vg
from scripts.checks.verification import _graduation_git
from scripts.checks.verification import validate_verification_registry as vvr

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
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _write(root, _row("live-one"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))
    for cid in ("new-a", "new-b", "new-c"):
        _write(root, _row(cid))
    return root


def _run(root: Path, fake) -> list[str]:
    failed: list[str] = []
    with patch("scripts.verification_graduation.run_differential", side_effect=fake):
        vvr.validate_verification_registry(failed, root=root)
    return failed


class TestWorktreeReuse:
    def test_added_entries_share_one_worktree(self, repo: Path) -> None:
        added: list[str] = []
        real_add = _graduation_git._add_worktree

        def counting_add(ref, root):
            added.append(ref)
            return real_add(ref, root)

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            worktree_for(base_ref)
            return ADMITTED

        with patch.object(_graduation_git, "_add_worktree", counting_add):
            failed = _run(repo, fake)
        assert failed == []
        assert len(added) == 1

    def test_baseline_read_once(self, repo: Path) -> None:
        real = vg.entries_at_ref
        calls: list[str] = []

        def counting(ref, repo_root=None):
            calls.append(ref)
            return real(ref, repo_root=repo_root)

        with patch("scripts.verification_graduation.entries_at_ref", counting):
            _run(repo, lambda *a, **k: ADMITTED)
        assert calls == ["origin/main"]

    def test_untracked_file_from_one_leg_is_gone_before_next(self, repo: Path) -> None:
        seen_junk: list[bool] = []

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            wt = worktree_for(base_ref)
            seen_junk.append((wt / "junk.txt").exists())
            (wt / "junk.txt").write_text("x", encoding="utf-8")
            return ADMITTED

        _run(repo, fake)
        assert seen_junk == [False, False, False]

    def test_failed_restore_is_a_failure_not_an_admission(self, repo: Path) -> None:
        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            worktree_for(base_ref)
            return ADMITTED

        real = _graduation_git.run_git

        def failing(args, cwd):
            if args[:1] == ["checkout"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
            return real(args, cwd)

        with patch.object(_graduation_git, "run_git", failing):
            failed = _run(repo, fake)
        assert any("error --" in f and "restore" in f for f in failed), failed

    def test_candidates_run_one_at_a_time(self, repo: Path) -> None:
        open_now = 0
        peak = 0

        def fake(row, repo_root=None, base_ref="origin/main", worktree_for=None):
            nonlocal open_now, peak
            open_now += 1
            peak = max(peak, open_now)
            worktree_for(base_ref)
            open_now -= 1
            return ADMITTED

        _run(repo, fake)
        assert peak == 1

    def test_unresolved_baseline_calls_helpers_without_keyword(self, repo: Path) -> None:
        calls: list[dict] = []

        def spy(entries, root=None, **kw):
            calls.append(kw)
            return []

        with (
            patch("scripts.verification_graduation.entries_at_ref", return_value=None),
            patch.object(vvr, "_added_entries", spy),
            patch.object(vvr, "_modified_entries", spy),
        ):
            vvr.validate_verification_registry([], root=repo)
        assert calls == [{}, {}]

    def test_head_shard_read_parses_through_parse_yaml(self, repo: Path) -> None:
        with patch.object(vvr.contract_rules, "parse_yaml", wraps=vvr.contract_rules.parse_yaml) as spy:
            _run(repo, lambda *a, **k: ADMITTED)
        assert spy.call_count >= 4
