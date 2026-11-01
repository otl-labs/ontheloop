"""Guard for docs/contracts/mechanical-conflicts.yaml (PLAN-decision-renumber-at-merge, rec-4323).

scripts.session.rebase_resolve reads the contract's table at run time, so this check keeps the table honest:
the document's shape, every derived path and generator, every roster glob, the Decision-numbering files, the
excluded paths (none may overlap a classified file) and git-ops.yaml's pointer back to the contract and to
the command.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.session.rebase_resolve import CONTRACT, load_classes

_TOP_KEYS = {"contract", "amendment_log", "pure_insertion", "classes", "excluded", "clean_run", "validation"}
_CLASS_KEYS = {"derived", "roster", "decision_numbering"}
_GIT_OPS = "docs/contracts/git-ops.yaml"
_COMMAND = "scripts.session.rebase_resolve"


def _tracked(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout.splitlines() if done.returncode == 0 else []


def _generator_problem(root: Path, generator: str) -> str | None:
    parts = generator.split()
    if len(parts) >= 3 and parts[:2] == ["bin/venv-python", "-m"]:
        stem = parts[2].replace(".", "/")
        found = (root / f"{stem}.py").is_file() or (root / stem / "__init__.py").is_file()
        return None if found else f"generator module {parts[2]} does not resolve to a file"
    if len(parts) >= 2 and parts[0] == "bin/venv-python" and parts[1] != "-m":
        return None if (root / parts[1]).is_file() else f"generator script {parts[1]} does not exist"
    return f"generator {generator!r} is not `bin/venv-python -m <module>` or `bin/venv-python <path>`"


def _table_problems(root: Path, data: dict[str, Any], tracked: list[str]) -> tuple[list[str], int]:
    problems: list[str] = []
    try:
        plan = load_classes(root)
    except ValueError as exc:
        return [f"table unreadable: {exc}"], 0
    classified: list[str] = []
    for path, generator in plan.derived.items():
        if path not in tracked:
            problems.append(f"derived path {path} is not a tracked file")
        if (problem := _generator_problem(root, generator)) is not None:
            problems.append(f"{path}: {problem}")
        classified.append(path)
    for entry in plan.rosters:
        pattern = entry if isinstance(entry, str) else entry.get("path", "")
        if not isinstance(entry, str) and not isinstance(entry.get("only_under", ""), str):
            problems.append(f"roster {pattern}: only_under must be a string")
        matched = [t for t in tracked if fnmatch.fnmatchcase(t, str(pattern))]
        if not matched:
            problems.append(f"roster {pattern} matches no tracked file")
        classified.extend(matched)
    for path in plan.decisions:
        if not (root / path).is_file():
            problems.append(f"decision_numbering file {path} is missing")
        classified.append(path)
    for pattern in plan.excluded:
        overlap = sorted(c for c in classified if fnmatch.fnmatchcase(c, pattern))
        if overlap:
            problems.append(f"excluded path {pattern} matches classified file(s) {overlap}")
    return problems, len(plan.derived) + len(plan.rosters) + len(plan.decisions) + len(plan.excluded)


def _pointer_problems(root: Path) -> list[str]:
    try:
        data = yaml.safe_load((root / _GIT_OPS).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{_GIT_OPS} unreadable: {exc}"]
    problems = []
    key = data.get("mechanical_conflict_classes") if isinstance(data, dict) else None
    if not isinstance(key, str) or CONTRACT not in key:
        problems.append(f"{_GIT_OPS} lacks a mechanical_conflict_classes key naming {CONTRACT}")
    phases = data.get("rebase_phase_distinction") if isinstance(data, dict) else None
    commit_flow = phases.get("commit_flow_time") if isinstance(phases, dict) else None
    if not isinstance(commit_flow, str) or _COMMAND not in commit_flow:
        problems.append(f"{_GIT_OPS} rebase_phase_distinction.commit_flow_time does not name {_COMMAND}")
    return problems


@registry.register("validate_mechanical_conflicts", owner="platform")
def validate_mechanical_conflicts(failed: list[str], *, repo_root: Path | None = None) -> None:
    """FAIL on a malformed mechanical-conflicts table or a missing git-ops pointer."""
    print("\n=== mechanical conflicts contract ===")
    root = repo_root if repo_root is not None else _common.ROOT
    try:
        data = yaml.safe_load((root / CONTRACT).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"mechanical conflicts: {CONTRACT} unreadable: {exc}")
        registry.skipped(f"{CONTRACT} unreadable")
        return
    problems: list[str] = []
    count = 0
    if not isinstance(data, dict) or set(data) != _TOP_KEYS:
        problems.append(f"top-level keys are not exactly {sorted(_TOP_KEYS)}")
    elif not isinstance(data["classes"], dict) or set(data["classes"]) != _CLASS_KEYS:
        problems.append(f"classes are not exactly {sorted(_CLASS_KEYS)}")
    else:
        problems, count = _table_problems(root, data, _tracked(root))
    problems += _pointer_problems(root)
    registry.examined(count, unit="table entries")
    for problem in problems:
        print(f"  FAIL: {problem}")
        failed.append(f"mechanical conflicts: {problem}")
    if not problems:
        print(f"  PASS: {count} table entries resolve; git-ops.yaml points at the contract and the command.")
