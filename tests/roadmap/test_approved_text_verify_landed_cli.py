from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.roadmap import approved_text as at
from tests.fixtures import merge_step_repos as fx

SLUG = "landed-cli"
PLAN_REL = fx.plan_rel(SLUG)
BLOCK = {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}
BLOCK_TWO = {"id": "b2", "file": "two.md", "before": "uno dos", "after": "UNO DOS"}
BASE = "alpha beta gamma delta\n"
DONE = "alpha BETA GAMMA delta\n"


def _plan_text() -> str:
    scope = ["doc.md", "src.py", "two.md", PLAN_REL]
    return yaml.safe_dump(
        {"slug": SLUG, "scope": [{"file": f} for f in scope], "implementation_declared": False}, sort_keys=False
    )


def _commit(repo: Path, files: dict[str, str], message: str = "c") -> str:
    for rel, text in files.items():
        fx.write(repo, rel, text)
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", message)
    return fx.git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocks: list[dict[str, str]] | None = None) -> Path:
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_sidecar(repo, SLUG, blocks or [BLOCK])
    _commit(repo, {"doc.md": BASE, "two.md": "uno dos\n", "src.py": "0\n", PLAN_REL: _plan_text()}, "plan")
    return repo


def _verify(repo: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    code = at.main(["verify", "--slug", SLUG, "--root", str(repo), "--base", "HEAD", *extra])
    return code, capsys.readouterr().out


def test_landed_words_pass_with_a_landed_line(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    sha = _commit(repo, {"doc.md": DONE, "src.py": "1\n"}, "apply")
    code, out = _verify(repo, capsys)
    lines = out.splitlines()
    assert code == 0
    assert lines[0] == f"APPROVED-TEXT LANDED doc.md {sha}"
    assert lines[1].startswith("APPROVED-TEXT OK ")


def test_words_applied_in_the_tree_pass_without_a_landed_line(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    fx.write(repo, "doc.md", DONE)
    code, out = _verify(repo, capsys)
    assert code == 0 and "LANDED" not in out and "APPROVED-TEXT OK " in out


def test_words_that_never_landed_fail_without_a_landing_commit(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, {"src.py": "1\n"}, "unrelated")
    code, out = _verify(repo, capsys)
    assert code == 1 and "APPROVED-TEXT MISMATCH" in out and "LANDED" not in out


def test_a_re_edited_bound_file_stays_strict(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, {"doc.md": DONE, "src.py": "1\n"}, "apply")
    fx.write(repo, "doc.md", DONE + "edited again\n")
    code, out = _verify(repo, capsys)
    assert code == 1 and "APPROVED-TEXT MISMATCH" in out and "LANDED" not in out


def test_two_qualifying_commits_compete(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    first = _commit(repo, {"doc.md": DONE, "src.py": "1\n"}, "apply")
    _commit(repo, {"doc.md": BASE, "src.py": "2\n"}, "revert")
    second = _commit(repo, {"doc.md": DONE, "src.py": "3\n"}, "apply again")
    code, out = _verify(repo, capsys)
    assert code == 1 and "competing landing commits" in out and first in out and second in out


def test_a_wrong_expect_digest_fails_after_the_landed_line(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, {"doc.md": DONE, "src.py": "1\n"}, "apply")
    code, out = _verify(repo, capsys, "--expect", "0" * 64)
    lines = out.splitlines()
    assert code == 1 and lines[0].startswith("APPROVED-TEXT LANDED doc.md ")
    assert lines[1].startswith("APPROVED-TEXT MISMATCH digest ")


def test_a_changed_sidecar_is_refused_before_any_landed_line(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, {"doc.md": DONE, "src.py": "1\n"}, "apply")
    fx.write_sidecar(repo, SLUG, [{**BLOCK, "after": "BETA GAMMA changed"}])
    code, out = _verify(repo, capsys)
    assert code == 1 and "APPROVED-TEXT SIDECAR changed" in out and "LANDED" not in out


def test_one_file_landed_and_one_applied_in_the_tree(tmp_path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path, monkeypatch, [BLOCK, BLOCK_TWO])
    sha = _commit(repo, {"doc.md": DONE, "src.py": "1\n"}, "apply doc")
    fx.write(repo, "two.md", "UNO DOS\n")
    code, out = _verify(repo, capsys)
    landed = [line for line in out.splitlines() if "LANDED" in line]
    assert code == 0 and landed == [f"APPROVED-TEXT LANDED doc.md {sha}"]
