"""Flags a hermetic pre-deploy pytest step whose target is guarded on a package PR CI's fast tier lacks (rec-2809).

PR CI's venv-less fast tier installs requirements-fast.txt plus requirements-dev.txt only, so a test file with a
module-level ``pytest.importorskip("duckdb")`` collects 0 tests there (pytest exit 5) and VP replay reds the
implementation PR. This check enforces docs/contracts/vp-red-before.yaml hermetic_authoring carve-out (iii) at plan-PR
time (Decision 181 point 2): the population is vp-red-before.yaml's eligibility predicate (PLAN files ADDED in this diff
whose implementation_declared is falsy). The verdict is a static AST walk of the target file and its conftest chain, so
--pre and the full tier (where the package is installed) agree. Residuals (transitive imports, merged unbuilt plans):
rec-4763.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
from collections.abc import Iterator
from pathlib import Path

import yaml

from scripts.checks import _common, _pytest_diff, registry

_LABEL = "Hermetic fast-tier collectability (rec-2809)"
_SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|]")
_VALUE_OPTIONS = frozenset(
    {"--deselect", "--ignore", "--ignore-glob", "-k", "-m", "-p", "-c", "-o", "--rootdir", "--cov", "--cov-report"}
)
_LAX_TRUE = frozenset({"1", "true", "t", "yes", "y", "on"})
_LAX_FALSE = frozenset({"0", "false", "f", "no", "n", "off"})
_FIX = (
    "set hermetic: false, or move the guarded selector into its own hermetic: false step (for a directory selector, "
    "list its unguarded member files instead of the directory) -- vp-red-before.yaml hermetic_authoring carve-out (iii)"
)


def absent_import_names(root: Path) -> set[str]:
    """Import names of requirements.in dists that neither requirements-fast.txt nor requirements-dev.txt installs."""
    parse = _pytest_diff._parse_requirement_dist_names
    full = parse(root / "requirements.in")
    installed = parse(root / "requirements-fast.txt") | parse(root / "requirements-dev.txt")
    return {_pytest_diff._dist_to_import_name(dist) for dist in full - installed}


def _lax_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _LAX_TRUE:
            return True
        if lowered in _LAX_FALSE:
            return False
    return None


def _pytest_selectors(segment: str) -> list[str] | None:
    """Positional tests/ selectors of one pytest segment, or None when the segment is not a pytest invocation."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    start = next(
        (
            i
            for i, tok in enumerate(tokens)
            if os.path.basename(tok) == "pytest" or (tok == "pytest" and i > 0 and tokens[i - 1] == "-m")
        ),
        None,
    )
    if start is None:
        return None
    selectors: list[str] = []
    skip_value = False
    for tok in tokens[start + 1 :]:
        if skip_value:
            skip_value = False
        elif tok.startswith("-"):
            skip_value = tok in _VALUE_OPTIONS
        elif tok.startswith("tests/"):
            selectors.append(tok)
    return selectors


def _module_level_nodes(body: list[ast.stmt], in_try: bool = False) -> Iterator[tuple[ast.stmt, bool]]:
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield stmt, in_try
        if isinstance(stmt, ast.Try):
            for part in (stmt.body, stmt.orelse, stmt.finalbody, *(h.body for h in stmt.handlers)):
                yield from _module_level_nodes(part, True)
        elif isinstance(stmt, (ast.If, ast.With, ast.For, ast.While)):
            yield from _module_level_nodes(stmt.body, in_try)
            yield from _module_level_nodes(stmt.orelse if hasattr(stmt, "orelse") else [], in_try)


def _importorskip_name(stmt: ast.stmt) -> str | None:
    call = stmt.value if isinstance(stmt, (ast.Expr, ast.Assign)) else None
    if not isinstance(call, ast.Call) or not call.args:
        return None
    func = call.func
    if not (
        (isinstance(func, ast.Attribute) and func.attr == "importorskip")
        or (isinstance(func, ast.Name) and func.id == "importorskip")
    ):
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split(".")[0]
    return None


def _guard_names(path: Path) -> set[str]:
    """Top-level names a module-level importorskip, or a module-level import outside try, requires."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for stmt, in_try in _module_level_nodes(tree.body):
        skipped = _importorskip_name(stmt)
        if skipped:
            names.add(skipped)
        elif isinstance(stmt, ast.Import) and not in_try:
            names.update(alias.name.split(".")[0] for alias in stmt.names)
        elif isinstance(stmt, ast.ImportFrom) and not in_try and stmt.level == 0 and stmt.module:
            names.add(stmt.module.split(".")[0])
    return names


def _guarded_by(test_file: Path, root: Path, absent: set[str]) -> tuple[Path, str] | str | None:
    """(guarding file, absent name) for the test file or its conftest chain, an error string, or None."""
    tests_root = root / "tests"
    candidates = [test_file]
    directory = test_file.parent
    while True:
        candidates.append(directory / "conftest.py")
        if directory == tests_root or directory == root or tests_root not in directory.parents:
            break
        directory = directory.parent
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            hit = sorted(_guard_names(candidate) & absent)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            return f"{candidate.relative_to(root).as_posix()} unreadable: {exc}"
        if hit:
            return candidate, hit[0]
    return None


def _check_selector(selector: str, root: Path, absent: set[str], plan_rel: str, step_id: object) -> tuple[list[str], int]:
    """(findings, missing count) for one positional selector."""
    target = root / selector.split("::", 1)[0]
    if target.is_dir():
        members = sorted(target.rglob("test_*.py"))
    elif target.is_file():
        members = [target]
    else:
        return [], 1
    findings: list[str] = []
    for member in members:
        verdict = _guarded_by(member, root, absent)
        if verdict is None:
            continue
        member_rel = member.relative_to(root).as_posix()
        if isinstance(verdict, str):
            findings.append(f"{plan_rel} step {step_id}: selector {selector}: {verdict}")
            continue
        guard_file, name = verdict
        guard_rel = guard_file.relative_to(root).as_posix()
        findings.append(
            f"{plan_rel} step {step_id}: selector {selector} (member {member_rel}) is guarded by {guard_rel} on "
            f"'{name}', which PR CI's fast tier lacks (pytest collects 0 tests there); {_FIX}"
        )
    return findings, 0


def _check_plan(plan_rel: str, root: Path, absent: set[str], notes: list[str]) -> tuple[list[str], int, int]:
    """(findings, hermetic pytest steps examined, missing selector paths) for one added plan."""
    try:
        data = yaml.safe_load((root / plan_rel).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{plan_rel}: unreadable plan: {exc}"], 0, 0
    if not isinstance(data, dict) or data.get("implementation_declared"):
        return [], 0, 0
    steps = data.get("verification_plan")
    if not isinstance(steps, list):
        notes.append(f"{plan_rel}: verification_plan is not a list -- skipped")
        return [], 0, 0
    findings: list[str] = []
    examined = missing = 0
    for step in steps:
        if not isinstance(step, dict):
            notes.append(f"{plan_rel}: non-mapping verification step -- skipped")
            continue
        if step.get("phase") != "pre-deploy" or not isinstance(step.get("command"), str):
            continue
        hermetic = _lax_bool(step.get("hermetic", False))
        if hermetic is None:
            findings.append(f"{plan_rel} step {step.get('step')}: hermetic value {step.get('hermetic')!r} is not a bool")
            continue
        if not hermetic:
            continue
        for segment in _SHELL_SEGMENT_SPLIT_RE.split(step["command"]):
            selectors = _pytest_selectors(segment)
            if selectors is None:
                continue
            examined += 1
            for selector in selectors:
                found, gone = _check_selector(selector, root, absent, plan_rel, step.get("step"))
                findings.extend(found)
                missing += gone
    return findings, examined, missing


@registry.register("validate_hermetic_fast_tier_collectability", owner="platform")
def validate_hermetic_fast_tier_collectability(failed: list[str], root: Path | None = None) -> None:
    """Fail a diff-added, not-yet-implemented plan whose hermetic pre-deploy pytest step targets a file guarded on a
    package PR CI's fast tier lacks (vp-red-before.yaml hermetic_authoring carve-out (iii), rec-2809)."""
    print("\n=== Hermetic fast-tier collectability (rec-2809, vp-red-before carve-out iii) ===")
    root = root if root is not None else _common.ROOT
    if not _common.origin_main_reachable(root):
        print("  SKIP: origin/main unreachable (advisory locally, authoritative in CI).")
        registry.skipped("diff base unreachable")
        return
    absent = absent_import_names(root)
    added = sorted(
        path
        for status, path in _common.get_status_aware_diff(root)
        if status in ("A", "??") and _common.PLAN_PATH_RE.match(path)
    )
    notes: list[str] = []
    findings: list[str] = []
    examined = missing = 0
    for plan_rel in added:
        plan_findings, plan_examined, plan_missing = _check_plan(plan_rel, root, absent, notes)
        findings.extend(plan_findings)
        examined += plan_examined
        missing += plan_missing
    for note in notes:
        print(f"  NOTE: {note}")
    if missing:
        print(f"  NOTE: {missing} selector path(s) absent on disk -- skipped, not reported")
    for finding in findings:
        print(f"  FAIL: {finding}")
    if findings:
        registry.failure_detail(findings)
        failed.append(_LABEL)
    else:
        print(f"  PASS: {examined} hermetic pytest step(s) examined against CI-absent set {sorted(absent)}.")
    registry.examined(examined, unit="hermetic_pytest_steps")
