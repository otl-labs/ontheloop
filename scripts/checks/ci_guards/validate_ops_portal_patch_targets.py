"""Caller-aware facade-patch-target guard (rec-2637 / Decision 124).

Root cause this guard defends against: Decision 124 split scripts/ops_data_portal.py into
scripts/ops_portal/*.py submodules and re-exports the moved symbols back through the facade
for backward-compatible imports. A test that exercises a MOVED caller (its definition now
lives in a submodule) but patches ``scripts.ops_data_portal.<sym>`` only rebinds the facade
re-export attribute -- the moved caller resolves the symbol at its own submodule scope, so
the patch never intercepts the call (rec-2637: file_decision unmocked, real writer resolution
raised RuntimeError in hermetic CI).

Keyed on the CALLER the test drives, not the symbol alone: file_rec/update_rec are
facade-resident (defined directly in scripts/ops_data_portal.py) and legitimately patch
scripts.ops_data_portal.<sym> for the very same symbol names -- a symbol-only guard would
false-positive on their tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.checks import _common, registry

_FACADE_MODULE = "scripts.ops_data_portal"

# Moved-caller -> (submodule namespace, bound-symbol set the caller resolves there).
# Add an entry here when another caller moves behind the facade; the scan logic needs no change.
_MOVED_CALLERS: dict[str, tuple[str, frozenset[str]]] = {
    "file_decision": (
        "scripts.ops_portal.decisions",
        frozenset({"_ducklake_write", "DECISIONS_JSONL", "gate_write", "_refresh_cache_after_write"}),
    ),
    "update_decision": (
        "scripts.ops_portal.decisions",
        frozenset({"_ducklake_write", "DECISIONS_JSONL", "gate_write", "_refresh_cache_after_write"}),
    ),
    "backfill_decisions_from_md": (
        "scripts.ops_portal.decisions",
        frozenset({"_sync_table", "_assert_no_orphaned_current_rows"}),
    ),
}


def _called_names(fn: ast.AST) -> set[str]:
    """Names/attrs invoked as a Call anywhere under `fn` (decorators, with-items, body)."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _facade_patch_symbols(fn: ast.AST) -> set[str]:
    """Symbols targeted by patch("scripts.ops_data_portal.<sym>"[, ...]) anywhere under `fn`."""
    symbols: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_patch_call = (isinstance(func, ast.Name) and func.id == "patch") or (
            isinstance(func, ast.Attribute) and func.attr == "patch"
        )
        if not is_patch_call or not node.args:
            continue
        target = node.args[0]
        if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
            continue
        if not target.value.startswith(f"{_FACADE_MODULE}."):
            continue
        symbols.add(target.value.rsplit(".", 1)[-1])
    return symbols


def _scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []

    rel = path.relative_to(_common.ROOT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
            continue
        called = _called_names(node)
        exercised_movers = [caller for caller in _MOVED_CALLERS if caller in called]
        if not exercised_movers:
            continue
        patched_symbols = _facade_patch_symbols(node)
        if not patched_symbols:
            continue
        for caller in exercised_movers:
            submodule, bound_symbols = _MOVED_CALLERS[caller]
            stale = patched_symbols & bound_symbols
            for sym in sorted(stale):
                violations.append(
                    f"{rel}:{node.lineno}: {node.name} exercises moved caller {caller!r} but patches "
                    f"{_FACADE_MODULE}.{sym} -- patch {submodule}.{sym} instead (the caller resolves it there)"
                )
    return violations


def _find_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        violations.extend(_scan_file(path))
    return violations


@registry.register("validate_ops_portal_patch_targets", owner="platform")
def validate_ops_portal_patch_targets(failed: list[str]) -> None:
    """Reject tests/**/*.py patches at the facade namespace for a symbol a moved caller
    the same test exercises resolves at its own submodule scope (rec-2637)."""
    print("\n=== ops_portal facade patch-target guard ===")
    tests_dir = _common.ROOT / "tests"
    paths = sorted(tests_dir.glob("**/*.py"))
    violations = _find_violations(paths)
    registry.examined(len(paths), unit="test_files")
    if violations:
        print("Stale facade patch targets found:")
        for v in violations:
            print(f"  - {v}")
        failed.append("ops_portal facade patch-target guard")
    else:
        print("No stale facade patch targets against moved ops_portal callers found.")
