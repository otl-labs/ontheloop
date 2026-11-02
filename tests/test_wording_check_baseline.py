"""Tests for scripts/roadmap/wording_check_baseline.py: drift admission and the serial detect-secrets settle."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.roadmap import wording_check_baseline as wb

_FILE_A = "docs/contracts/a.yaml"
_FILE_B = "docs/contracts/b.yaml"
_SIDE = {_FILE_A}


def _finding(line: int, tag: str = "1") -> dict[str, Any]:
    return {
        "type": "Secret Keyword",
        "filename": _FILE_A,
        "hashed_secret": tag * 40,
        "is_verified": False,
        "line_number": line,
    }


def _baseline(a: list[dict] | None = None, b: list[dict] | None = None, **top: Any) -> dict[str, Any]:
    results: dict[str, list] = {}
    if a is not None:
        results[_FILE_A] = a
    if b is not None:
        results[_FILE_B] = b
    return {"version": "1", "plugins_used": [], "results": results, "generated_at": "t0", **top}


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def _drift(old: dict, new: dict) -> list[str]:
    return wb.drift(_dump(old), _dump(new), _SIDE)


class TestDrift:
    def test_a_line_shift_and_a_new_generated_at_are_admitted(self) -> None:
        old = _baseline([_finding(3)])
        new = _baseline([_finding(4)], generated_at="t1")
        assert _drift(old, new) == []

    def test_a_dropped_finding_and_a_dropped_file_entry_are_admitted(self) -> None:
        assert _drift(_baseline([_finding(3), _finding(5, "2")]), _baseline([_finding(3)])) == []
        assert _drift(_baseline([_finding(3)]), _baseline()) == []

    def test_an_added_finding_is_refused(self) -> None:
        refused = _drift(_baseline([_finding(3)]), _baseline([_finding(3), _finding(9, "2")]))
        assert len(refused) == 1 and "gains a finding" in refused[0]

    def test_a_changed_hash_is_an_added_finding(self) -> None:
        assert _drift(_baseline([_finding(3)]), _baseline([_finding(3, "2")]))

    def test_another_top_level_key_is_refused(self) -> None:
        refused = _drift(_baseline([_finding(3)]), _baseline([_finding(3)], version="2"))
        assert refused == ["the top-level key 'version' differs"]

    def test_a_non_sidecar_file_entry_change_is_refused(self) -> None:
        old = _baseline(b=[_finding(3)])
        assert "not a sidecar file" in _drift(old, _baseline(b=[_finding(4)]))[0]
        assert "not a sidecar file" in _drift(old, _baseline())[0]

    def test_a_new_file_entry_is_refused(self) -> None:
        assert _drift(_baseline(), _baseline(a=[_finding(1)])) == [f"{_FILE_A} is a new file entry"]

    def test_unparseable_json_is_a_refusal_not_an_exception(self) -> None:
        refused = wb.drift("{broken", _dump(_baseline()), _SIDE)
        assert len(refused) == 1 and "HEAD baseline is not valid JSON" in refused[0]
        assert "no results mapping" in wb.drift(_dump(_baseline()), "[]", _SIDE)[0]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "t"
    (root / "docs/contracts").mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    (root / _FILE_A).write_text("one\n", encoding="utf-8")
    (root / wb.BASELINE).write_text(_dump(_baseline([_finding(1)])), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


class HookRunner:
    """Stands in for the pinned hook: each call may rewrite the baseline, then returns the scripted exit code."""

    def __init__(self, steps: list[tuple[int, dict | None]], raises: BaseException | None = None) -> None:
        self.steps, self.raises = list(steps), raises
        self.calls: list[tuple[list[str], Path, int]] = []

    def __call__(self, cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, cwd, timeout))
        if self.raises is not None:
            raise self.raises
        rc, rewrite = self.steps.pop(0)
        if rewrite is not None:
            (cwd / wb.BASELINE).write_text(_dump(rewrite), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, rc, "hook output line\n", "")


def _settle(tree: Path, runner: Callable, changed: set[str] | None = None) -> str | None:
    return wb.settle(tree, changed or {_FILE_A, wb.BASELINE, ".venv"}, _SIDE, runner, 120)


class TestSettle:
    def test_the_serial_hook_command_names_the_changed_files_only(self, tree: Path) -> None:
        (tree / ".venv").symlink_to(tree, target_is_directory=True)
        runner = HookRunner([(0, None)])
        assert _settle(tree, runner, {_FILE_A, wb.BASELINE, ".venv", "gone.yaml"}) is None
        cmd, cwd, timeout = runner.calls[0]
        assert cmd[:7] == [
            "env",
            "PRE_COMMIT_NO_CONCURRENCY=1",
            "bin/venv-python",
            "-m",
            "pre_commit",
            "run",
            "detect-secrets",
        ]
        assert cmd[7:] == ["--files", _FILE_A]
        assert (cwd, timeout) == (tree, 120)

    def test_a_clean_hook_run_settles_nothing(self, tree: Path) -> None:
        assert _settle(tree, HookRunner([(0, None)])) is None
        assert _git(tree, "status", "--porcelain", "--untracked-files=no").strip() == ""

    def test_a_rejection_with_the_baseline_untouched_carries_the_pragma_remedy(self, tree: Path) -> None:
        reason = _settle(tree, HookRunner([(1, None)]))
        assert reason is not None and "hook rejects the placed words" in reason and "pragma: allowlist secret" in reason
        assert "hook output line" in reason

    def test_an_admitted_rewrite_is_staged_and_confirmed(self, tree: Path) -> None:
        runner = HookRunner([(1, _baseline([_finding(2)], generated_at="t1")), (0, None)])
        assert _settle(tree, runner) is None
        assert len(runner.calls) == 2
        assert wb.BASELINE in _git(tree, "diff", "--cached", "--name-only").split()
        assert json.loads((tree / wb.BASELINE).read_text(encoding="utf-8"))["results"][_FILE_A][0]["line_number"] == 2

    def test_an_admitted_rewrite_whose_confirming_run_is_red_did_not_settle(self, tree: Path) -> None:
        runner = HookRunner([(1, _baseline([_finding(2)])), (1, None)])
        reason = _settle(tree, runner)
        assert reason is not None and "did not settle" in reason and "pragma: allowlist secret" in reason

    def test_a_refused_rewrite_is_left_unstaged(self, tree: Path) -> None:
        runner = HookRunner([(1, _baseline([_finding(2), _finding(7, "2")]))])
        reason = _settle(tree, runner)
        assert reason is not None and "refused" in reason and "gains a finding" in reason
        assert len(runner.calls) == 1
        assert _git(tree, "diff", "--cached", "--name-only").strip() == ""

    def test_a_planner_edited_baseline_is_restored_first(self, tree: Path) -> None:
        (tree / wb.BASELINE).write_text(_dump(_baseline([_finding(1), _finding(9, "3")])), encoding="utf-8")
        _git(tree, "add", wb.BASELINE)
        assert _settle(tree, HookRunner([(0, None)])) is None
        assert (tree / wb.BASELINE).read_text(encoding="utf-8") == _git(tree, "show", f"HEAD:{wb.BASELINE}")
        assert _git(tree, "diff", "--cached", "--name-only").strip() == ""

    def test_no_baseline_at_head_settles_without_a_hook_run(self, tree: Path) -> None:
        _git(tree, "rm", "-q", "-f", wb.BASELINE)
        _git(tree, "commit", "-q", "-m", "drop")
        runner = HookRunner([])
        assert _settle(tree, runner) is None and runner.calls == []

    def test_a_hook_timeout_names_the_cold_environment_remedy(self, tree: Path) -> None:
        reason = _settle(tree, HookRunner([], raises=subprocess.TimeoutExpired("x", 1)))
        assert reason is not None and "timed out after 120 s" in reason and "install-hooks" in reason

    def test_a_hook_that_cannot_start_is_a_reason(self, tree: Path) -> None:
        reason = _settle(tree, HookRunner([], raises=FileNotFoundError("no venv")))
        assert reason is not None and "could not start" in reason

    def test_a_confirming_run_that_cannot_start_is_a_reason(self, tree: Path) -> None:
        class Second(HookRunner):
            def __call__(self, cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
                if self.calls:
                    self.raises = subprocess.TimeoutExpired("x", 1)
                return super().__call__(cmd, cwd, timeout)

        reason = _settle(tree, Second([(1, _baseline([_finding(2)]))]))
        assert reason is not None and "timed out" in reason
