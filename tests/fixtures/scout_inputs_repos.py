"""Fixture git repos with a minimal fingerprintable tree for the scout-input predicate (rec-5654)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from tests.fixtures.merge_overlap_repos import git

DECISIONS = "## Decision 217: Title (Decided)\n\nbody one\n\n## Decision 229: Other (Decided)\n\nbody two\n"
PLAN: dict[str, Any] = {
    "schema_version": 7,
    "slug": "demo",
    "intent": "Build the thing",
    "plan_type": "IMPLEMENTATION",
    "verification_tier": "V2",
    "phase": "T4.32",
    "scope": [{"file": "src/a.py", "action": "Create", "purpose": "p"}],
    "cites": ["dec:217"],
    "amends": [],
    "introduces": [],
    "closes_criteria": [],
    "gate_rounds": [],
}
ROADMAP = {
    "document": {"id": "R", "version": 1, "status": "ratified", "filed_via": "ops_decisions:dec-108"},
    "tier_items": [{"id": "T4.32", "tier": "T4", "name": "verdicts", "status": "in_progress", "intent": "i"}],
}


def plan_text(slug: str = "demo", **overrides: Any) -> str:
    plan = copy.deepcopy(PLAN)
    plan.update(slug=slug, **overrides)
    return yaml.safe_dump(plan, sort_keys=False)


def write(repo: Path, rel: str, content: str | bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    """A repo on branch main holding the scout-input tree; branch pr is created from it by pr_branch()."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "docs/DECISIONS.md", DECISIONS)
    write(repo, "docs/ROADMAP-PLATFORM.yaml", yaml.safe_dump(ROADMAP))
    write(repo, "docs/contracts/north-star.yaml", "stars\n")
    write(repo, "src/a.py", "x = 1\n")
    commit(repo, "base")
    return repo


def pr_branch(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "pr")


def on_main(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")


def merge_base(repo: Path) -> str:
    return git(repo, "merge-base", "main", "pr")


def merge_base_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")
