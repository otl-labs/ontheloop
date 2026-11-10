"""Tests for scripts/ci/push_scope.py: only a non-empty diff confined to docs/plans/ is plans-only."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.checks import _common
from scripts.ci import push_scope
from scripts.ops_portal import trailer_closure_gate

ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _commit(tmp_path, {"docs/plans/a.md": "a", "src/x.py": "x", "docs/plans/approved/s.yaml": "s"})
    return tmp_path


def _classify(repo: Path, before: str, after: str, forced: str = "false") -> bool:
    return push_scope.classify(before, after, forced, str(repo))[0]


def test_plans_only_diff_is_plans_only(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, {"docs/plans/a.md": "b", "docs/plans/approved/s.yaml": "t"})
    assert _classify(repo, before, after) is True


def test_mixed_diff_is_full(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, {"docs/plans/a.md": "b", "src/x.py": "y"})
    assert _classify(repo, before, after) is False


def test_deletion_under_plans_is_plans_only(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, {"docs/plans/a.md": None})
    assert _classify(repo, before, after) is True


def test_deletion_outside_is_full(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, {"src/x.py": None})
    assert _classify(repo, before, after) is False


def test_rename_out_of_plans_is_full(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "docs/plans/a.md", "src/a.md")
    _git(repo, "commit", "-q", "-m", "mv")
    after = _git(repo, "rev-parse", "HEAD")
    assert _classify(repo, before, after) is False


def test_zero_before_is_full(repo: Path) -> None:
    assert _classify(repo, ZERO, _git(repo, "rev-parse", "HEAD")) is False


def test_unresolvable_before_is_full(repo: Path) -> None:
    assert _classify(repo, "1" * 40, _git(repo, "rev-parse", "HEAD")) is False


def test_forced_push_is_full(repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, {"docs/plans/a.md": "b"})
    assert _classify(repo, before, after, forced="true") is False


def test_empty_diff_is_full(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    assert _classify(repo, head, head) is False


def test_git_error_is_full(tmp_path: Path) -> None:
    assert _classify(tmp_path, "1" * 40, "2" * 40) is False


def test_confined_to_plans_polarity() -> None:
    assert push_scope.confined_to_plans(None) is False
    assert push_scope.confined_to_plans(set()) is False
    assert push_scope.confined_to_plans({"docs/plans/a.md"}) is True


def test_prefix_matches_trailer_gate() -> None:
    assert push_scope.PLAN_PREFIX == trailer_closure_gate._PLAN_PREFIX


def test_cli_writes_github_output(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit(repo, {"docs/plans/a.md": "b"})
    out = tmp_path / "gh_out"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("BEFORE", before)
    monkeypatch.setenv("AFTER", after)
    monkeypatch.setenv("FORCED", "false")
    assert push_scope.main(["push_scope.py", "classify"]) == 0
    monkeypatch.setenv("FORCED", "true")
    assert push_scope.main(["push_scope.py", "classify"]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["plans_only=true", "plans_only=false"]


def test_cli_exception_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    def boom(*_a: object, **_k: object) -> tuple[bool, str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(push_scope, "classify", boom)
    assert push_scope.main(["push_scope.py", "classify"]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["plans_only=false"]


def test_range_base_matches_push_context_base(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    _commit(repo, {"docs/plans/a.md": "b"})
    after = _commit(repo, {"docs/plans/a.md": "c"})
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_BEFORE", before)
    base = _common.push_context_base(repo)
    assert base == before
    assert base != _git(repo, "rev-parse", "HEAD~1")
    assert push_scope.changed_paths(base, after, str(repo)) == {"docs/plans/a.md"}
