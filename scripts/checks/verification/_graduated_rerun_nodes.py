"""Static pytest node-id resolution for the graduated-record standing re-run (rec-4831, rec-4115's design).

Pure ast: no pytest import, no collection. A node id "path.py[::Class[::Nested]]::function[param]" resolves
when the file exists and the Class/function segments are found by an ast walk (a "[...]" parameter suffix is
stripped, so a stale parameter id still resolves -- a declared residual). Flags are skipped, and the value
of a flag that takes one (-k, -m, ...) is skipped with it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_VALUED_FLAGS = frozenset(
    {
        "-k",
        "-m",
        "-p",
        "-n",
        "-c",
        "-o",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--maxfail",
        "--rootdir",
        "--basetemp",
        "--junitxml",
        "--durations",
        "--timeout",
    }
)
_TRIVIAL_BASES = frozenset({"object", "TestCase", "unittest.TestCase"})


def node_arguments(args: list[str]) -> list[str]:
    """The path/node-id arguments of a pytest argument list, flags and flag values removed."""
    nodes: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            skip_next = arg in _VALUED_FLAGS
            continue
        nodes.append(arg)
    return nodes


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _is_integration_marker(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_is_integration_marker(elt) for elt in node.elts)
    return _dotted(node).endswith("mark.integration")


def _module_marked(tree: ast.Module) -> bool:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            if _is_integration_marker(stmt.value):
                return True
    return False


def _decorated(node: ast.AST) -> bool:
    return any(_is_integration_marker(d) for d in getattr(node, "decorator_list", []))


def _class_body_marked(cls: ast.ClassDef) -> bool:
    if _decorated(cls):
        return True
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            if _is_integration_marker(stmt.value):
                return True
    return False


def _any_integration(tree: ast.Module) -> bool:
    if _module_marked(tree):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _class_body_marked(node):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _decorated(node):
            return True
    return False


def _find_member(body: list[ast.stmt], name: str) -> ast.AST | None:
    for stmt in body:
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def _has_foreign_base(cls: ast.ClassDef, tree: ast.Module, seen: frozenset[str] = frozenset()) -> bool:
    for base in cls.bases:
        name = _dotted(base)
        if name in _TRIVIAL_BASES:
            continue
        local = _find_member(tree.body, name)
        if isinstance(local, ast.ClassDef) and local.name not in seen:
            if _has_foreign_base(local, tree, seen | {cls.name}):
                return True
            continue
        return True
    return False


def _inherited_member(cls: ast.ClassDef, tree: ast.Module, name: str, seen: frozenset[str] = frozenset()) -> bool:
    for base in cls.bases:
        local = _find_member(tree.body, _dotted(base))
        if isinstance(local, ast.ClassDef) and local.name not in seen:
            if _find_member(local.body, name) is not None or _inherited_member(local, tree, name, seen | {cls.name}):
                return True
    return False


def _walk_segments(tree: ast.Module, segments: list[str]) -> tuple[bool, list[ast.AST]]:
    """(resolved, nodes along the path). A class with a foreign base resolves leniently."""
    body = tree.body
    chain: list[ast.AST] = []
    for index, segment in enumerate(segments):
        member = _find_member(body, segment)
        if member is None:
            owner = chain[-1] if chain else None
            if isinstance(owner, ast.ClassDef) and (_inherited_member(owner, tree, segment) or _has_foreign_base(owner, tree)):
                return True, chain
            return False, chain
        chain.append(member)
        if index < len(segments) - 1:
            if not isinstance(member, ast.ClassDef):
                return False, chain
            body = member.body
    return True, chain


def _split(node_id: str) -> tuple[str, list[str]]:
    parts = node_id.split("::")
    segments = [p.split("[", 1)[0] for p in parts[1:]]
    return parts[0], segments


def unresolved_nodes(args: list[str], root: Path) -> list[str]:
    """The node ids among `args` that do not resolve against the tree at `root`."""
    missing: list[str] = []
    for node_id in node_arguments(args):
        file_part, segments = _split(node_id)
        target = root / file_part
        if not target.exists():
            missing.append(node_id)
            continue
        if not segments:
            continue
        tree = _parse(target) if target.is_file() else None
        if tree is None:
            missing.append(node_id)
            continue
        ok, _ = _walk_segments(tree, segments)
        if not ok:
            missing.append(node_id)
    return missing


def can_select_integration(args: list[str], root: Path) -> bool:
    """Whether some argument can select an integration-marked test (a -k expression is not evaluated)."""
    for node_id in node_arguments(args):
        file_part, segments = _split(node_id)
        target = root / file_part
        files = [target] if target.is_file() else sorted(target.rglob("*.py")) if target.is_dir() else []
        for path in files:
            tree = _parse(path)
            if tree is None:
                continue
            if not segments:
                if _any_integration(tree):
                    return True
                continue
            if _module_marked(tree):
                return True
            ok, chain = _walk_segments(tree, segments)
            if ok and any(isinstance(n, ast.ClassDef) and _class_body_marked(n) or _decorated(n) for n in chain):
                return True
    return False
