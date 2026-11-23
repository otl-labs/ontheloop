"""Tests for scripts/checks/hygiene/derived_list_rank.py (rec-5516): coupled appends, size pressure, shallow history.

Every node builds a throwaway git history; none reads this repo's own history.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from scripts.checks.hygiene import derived_list_rank as rank_mod

LIST = "config/list.yaml"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, files: Mapping[str, str | None], subject: str) -> None:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)


def _repo(root: Path, files: Mapping[str, str | None]) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, files, "seed")
    return root


def _append(repo: Path, line: str) -> str:
    return (repo / LIST).read_text(encoding="utf-8") + line + "\n"


def test_coupled_append_needs_a_named_new_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", {LIST: "rows:\n  seeded: 1\n", "scripts/old_mod.py": "x = 1\n"})
    _commit(repo, {"scripts/foo_new.py": "x = 1\n", LIST: _append(repo, "  foo_new: 1")}, "coupled add")
    _commit(repo, {"scripts/unrelated.py": "x = 1\n", LIST: _append(repo, "  something_else: 1")}, "squash, unrelated")
    _commit(repo, {"scripts/foo_new_two.py": "x = 1\n", LIST: _append(repo, "  foo_new_twofold: 1")}, "prefix only")
    _git(repo, "mv", "scripts/old_mod.py", "scripts/renamed_mod.py")
    _commit(repo, {LIST: _append(repo, "  scripts/renamed_mod.py: 1")}, "coupled rename")
    _commit(repo, {"scripts/__init__.py": "", LIST: _append(repo, "  __init__: 1")}, "generic stem")
    ranking = rank_mod.rank(repo, [LIST], min_commits=1)
    assert ranking.note is None
    assert ranking.scores[LIST].coupled_appends == 2


def test_size_pressure_multiplies_the_score(tmp_path: Path) -> None:
    budgets = (
        "budgets:\n  config/override.yaml: 4\n"
        "classes:\n"
        "  - {slug: generated, include: [], governed: false, limit: n/a}\n"
        "  - {slug: config, include: ['config/*.yaml'], governed: true, limit: 10}\n"
        "  - {slug: workflow_outputs, include: ['docs/plans/*.yaml'], governed: false, limit: n/a}\n"
        "  - {slug: residual, include: [], governed: true, limit: 20}\n"
    )
    files = {
        "config/structural_size_budgets.yaml": budgets,
        "config/sloc_budgets.yaml": "budgets:\n  scripts/big.py: 4\n",
        LIST: "rows:\n  seeded: 1\n  other: 2\n  # comment\n\n  third: 3\n",
        "config/override.yaml": "a: 1\nb: 2\n",
        "docs/plans/PLAN-x.yaml": "a: 1\n",
        "notes/free.txt": "one\ntwo\n",
        "scripts/big.py": "a = 1\nb = 2\n",
        "scripts/small.py": "a = 1\n",
    }
    repo = _repo(tmp_path / "r", files)
    _commit(repo, {"scripts/foo_new.py": "x\n", LIST: _append(repo, "  foo_new: 1")}, "coupled")
    paths = [LIST, "config/override.yaml", "docs/plans/PLAN-x.yaml", "notes/free.txt", "scripts/big.py", "scripts/small.py"]
    scores = rank_mod.rank(repo, paths, min_commits=1).scores
    assert scores[LIST] == rank_mod.Score(coupled_appends=1, size_pressure=0.5, score=1.5)
    assert scores["config/override.yaml"].size_pressure == 0.5
    assert scores["docs/plans/PLAN-x.yaml"].size_pressure == 0.0
    assert scores["notes/free.txt"].size_pressure == 0.1
    assert scores["scripts/big.py"].size_pressure == 0.5
    assert scores["scripts/small.py"].size_pressure == 0.002
    assert rank_mod.size_pressure(repo, "config/missing.yaml") == 0.0


def test_size_limit_without_budget_files(tmp_path: Path) -> None:
    assert rank_mod.size_limit(tmp_path, "config/a.yaml") == 0
    assert rank_mod.size_limit(tmp_path, "scripts/a.py") == rank_mod.SLOC_LIMIT
    (tmp_path / "config").mkdir()
    (tmp_path / "config/structural_size_budgets.yaml").write_text("classes:\n  - {slug: x, include: []}\n", encoding="utf-8")
    assert rank_mod.size_limit(tmp_path, "config/a.yaml") == 0
    (tmp_path / "config/structural_size_budgets.yaml").write_text("[unclosed\n", encoding="utf-8")
    assert rank_mod.size_limit(tmp_path, "config/a.yaml") == 0


def test_shallow_history_returns_zero_scores_and_the_note(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", {LIST: "rows:\n  seeded: 1\n"})
    for n in range(2):
        _commit(repo, {f"scripts/new_{n}.py": "x\n", LIST: _append(repo, f"  new_{n}: 1")}, f"add {n}")
    few = rank_mod.rank(repo, [LIST], min_commits=30)
    assert few.scores[LIST].coupled_appends == 0 and few.scores[LIST].score == 0
    assert few.note == "only 3 commits in the window (fewer than 30), so every score is 0"
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(clone)], check=True, capture_output=True)
    shallow = rank_mod.rank(clone, [LIST], min_commits=1)
    assert shallow.scores[LIST].score == 0 and "history is shallow" in (shallow.note or "")
    assert rank_mod.rank(repo, [], min_commits=1) == rank_mod.Ranking({}, None)


def test_header_like_content_lines_and_deleted_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", {LIST: "rows:\n", "config/doomed.yaml": "a: 1\n"})
    _commit(repo, {"scripts/foo_new.py": "x\n", LIST: _append(repo, "++ b/foo_new"), "config/doomed.yaml": None}, "odd")
    lines = rank_mod.added_lines_by_commit(repo, "90 days ago", [LIST, "config/doomed.yaml"])
    latest = next(iter(lines.values()))
    assert latest == {LIST: ["++ b/foo_new"]}
    assert rank_mod.rank(repo, [LIST], min_commits=1).scores[LIST].coupled_appends == 1


def test_git_failures_raise_rank_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(rank_mod.RankError, match="exited"):
        rank_mod.rank(tmp_path / "plain", ["a.yaml"])

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("no git")

    monkeypatch.setattr(rank_mod.subprocess, "run", boom)
    with pytest.raises(rank_mod.RankError, match="no git"):
        rank_mod.rank(tmp_path, ["a.yaml"])


def test_needles_skip_generic_stems() -> None:
    assert rank_mod._needle_re(set()) is None
    needle = rank_mod._needle_re({"pkg/__init__.py"})
    assert needle is not None and not needle.search("__init__") and needle.search("see pkg/__init__.py")
    assert rank_mod.effective_lines("a\n\n# c\n  b\n") == 2
