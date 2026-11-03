"""Structural channel: the files one hop from a query file (generic over files, no recommendation vocabulary).

Uses scripts.dependency_graph's PUBLIC API only (build_graph, import_subgraph, edges); that module is not modified,
so Decision 135's affected-set derivation is untouched. The tests mirror reads scripts.test_coverage_checker's
map_source_to_test (Decision 131's naming source, read-only), plus the tests/**/test_<stem>.py stem rule because
map_source_to_test returns None for unregistered subpackages (gap owned by rec-2641).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts import dependency_graph as dg
from scripts.recs_related.envelope import CHANNEL_OK, unavailable
from scripts.test_coverage_checker import map_source_to_test

RELATION_UPSTREAM = "upstream"
RELATION_DOWNSTREAM = "downstream"
RELATION_PATCH_STRING = "patch_string"
RELATION_SIBLING = "sibling"
RELATION_TESTS_MIRROR = "tests_mirror"

_MODULE_ROOTS = ("src", "scripts", "tests")


@dataclass(frozen=True)
class Neighbours:
    """Neighbour path -> the relations that reach it, plus the import channel's own status."""

    relations: dict[str, frozenset[str]]
    imports: str


def module_of(path: str) -> str | None:
    """The dotted first-party module name of a repo-relative .py path, or None outside src/scripts/tests."""
    p = PurePosixPath(path)
    if p.suffix != ".py" or not p.parts or p.parts[0] not in _MODULE_ROOTS:
        return None
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def path_of(module: str, tracked: frozenset[str]) -> str | None:
    """The tracked file a dotted module name resolves to (module file, else package __init__), or None."""
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if candidate in tracked:
            return candidate
    return None


def _siblings(path: str, tracked: frozenset[str]) -> set[str]:
    here = posixpath.dirname(path)
    return {t for t in tracked if t != path and posixpath.dirname(t) == here}


def _tests_mirror(path: str, repo_root: Path, tracked: frozenset[str]) -> set[str]:
    if not path.endswith(".py") or path.startswith("tests/"):
        return set()
    found: set[str] = set()
    mapped = map_source_to_test(repo_root / path)
    if mapped is not None:
        try:
            rel = mapped.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = ""
        if rel in tracked:
            found.add(rel)
        elif rel:
            found.update(t for t in tracked if t.startswith(f"{rel}/") and PurePosixPath(t).name.startswith("test_"))
    stem = PurePosixPath(path).stem
    if stem != "__init__":
        found.update(t for t in tracked if t.startswith("tests/") and PurePosixPath(t).name == f"test_{stem}.py")
    return found


def _import_edges(path: str, repo_root: Path, tracked: frozenset[str]) -> tuple[dict[str, set[str]], str]:
    out: dict[str, set[str]] = {}
    module = module_of(path)
    if module is None:
        return out, CHANNEL_OK
    if path not in tracked:
        return out, unavailable(f"{path} is untracked, so it has no import edges")
    try:
        graph = dg.build_graph(repo_root)
    except (ImportError, OSError) as exc:
        return out, unavailable(f"import graph could not be built: {exc}")
    if module not in graph:
        return out, unavailable(f"{module} is not a node of the import graph")

    def add(neighbour: str, relation: str) -> None:
        resolved = path_of(neighbour, tracked)
        if resolved is not None and resolved != path:
            out.setdefault(resolved, set()).add(relation)

    imports = dg.import_subgraph(graph)
    for succ in imports.successors(module):
        add(succ, RELATION_UPSTREAM)
    for pred in imports.predecessors(module):
        add(pred, RELATION_DOWNSTREAM)
    for _, succ, data in graph.out_edges(module, data=True):
        if data.get("kind") == dg.EDGE_KIND_PATCH_STRING:
            add(succ, RELATION_PATCH_STRING)
    for pred, _, data in graph.in_edges(module, data=True):
        if data.get("kind") == dg.EDGE_KIND_PATCH_STRING:
            add(pred, RELATION_PATCH_STRING)
    return out, CHANNEL_OK


def neighbours_of(path: str, repo_root: Path, tracked: frozenset[str]) -> Neighbours:
    """Imports (up and down, 1 hop), patch_string edges, package siblings and the tests mirror of *path*.

    An untracked (e.g. not-yet-created) *path* still yields siblings and the stem-rule tests mirror, with the import
    channel reported unavailable for it.
    """
    relations, status = _import_edges(path, repo_root, tracked)
    for sibling in _siblings(path, tracked):
        relations.setdefault(sibling, set()).add(RELATION_SIBLING)
    for test in _tests_mirror(path, repo_root, tracked):
        relations.setdefault(test, set()).add(RELATION_TESTS_MIRROR)
    return Neighbours({p: frozenset(r) for p, r in relations.items()}, status)
