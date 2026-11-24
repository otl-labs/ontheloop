"""kind test_node: test nodes whose literal closure holds a victim number and that sit in a file reading the log."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.checks.decisions.consolidation_inventory._model import Evidence, Gate, Hit, make_hit, note

SOURCE_BASENAMES = ("DECISIONS.md", "DECISIONS_ARCHIVE.md", "decisions-index.json")
SOURCE_MODULES = (
    "scripts.decisions_md",
    "scripts.decisions_index",
    "scripts.checks._marker_guard",
    "tests.fixtures.decision_entry",
)
_DECISIONS_NUMS = re.compile(r"Decisions?\s+(\d+(?:\s*(?:,|and|/|&|\+)\s*\d+)*)")
_DEC_ID = re.compile(r"dec-0*(\d+)")
_NUM = re.compile(r"\d+")
_PREFILTER = SOURCE_BASENAMES + SOURCE_MODULES + ("decision_entry",)
_SKIP_DIRS = frozenset({"__pycache__", ".venv", "node_modules"})


def _constant_literals(value: object) -> set[int]:
    found: set[int] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int):
        found.add(value)
    elif isinstance(value, str):
        for match in _DECISIONS_NUMS.finditer(value):
            found.update(int(n) for n in _NUM.findall(match.group(1)))
        found.update(int(n) for n in _DEC_ID.findall(value))
    return found


def literals(node: ast.AST) -> set[int]:
    """The int constants and the Decision numbers named in string constants under node."""
    found: set[int] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant):
            found |= _constant_literals(sub.value)
    return found


def _source_names(tree: ast.Module) -> set[str]:
    """Local names bound by an import of a decisions reader module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in SOURCE_MODULES:
                names.update(a.asname or a.name for a in node.names)
            else:
                for alias in node.names:
                    if f"{node.module}.{alias.name}" in SOURCE_MODULES:
                        names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in SOURCE_MODULES:
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _has_source_constant(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Constant) and isinstance(sub.value, str) and any(b in sub.value for b in SOURCE_BASENAMES)
        for sub in ast.walk(node)
    )


def _facts(node: ast.AST) -> tuple[set[str], set[int], bool]:
    """(names, victim-number literals, holds a decisions-source constant) from one walk."""
    names: set[str] = set()
    lits: set[int] = set()
    source = False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.Constant):
            lits |= _constant_literals(sub.value)
            if isinstance(sub.value, str) and any(b in sub.value for b in SOURCE_BASENAMES):
                source = True
    return names, lits, source


class _Facts:
    def __init__(self) -> None:
        self._cache: dict[int, tuple[set[str], set[int], bool]] = {}

    def get(self, node: ast.AST) -> tuple[set[str], set[int], bool]:
        key = id(node)
        if key not in self._cache:
            self._cache[key] = _facts(node)
        return self._cache[key]


class _Module:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.source_names = _source_names(tree)
        self.facts = _Facts()
        self.defs: dict[str, list[ast.AST]] = {}
        self.class_attrs: dict[str, dict[str, list[ast.AST]]] = {}
        for stmt in tree.body:
            for name in self._bound(stmt):
                self.defs.setdefault(name, []).append(stmt)
            if isinstance(stmt, ast.ClassDef):
                attrs: dict[str, list[ast.AST]] = {}
                for inner in stmt.body:
                    for name in self._bound(inner):
                        attrs.setdefault(name, []).append(inner)
                self.class_attrs[stmt.name] = attrs

    @staticmethod
    def _bound(stmt: ast.AST) -> list[str]:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return [stmt.name]
        if isinstance(stmt, ast.Assign):
            return [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            return [stmt.target.id]
        return []

    def closure(self, node: ast.AST, owner: str | None) -> list[ast.AST]:
        """The node plus every module-level assignment, function or class attribute it names, transitively."""
        seen: set[int] = {id(node)}
        out: list[ast.AST] = [node]
        queue: list[ast.AST] = [node]
        while queue:
            current = queue.pop()
            for name in self.facts.get(current)[0]:
                for target in self.defs.get(name, []) + (self.class_attrs.get(owner or "", {}).get(name, [])):
                    if id(target) not in seen:
                        seen.add(id(target))
                        out.append(target)
                        queue.append(target)
        return out

    def holds_source(self, nodes: list[ast.AST]) -> bool:
        for node in nodes:
            names, _, has_constant = self.facts.get(node)
            if has_constant or (self.source_names and names & self.source_names):
                return True
        return False


def _test_nodes(tree: ast.Module) -> list[tuple[str, str | None, ast.AST]]:
    found: list[tuple[str, str | None, ast.AST]] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name.startswith("test"):
            found.append((stmt.name, None, stmt))
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            for inner in stmt.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner.name.startswith("test"):
                    found.append((f"{stmt.name}::{inner.name}", stmt.name, inner))
    return found


def test_files(root: Path) -> list[Path]:
    base = root / "tests"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.py") if not (set(p.relative_to(root).parts) & _SKIP_DIRS))


def scan(root: Path, victims: tuple[int, ...], gate: Gate, notes: list[dict[str, str]]) -> list[Hit]:
    hits: list[Hit] = []
    wanted = set(victims)
    for path in test_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            note(notes, rel, "unreadable: skipped")
            continue
        if not any(token in text for token in _PREFILTER):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            note(notes, rel, "syntax error: skipped")
            continue
        module = _Module(tree)
        if not (_has_source_constant(tree) or module.source_names):
            continue
        for node_id, owner, node in _test_nodes(tree):
            closure = module.closure(node, owner)
            found: set[int] = set()
            for part in closure:
                found |= module.facts.get(part)[1] & wanted
            if not found:
                continue
            dataflow = module.holds_source(closure)
            for victim in sorted(found):
                if dataflow:
                    ev = Evidence(
                        "dataflow_literal", f"Decision {victim} literal in a node whose closure opens a decisions source"
                    )
                else:
                    ev = Evidence(
                        "cooccurrence", f"Decision {victim} literal in a node of a file that names a decisions source"
                    )
                hits.append(make_hit("test_node", victim, f"{rel}::{node_id}", [ev], gate.is_gated(rel)))
    return hits
