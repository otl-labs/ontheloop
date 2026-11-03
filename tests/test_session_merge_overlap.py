"""Fixture-repo tests of scripts.session.merge_overlap (refs-only predicate)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.session import merge_overlap as mo
from tests.fixtures import merge_overlap_repos as fx


@pytest.fixture
def repo(tmp_path: Path) -> fx.Fixture:
    return fx.Fixture(tmp_path)


def _verdict(f: fx.Fixture, number: int) -> mo.Verdict:
    f.fetch_refs(f.work, number)
    return mo.overlap(f.work, f"refs/pr/{number}", "refs/remotes/origin/main")


def test_not_behind_is_clear(repo: fx.Fixture) -> None:
    repo.branch_pr(1, "a.txt", "pr\n")
    v = _verdict(repo, 1)
    assert (v.kind, v.behind, v.files) == (mo.CLEAR, 0, ())


def test_behind_with_disjoint_files_is_clear(repo: fx.Fixture) -> None:
    repo.branch_pr(1, "a.txt", "pr\n")
    repo.advance_main("b.txt", "main\n")
    v = _verdict(repo, 1)
    assert v.kind == mo.CLEAR and v.behind == 1


def test_behind_with_shared_file_is_overlap_naming_only_shared(repo: fx.Fixture) -> None:
    repo.branch_pr(1, "a.txt", "pr\n")
    repo.advance_main("a.txt", "main\n")
    repo.advance_main("only_main.txt", "x\n")
    v = _verdict(repo, 1)
    assert v.kind == mo.OVERLAP and v.files == ("a.txt",) and v.behind == 2


def test_branch_updated_with_main_is_clear(repo: fx.Fixture) -> None:
    repo.branch_pr(1, "b.txt", "pr\n")
    repo.advance_main("b.txt", "main\n")
    assert _verdict(repo, 1).kind == mo.OVERLAP
    repo.update_pr(1)
    assert _verdict(repo, 1).kind == mo.CLEAR


def test_rename_on_main_of_file_pr_modifies_is_overlap(repo: fx.Fixture) -> None:
    repo.branch_pr(1, "a.txt", "pr\n")
    fx.git(repo.work, "mv", "a.txt", "renamed.txt")
    fx.git(repo.work, "commit", "-m", "rename")
    fx.git(repo.work, "push", "origin", "main")
    v = _verdict(repo, 1)
    assert v.kind == mo.OVERLAP and "a.txt" in v.files


def test_unresolvable_ref_is_unavailable(repo: fx.Fixture) -> None:
    v = mo.overlap(repo.work, "refs/nope", "refs/remotes/origin/main")
    assert v.kind == mo.UNAVAILABLE and v.reason


def test_non_repository_root_is_unavailable(tmp_path: Path) -> None:
    assert mo.overlap(tmp_path, "HEAD", "main").kind == mo.UNAVAILABLE


def test_missing_root_is_unavailable(tmp_path: Path) -> None:
    assert mo.overlap(tmp_path / "absent", "HEAD", "main").kind == mo.UNAVAILABLE


def test_cli_line_format_and_exit_codes(repo: fx.Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    repo.branch_pr(1, "a.txt", "pr\n")
    repo.branch_pr(2, "b.txt", "pr\n", name="pr2")
    repo.advance_main("a.txt", "main\n")
    repo.fetch_refs(repo.work, 1)
    repo.fetch_refs(repo.work, 2)
    base = ["check", "--root", str(repo.work), "--base", "refs/remotes/origin/main"]
    assert mo.main([*base, "--head", "refs/pr/1"]) == 1
    out = capsys.readouterr().out.strip()
    assert out.startswith("MERGE-OVERLAP OVERLAP behind=1 base=") and out.endswith("files=a.txt")
    assert mo.main([*base, "--head", "refs/pr/2"]) == 0
    assert capsys.readouterr().out.startswith("MERGE-OVERLAP CLEAR behind=1 base=")
    assert mo.main([*base, "--head", "refs/missing"]) == 2
    assert capsys.readouterr().out.startswith("MERGE-OVERLAP UNAVAILABLE reason=")


def test_cli_root_defaults_to_this_checkout() -> None:
    assert mo.ROOT == Path(mo.__file__).resolve().parents[2]


def _fake(monkeypatch: pytest.MonkeyPatch, responder) -> None:  # noqa: ANN001
    import subprocess

    def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        code, out = responder(args)
        return subprocess.CompletedProcess(args, code, out, "err")

    monkeypatch.setattr(mo, "_git", run)


def test_git_that_cannot_run_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root: Path, *args: str) -> None:
        raise OSError("no git")

    monkeypatch.setattr(mo, "_git", boom)
    v = mo.overlap(Path("."), "h", "b")
    assert v.kind == mo.UNAVAILABLE and "no git" in v.reason


def test_failed_diff_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, lambda a: (1, "") if a[0] == "diff" else (0, "abc\n"))
    assert mo.overlap(Path("."), "h", "b").kind == mo.UNAVAILABLE


def test_unparseable_count_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, lambda a: (0, "many\n" if a[0] == "rev-list" else "abc\n"))
    v = mo.overlap(Path("."), "h", "b")
    assert v.kind == mo.UNAVAILABLE and "unparseable" in v.reason
