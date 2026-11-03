"""Shared pure AST helpers for the test-weakening surfacing checks (PLAN-test-weakening-surfacing,
rec-3357, Decision 181 point 2's narrowed residual).

Both scripts.checks.hygiene.validate_test_skip_citations and
scripts.checks.verification.validate_weakening_candidates need the SAME notion of "what is a skip
site" and "what changed between two versions of a test file" -- this module is their one shared
home (Decision 104's single-home precedent), never duplicated. Pure: no I/O, and never raises on
bad input -- unparseable source yields an empty result rather than a propagated SyntaxError, since
a check calling this on a diff's HEAD content must not crash on a file that fails to parse for
reasons outside this module's concern (that is validate_imports' jurisdiction, not this one's).

A SKIP SITE (``SkipSite``) is a skip/skipif/xfail pytest mark (decorator, module/class pytestmark
assignment, an alias assignment, a ``pytest.param(..., marks=...)`` entry, or an
``item.add_marker(...)`` argument -- called or bare), a unittest skip/skipIf/skipUnless/
expectedFailure decorator, a ``pytest.skip``/``pytest.xfail``/``pytest.importorskip`` call, a
``self.skipTest(...)`` call, or a ``raise unittest.SkipTest(...)``/``raise
pytest.skip.Exception(...)``. Names resolve through the module's own imports (``import pytest as
X``, ``from pytest import skip, xfail, importorskip, mark``, ``import unittest as X``, ``from
unittest import skip, skipIf, skipUnless, expectedFailure, SkipTest``) via a dotted-chain resolver,
not a hardcoded literal match on ``pytest``/``unittest`` -- except that a bare ``pytest``/
``unittest`` root is accepted even with no corresponding import statement in view (the overwhelmingly
common real-world case), so this module degrades gracefully rather than going blind on a file whose
import is in a parent scope this AST-only parse of one source string cannot see.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass

_PYTEST_MARK_SKIP_NAMES = frozenset({"skip", "skipif", "xfail"})
_UNITTEST_DECORATOR_NAMES = frozenset({"skip", "skipIf", "skipUnless", "expectedFailure"})
_PYTEST_BARE_CALL_NAMES = frozenset({"skip", "xfail", "importorskip"})

SKIP_KINDS = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
        "pytest.skip",
        "pytest.xfail",
        "pytest.importorskip",
        "unittest.SkipTest",
        "pytest.skip.Exception",
        "self.skipTest",
    }
)

_TOLERANCE_KEYWORDS = frozenset({"rel", "abs", "rel_tol", "abs_tol", "rtol", "atol", "delta"})
_TOLERANCE_CALLEES = frozenset({"approx", "isclose", "assertAlmostEqual", "allclose", "assert_allclose"})


@dataclass(frozen=True)
class SkipSite:
    kind: str
    line: int
    reason: str
    scope: str


class _Imports:
    """Resolved import aliases for one module, used to resolve a dotted-attribute chain's root
    to a canonical ``"pytest"``/``"unittest"`` domain plus the remaining (already-canonical)
    chain segments."""

    def __init__(self) -> None:
        self.pytest_alias: str = "pytest"
        self.unittest_alias: str = "unittest"
        self.pytest_direct: dict[str, str] = {}
        self.unittest_direct: dict[str, str] = {}

    @classmethod
    def from_tree(cls, tree: ast.Module) -> "_Imports":
        imports = cls()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pytest":
                        imports.pytest_alias = alias.asname or "pytest"
                    elif alias.name == "unittest":
                        imports.unittest_alias = alias.asname or "unittest"
            elif isinstance(node, ast.ImportFrom):
                if node.module == "pytest":
                    for alias in node.names:
                        if alias.name in {"skip", "xfail", "importorskip", "mark"}:
                            imports.pytest_direct[alias.asname or alias.name] = alias.name
                elif node.module == "unittest":
                    for alias in node.names:
                        if alias.name in {"skip", "skipIf", "skipUnless", "expectedFailure", "SkipTest"}:
                            imports.unittest_direct[alias.asname or alias.name] = alias.name
        return imports

    def resolve(self, chain: list[str]) -> tuple[str, list[str]] | None:
        if not chain:
            return None
        head, rest = chain[0], chain[1:]
        if head == self.pytest_alias:
            return ("pytest", rest)
        if head == self.unittest_alias:
            return ("unittest", rest)
        if head in self.pytest_direct:
            return ("pytest", [self.pytest_direct[head], *rest])
        if head in self.unittest_direct:
            return ("unittest", [self.unittest_direct[head], *rest])
        return None


def _dotted_chain(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def _kind_from_resolved(resolved: tuple[str, list[str]]) -> str | None:
    root, rest = resolved
    if root == "pytest":
        if rest == ["mark", "skip"]:
            return "pytest.mark.skip"
        if rest == ["mark", "skipif"]:
            return "pytest.mark.skipif"
        if rest == ["mark", "xfail"]:
            return "pytest.mark.xfail"
        if rest == ["skip"]:
            return "pytest.skip"
        if rest == ["xfail"]:
            return "pytest.xfail"
        if rest == ["importorskip"]:
            return "pytest.importorskip"
        if rest == ["skip", "Exception"]:
            return "pytest.skip.Exception"
        if rest == ["param"]:
            return "pytest.param"
        return None
    if root == "unittest":
        if rest == ["skip"]:
            return "unittest.skip"
        if rest == ["skipIf"]:
            return "unittest.skipIf"
        if rest == ["skipUnless"]:
            return "unittest.skipUnless"
        if rest == ["expectedFailure"]:
            return "unittest.expectedFailure"
        if rest == ["SkipTest"]:
            return "unittest.SkipTest"
        return None
    return None


def _const_str(node: ast.expr | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return ""


def _reason_from_call(call: ast.Call | None, kind: str) -> str:
    if call is None:
        return ""
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    if "reason" in kw:
        return _const_str(kw["reason"])
    args = call.args
    if kind == "pytest.importorskip":
        idx = 2
    elif kind in {"pytest.mark.skipif", "pytest.mark.xfail", "unittest.skipIf", "unittest.skipUnless"}:
        idx = 1
    else:
        idx = 0
    return _const_str(args[idx]) if len(args) > idx else ""


def _immediate_containers(
    node: ast.AST,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None, ast.ClassDef | None]:
    cur = getattr(node, "parent", None)
    while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        cur = getattr(cur, "parent", None)
    if cur is None:
        return None, None
    if isinstance(cur, ast.ClassDef):
        return cur, None
    parent = getattr(cur, "parent", None)
    while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        parent = getattr(parent, "parent", None)
    enclosing_class = parent if isinstance(parent, ast.ClassDef) else None
    return cur, enclosing_class


def _scope_of(node: ast.AST) -> str:
    container, enclosing_class = _immediate_containers(node)
    if container is None:
        return "*"
    if isinstance(container, ast.ClassDef):
        return container.name
    is_test = container.name.startswith("test") and (enclosing_class is None or enclosing_class.name.startswith("Test"))
    if is_test:
        return f"{enclosing_class.name}::{container.name}" if enclosing_class else container.name
    return "*"


def _mark_candidates_in(value: ast.expr) -> list[ast.expr]:
    if isinstance(value, (ast.List, ast.Tuple)):
        return list(value.elts)
    return [value]


def _record_if_mark(expr: ast.expr, imports: _Imports, add_fn, line: int | None = None) -> None:
    call = expr if isinstance(expr, ast.Call) else None
    func_expr = call.func if call is not None else expr
    chain = _dotted_chain(func_expr)
    if chain is None:
        return
    resolved = imports.resolve(chain)
    if resolved is None:
        return
    kind = _kind_from_resolved(resolved)
    if kind not in {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}:
        return
    add_fn(expr, kind, _reason_from_call(call, kind), line=line if line is not None else expr.lineno)


def _is_self_skip_test_call(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "skipTest"
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    )


def _handle_call(node: ast.Call, imports: _Imports, add_fn) -> None:
    if _is_self_skip_test_call(node):
        add_fn(node, "self.skipTest", _reason_from_call(node, "self.skipTest"))
        return
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "add_marker" and node.args:
        _record_if_mark(node.args[0], imports, add_fn, line=node.lineno)
        return
    chain = _dotted_chain(func)
    if chain is None:
        return
    resolved = imports.resolve(chain)
    if resolved is None:
        return
    kind = _kind_from_resolved(resolved)
    if kind == "pytest.param":
        for kw in node.keywords:
            if kw.arg == "marks":
                for cand in _mark_candidates_in(kw.value):
                    _record_if_mark(cand, imports, add_fn)
        return
    if kind in SKIP_KINDS:
        add_fn(node, kind, _reason_from_call(node, kind))


def _handle_bare_attribute(node: ast.Attribute, imports: _Imports, add_fn) -> None:
    parent = getattr(node, "parent", None)
    if isinstance(parent, ast.Call) and parent.func is node:
        return
    chain = _dotted_chain(node)
    if chain is None:
        return
    resolved = imports.resolve(chain)
    if resolved is None:
        return
    kind = _kind_from_resolved(resolved)
    if kind in SKIP_KINDS:
        add_fn(node, kind, "")


def _handle_raise(node: ast.Raise, exc: ast.expr, imports: _Imports, add_fn) -> None:
    call = exc if isinstance(exc, ast.Call) else None
    func_expr = call.func if call is not None else exc
    chain = _dotted_chain(func_expr)
    if chain is None:
        return
    resolved = imports.resolve(chain)
    if resolved is None:
        return
    kind = _kind_from_resolved(resolved)
    if kind not in {"unittest.SkipTest", "pytest.skip.Exception"}:
        return
    add_fn(node, kind, _reason_from_call(call, kind), line=node.lineno)


def skip_sites(source: str) -> list[SkipSite]:
    """Every skip/xfail/importorskip SITE in `source`, with its kind, reason and scope.

    Never raises: unparseable source yields an empty list.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]

    imports = _Imports.from_tree(tree)
    sites: list[SkipSite] = []
    seen: set[int] = set()

    def add(node: ast.expr | ast.stmt, kind: str, reason: str, line: int | None = None) -> None:
        key = id(node)
        if key in seen:
            return
        seen.add(key)
        sites.append(SkipSite(kind=kind, line=line if line is not None else node.lineno, reason=reason, scope=_scope_of(node)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _handle_call(node, imports, add)
        elif isinstance(node, ast.Attribute):
            _handle_bare_attribute(node, imports, add)
        elif isinstance(node, ast.Raise) and node.exc is not None:
            _handle_raise(node, node.exc, imports, add)
        elif isinstance(node, ast.Assign):
            for cand in _mark_candidates_in(node.value):
                _record_if_mark(cand, imports, add)

    return sites


def applicable_skip_sites(source: str, key: str) -> list[SkipSite]:
    """Sites applying to test `key` ("func" or "Class::func"): its own scope, its class's
    (when `key` carries one), and the module scope "*"."""
    sites = skip_sites(source)
    scopes = {key, "*"}
    if "::" in key:
        scopes.add(key.split("::", 1)[0])
    return [s for s in sites if s.scope in scopes]


def _known_scopes_at(source: str | None) -> set[str]:
    scopes: set[str] = {"*"}
    if source is None:
        return scopes
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return scopes
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.add(stmt.name)
        elif isinstance(stmt, ast.ClassDef):
            scopes.add(stmt.name)
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scopes.add(f"{stmt.name}::{sub.name}")
    return scopes


def added_skip_sites(files: dict[str, tuple[str | None, str | None]]) -> dict[str, list[SkipSite]]:
    """{path: [SkipSite]} of sites ADDED at head relative to base, over every (base, head) pair in
    `files` (one entry per changed test file at once -- the single home both checks use).

    Added = head's sites minus base's (a multiset keyed by scope/kind/reason), less any site
    CARRIED from another changed-or-deleted file in the SAME diff: a site matches (scope, kind,
    reason) a site removed elsewhere, and that scope did not already exist in THIS file at base
    (a rename/decomposition moving a skip with its tests adds none; a skip carried onto a
    pre-existing scope in the destination is added).
    """
    head_sites_by_file: dict[str, list[SkipSite]] = {}
    base_counter_by_file: dict[str, Counter[tuple[str, str, str]]] = {}
    base_scopes_by_file: dict[str, set[str]] = {}

    for path, (base_src, head_src) in files.items():
        base_sites = skip_sites(base_src) if base_src is not None else []
        head_sites_by_file[path] = skip_sites(head_src) if head_src is not None else []
        base_counter_by_file[path] = Counter((s.scope, s.kind, s.reason) for s in base_sites)
        base_scopes_by_file[path] = _known_scopes_at(base_src)

    removed_pool: Counter[tuple[str, str, str]] = Counter()
    removed_from: dict[tuple[str, str, str], set[str]] = {}
    for path in files:
        head_counter = Counter((s.scope, s.kind, s.reason) for s in head_sites_by_file[path])
        removed = base_counter_by_file[path] - head_counter
        for key, count in removed.items():
            removed_pool[key] += count
            removed_from.setdefault(key, set()).add(path)

    result: dict[str, list[SkipSite]] = {}
    for path, (_base_src, head_src) in files.items():
        if head_src is None:
            continue
        head_counter = Counter((s.scope, s.kind, s.reason) for s in head_sites_by_file[path])
        added_counter = head_counter - base_counter_by_file[path]
        if not added_counter:
            continue
        remaining = Counter(added_counter)
        added_list: list[SkipSite] = []
        for site in head_sites_by_file[path]:
            key = (site.scope, site.kind, site.reason)
            if remaining.get(key, 0) <= 0:
                continue
            is_carried = (
                removed_pool.get(key, 0) > 0
                and bool(removed_from.get(key, set()) - {path})
                and site.scope not in base_scopes_by_file[path]
            )
            if is_carried:
                removed_pool[key] -= 1
            else:
                added_list.append(site)
            remaining[key] -= 1
        if added_list:
            result[path] = added_list
    return result


def test_functions(source: str) -> dict[str, ast.AST]:
    """{key: node} for module-level test* functions and test* methods of top-level Test* classes."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name.startswith("test"):
            out[stmt.name] = stmt
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out[f"{stmt.name}::{sub.name}"] = sub
    return out


def _call_display_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def assertions(node: ast.AST) -> Counter[tuple[str, str]]:
    """Counter of (normalized-dump, display-string) for every `assert` statement's test and every
    call whose callee name starts with "assert" or is raises/warns/approx/isclose, found anywhere
    under `node` (including nested helper calls)."""
    counter: Counter[tuple[str, str]] = Counter()
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            counter[(ast.dump(child.test), ast.unparse(child.test).splitlines()[0])] += 1
        elif isinstance(child, ast.Call):
            name = _call_display_name(child.func)
            if name is not None and (name.startswith("assert") or name in {"raises", "warns", "approx", "isclose"}):
                counter[(ast.dump(child), ast.unparse(child).splitlines()[0])] += 1
    return counter


def _numeric_literal(node: ast.expr) -> float | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric_literal(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    return None


def tolerances(node: ast.AST) -> dict[tuple[str, str, int], float]:
    """{(callee, keyword, occurrence): value} for every tolerance keyword on a tolerance-bearing
    call found under `node`."""
    out: dict[tuple[str, str, int], float] = {}
    occurrence: Counter[tuple[str, str]] = Counter()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_display_name(child.func)
        if name not in _TOLERANCE_CALLEES:
            continue
        for kw in child.keywords:
            if kw.arg in _TOLERANCE_KEYWORDS:
                value = _numeric_literal(kw.value)
                if value is None:
                    continue
                key2 = (name, kw.arg)
                out[(name, kw.arg, occurrence[key2])] = value
                occurrence[key2] += 1
    return out


def numeric_constants(source: str) -> dict[str, float]:
    """{name: value} for module-level UPPER_CASE names bound to an int/float literal."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, float] = {}
    for stmt in tree.body:
        name: str | None = None
        value_node: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name, value_node = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            name, value_node = stmt.target.id, stmt.value
        if name is None or value_node is None or not name.isupper():
            continue
        value = _numeric_literal(value_node)
        if value is not None:
            out[name] = value
    return out
