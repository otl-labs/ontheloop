"""Flags a root-taking function that calls a ROOT-pinned diff-base primitive bare (rec-3166).

Root cause: scripts/checks/_common.py's push_context_base() / get_changed_files() /
get_status_aware_diff() / status_diff_base() each grew an optional `root` parameter (Decision 159 cl.1 amendment)
so a check evaluating an injected root (a fixture repository, a test tmp_path) can pair its
git probes and path-existence filters with that same root instead of silently reading the
REAL repository. A function that itself accepts `root` but calls one of these three
primitives without forwarding it re-opens the exact leak: on a post-merge push, the bare
call reads the outer repository's push-context base while the rest of the function operates
on the injected root (validate_graduation_completeness.py:267, the live rec-3166 break).

Detects a Call node, inside the body of a FunctionDef/AsyncFunctionDef whose own signature
declares a `root` parameter, whose func is either a bare ast.Name or an ast.Attribute named
`push_context_base` / `get_changed_files` / `get_status_aware_diff`, that passes neither a
positional `root` Name argument nor a `root=` keyword argument. A `# root-scoped-ok: <reason>`
comment on the call's line(s) waives a deliberate bare call (the `# count-coupling-ok:`
precedent, validate_test_count_coupling.py:34 -- NOT a Decision-165 authorization grammar, so
it stays a free-text suppression marker here rather than moving to _marker_guard.py).

Scope: scripts/checks/**/*.py only (mirrors validate_check_accounting's glob) -- the guard is
blind to a root-taking function outside that tree (e.g. scripts/test_coverage_checker.py, a
named Decision 159 consumer with no `root` parameter today) by design; widening the glob is a
follow-on, not this check's job.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from scripts.checks import _common, registry

_TARGET_PRIMITIVES = frozenset({"push_context_base", "get_changed_files", "get_status_aware_diff", "status_diff_base"})
_WAIVER_MARKER = "# root-scoped-ok:"


def _func_takes_root(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    return any(a.arg == "root" for a in all_args)


def _call_target_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _TARGET_PRIMITIVES:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _TARGET_PRIMITIVES:
        return func.attr
    return None


def _call_passes_root(node: ast.Call) -> bool:
    for arg in node.args:
        if isinstance(arg, ast.Name) and arg.id == "root":
            return True
    for kw in node.keywords:
        if kw.arg == "root":
            return True
    return False


def _call_has_waiver(lines: list[str], node: ast.Call) -> bool:
    start = node.lineno
    end = getattr(node, "end_lineno", start) or start
    return any(_WAIVER_MARKER in lines[lineno - 1] for lineno in range(start, end + 1) if 1 <= lineno <= len(lines))


def _iter_scope_nodes(stmts: list[ast.stmt]) -> Iterator[ast.AST]:
    """Yield every node reachable from `stmts` without descending into a nested
    function/class body -- those are separate scopes, walked independently (mirrors
    validate_test_count_coupling.py's precedent)."""
    stack: list[ast.AST] = list(stmts)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _scan_file(path: Path) -> tuple[list[str], int]:
    """Return (violations, root_taking_functions_scanned) for one file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return [], 0

    rel = path.relative_to(_common.ROOT)
    violations: list[str] = []
    scanned = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _func_takes_root(node):
            continue
        scanned += 1
        for call in _iter_scope_nodes(node.body):
            if not isinstance(call, ast.Call):
                continue
            target = _call_target_name(call)
            if target is None:
                continue
            if _call_passes_root(call):
                continue
            if _call_has_waiver(lines, call):
                continue
            violations.append(
                f"{rel}:{call.lineno}: {node.name} takes a `root` parameter but calls "
                f"{target}() without forwarding it -- pass {target}(root=root) (or root positionally), "
                f"or mark the line `{_WAIVER_MARKER} <reason>` if the bare call is deliberate"
            )
    return violations, scanned


def _scan_tree(paths: list[Path]) -> tuple[list[str], int]:
    violations: list[str] = []
    scanned = 0
    for path in paths:
        file_violations, file_scanned = _scan_file(path)
        violations.extend(file_violations)
        scanned += file_scanned
    return violations, scanned


@registry.register("validate_root_scoped_diff_base", owner="platform")
def validate_root_scoped_diff_base(failed: list[str]) -> None:
    """Scan scripts/checks/**/*.py for a root-taking function that calls a ROOT-pinned
    diff-base primitive (push_context_base / get_changed_files / get_status_aware_diff /
    status_diff_base) without forwarding its own `root`.

    Composes exactly ONE terminal Decision 170 declaration on each of its two reachable exit
    paths: a missing scripts/checks/ directory under ROOT `skipped()`s (could not examine);
    otherwise `examined(N, unit="root_taking_functions")`, N being the count of root-taking
    functions scanned (zero is a legitimate empty domain, not a skip).
    """
    print("\n=== Root-scoped diff-base guard (rec-3166) ===")
    checks_dir = _common.ROOT / "scripts" / "checks"
    if not checks_dir.exists():
        print(f"  SKIP: {checks_dir} not found.")
        registry.skipped("scripts/checks directory not found under ROOT")
        return

    paths = sorted(checks_dir.glob("**/*.py"))
    violations, scanned = _scan_tree(paths)
    if violations:
        print("Root-scoped diff-base violations found:")
        for v in violations:
            print(f"  - {v}")
        failed.append("Root-scoped diff-base guard")
    else:
        print(f"  PASS: {scanned} root-taking function(s) scanned across {len(paths)} file(s); no bare primitive calls.")
    registry.examined(scanned, unit="root_taking_functions")
