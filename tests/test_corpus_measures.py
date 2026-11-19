"""corpus_measures on a throwaway git repository (hermetic, rec-5264)."""

from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scripts.preflight import corpus_measures as cm


def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{when}T12:00:00Z"
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _commit(repo: Path, when: str, decisions: list[int], plans: list[str]) -> None:
    md = repo / "docs" / "DECISIONS.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    existing = md.read_text(encoding="utf-8") if md.exists() else ""
    md.write_text(existing + "".join(f"## Decision {n}: t\n\n" for n in decisions), encoding="utf-8")
    (repo / "docs" / "plans").mkdir(exist_ok=True)
    for name in plans:
        (repo / "docs" / "plans" / f"PLAN-{name}.yaml").write_text("x: 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"c {when}", when=when)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "2026-08-01", [1, 2], ["old"])
    _commit(tmp_path, "2026-09-01", [3, 4, 5], ["a", "b"])
    _commit(tmp_path, "2026-10-01", [6], ["c"])
    _commit(tmp_path, "2026-12-01", [7, 8, 9, 10], ["d"])
    return tmp_path


def test_entries_per_plan_counts_new_ids_and_added_plans(repo: Path) -> None:
    assert cm.entries_per_plan(90, date(2026, 11, 17), repo) == (4, 3)


def test_window_ends_at_as_of(repo: Path) -> None:
    before = cm.entries_per_plan(90, date(2026, 11, 17), repo)
    _commit(repo, "2026-12-05", [11, 12], ["e", "f"])
    assert cm.entries_per_plan(90, date(2026, 11, 17), repo) == before
    assert cm.entries_per_plan(90, date(2026, 12, 2), repo) == (5, 2)


def test_short_history_raises_naming_the_window(repo: Path) -> None:
    with pytest.raises(ValueError, match="no base commit"):
        cm.entries_per_plan(400, date(2026, 11, 17), repo)


def test_window_without_plans_raises(repo: Path) -> None:
    with pytest.raises(ValueError, match="adds no plan"):
        cm.entries_per_plan(10, date(2026, 11, 17), repo)


def test_index_bytes(tmp_path: Path) -> None:
    f = tmp_path / "i.json"
    f.write_bytes(b"12345")
    assert cm.index_bytes(f) == 5
