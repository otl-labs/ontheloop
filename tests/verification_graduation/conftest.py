"""Package conftest for tests/verification_graduation/ (Decision 176 concern-split
decomposition of the former tests/test_verification_graduation.py monolith, SLOC decompose-by-
default -- see AGENTS.md SLOC governance).

Hoists the shared git/seed fixture helpers used across the differential test classes AND the new
loader test classes -- without this conftest the loader classes would need to duplicate ~60 SLOC
or import across test_* modules, which validate_no_cross_test_imports fails in both tiers
(Decision 131 clause 2).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from scripts import verification_graduation as vg

# Files a synthetic tree needs so a subprocess re-invoking
# `python -m scripts.verification_graduation --run-verifier ...` in that tree's cwd can resolve
# its own import chain (the real repo always carries this closure at HEAD; a fixture repo must
# replicate it explicitly).
_MANIFEST_DOMAINS = (
    "ci_guards contracts decisions deps executor hygiene iam_tf lambda_pkg misc "
    "ops_governance prompts prose roadmap sloc structural typing verification"
).split()

_GRADUATION_DEPS = (
    (
        "scripts/verification_graduation.py",
        "scripts/verification_checks.py",
        "scripts/contract_rules.py",
        "scripts/checks/verification/_graduation_git.py",
        "scripts/checks/__init__.py",
        "scripts/checks/_common.py",
        "scripts/checks/_scaffolding.py",
        "scripts/checks/_pytest_diff.py",
        "scripts/checks/_budget_recs.py",
        "scripts/rec_episode.py",
        "scripts/checks/_terraform.py",
        "scripts/checks/_schema.py",
        "scripts/checks/registry.py",
        "scripts/checks/validation_result.py",
        "scripts/checks/verification/validate_verifier_hermeticity.py",
        "scripts/checks/iam_tf/validate_terraform_try.py",
    )
    # registry.py imports every domain's _manifest.py (Decision 169) -- a synthetic tree missing
    # any one of them fails the whole import chain, not just that domain's checks.
    + tuple(f"scripts/checks/{domain}/__init__.py" for domain in _MANIFEST_DOMAINS)
    + tuple(f"scripts/checks/{domain}/_manifest.py" for domain in _MANIFEST_DOMAINS)
)


def _seed_graduation_deps(repo: Path) -> None:
    for rel in _GRADUATION_DEPS:
        src = vg.ROOT / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, ["init", "-q"])
    _git(repo, ["config", "user.email", "test@example.com"])
    _git(repo, ["config", "user.name", "Test"])


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", message])
    return _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
