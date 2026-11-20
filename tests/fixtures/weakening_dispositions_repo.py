"""Real-git fixture helpers for the weakening-dispositions gate tests (rec-5441). No assertions live here."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REVIEW_DISPOSITIONS_YAML = """
invariants:
  content:
    review_dispositions:
      refactor: "cite file.py::name"
      planned-change: "cite acceptance_criteria[N] or test_obligations[N]"
      retirement: "cite the deleted path or file.py::name"
      growth-safe-pin: "cite file.py::name"
      owned-skip: "cite rec-NNNN or env: label"
      weakening: "never"
"""

STRONG_TEST = "def test_a():\n    assert x == 1\n    assert y == 2\n"
WEAK_TEST = "def test_a():\n    assert x == 1\n"
ASSERT_KEY = "[assertion-removed] tests/t.py::test_a"
PLAN = "docs/plans/PLAN-x.yaml"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_plan(repo: Path, rel: str, entries: list[dict], **extra: object) -> None:
    doc = {"acceptance_criteria": ["ac 0", "ac 1"], "test_obligations": [{"source": "x"}], "weakening_dispositions": entries}
    doc.update(extra)
    write(repo, rel, yaml.safe_dump(doc))


def planned_entry(key: str = ASSERT_KEY, cite: str = "acceptance_criteria[0]") -> dict:
    return {"candidate": key, "disposition": "planned-change", "cite": cite}


def commit_all(repo: Path, message: str = "step") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def init_repo(root: Path, files: dict[str, str] | None = None) -> str:
    """git init, the contract's review_dispositions, optional extra files; returns the base commit sha."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    write(root, "docs/contracts/implement-scope-boundary.yaml", REVIEW_DISPOSITIONS_YAML)
    write(root, "scripts/checks/foo.py", "def kept_fn():\n    pass\n")
    for rel, text in (files or {}).items():
        write(root, rel, text)
    return commit_all(root, "base")
