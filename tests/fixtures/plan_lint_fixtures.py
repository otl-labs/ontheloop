"""Builders for the tmp-path fixture repos and plan dicts the plan_lint tests share.

Run as a module (`python -m tests.fixtures.plan_lint_fixtures <arg>...`) it stands in for the acceptance runner:
each arg prints one line, `FAIL` exits 3 and `SLEEP` outlives any timeout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROWS = {
    "pre_glob_closure": True,
    "accounting_baseline_strike": True,
    "class_d_amendment_entry": True,
    "registry_shard_disposition": True,
    "size_headroom": False,
    "unbuilt_same_file": False,
    "acceptance_decidable": False,
    "frozen_companions": False,
}
SHARD_DIR = "config/agent/verification_registry/entries"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_yaml(root: Path, rel: str, data: Any) -> Path:
    return write(root, rel, yaml.safe_dump(data))


def write_contract(root: Path, gating: dict[str, bool] | None = None, frozen: list[dict[str, str]] | None = None) -> Path:
    rows = {name: {"gating": flag} for name, flag in {**ROWS, **(gating or {})}.items()}
    data = {"derived_findings": {"rows": rows}, "frozen_companions": frozen or []}
    return write_yaml(root, "docs/contracts/plan-obligations.yaml", data)


def make_plan(*rows: tuple[str, str, str], **extra: Any) -> dict[str, Any]:
    scope = [{"file": f, "action": a, "purpose": p} for f, a, p in rows]
    return {"schema_version": 4, "plan_type": "IMPLEMENTATION", "slug": "fixture", "scope": scope, **extra}


def write_baseline(root: Path, names: list[str]) -> None:
    write_yaml(root, "config/check_accounting_baseline.yaml", {"entries": names})


def write_class_d(root: Path, rel: str, *, with_log: bool) -> None:
    data: dict[str, Any] = {"contract": {"class": "D", "id": "x"}}
    if with_log:
        data["amendment_log"] = [{"date": "2026-01-01"}]
    write_yaml(root, rel, data)


def write_shard(root: Path, check_id: str, node_id: str) -> str:
    rel = f"{SHARD_DIR}/{check_id}.yaml"
    write_yaml(root, rel, {"check_id": check_id, "check_spec": {"node_id": node_id}})
    return rel


def write_structural_registry(root: Path, limit: int) -> None:
    classes = [{"slug": "contracts", "include": ["docs/contracts/*.yaml"], "governed": True, "limit": limit}]
    write_yaml(root, "config/structural_size_budgets.yaml", {"exclusions": {}, "classes": classes, "budgets": {}})


def write_closure_repo(root: Path) -> None:
    """One gated check (scripts.checks.fake.validate_thing) whose only glob is scripts/checks/fake/**."""
    for pkg in ("scripts", "scripts/checks", "scripts/checks/fake"):
        write(root, f"{pkg}/__init__.py", "")
    write(root, "scripts/hublib.py", "def hub():\n    pass\n")
    write(root, "scripts/checks/fake/validate_thing.py", "def validate_thing(failed):\n    pass\n")


def git_commit(root: Path, date: str | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"], cwd=root, check=True, env=env)


def main(argv: list[str]) -> int:
    if "FAIL" in argv:
        return 3
    if "SLEEP" in argv:
        time.sleep(30)
    for arg in argv:
        print(f"{arg}: fixture line")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
