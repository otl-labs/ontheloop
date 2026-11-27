"""The code_reviewer reference_hits provider: every file and line that names what the change touches (rec-5724).

Pure and read-only: the head snapshot is walked and read as bytes, never imported, executed or installed. Terms are each
changed path, the dotted module name of a changed .py under scripts/ or src/, and each top-level or class-level def or
class name in a changed .py that the change edits or removes. One whole-tree pass matches the escaped terms as whole words.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_HITS_PER_TERM = 50
MAX_TERMS = 150
MIN_NAME = 5
PER_FILE_KEPT = 3
PRIORITY = ("scripts/", "src/", "tests/", ".github/")
PLANS = "docs/plans/"
BUNDLE_HEADER = re.compile(r"^=== (.+) ===$", re.MULTILINE)
WORD = r"A-Za-z0-9_"


def _priority(path: str) -> int:
    if path.startswith(PLANS):
        return len(PRIORITY) + 1
    for rank, prefix in enumerate(PRIORITY):
        if path.startswith(prefix):
            return rank
    return len(PRIORITY)


def _defs(tree: ast.AST) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []

    def span(node: Any) -> tuple[int, int]:
        start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
        return start, int(node.end_lineno or node.lineno)

    def take(body: list[ast.stmt], nested: bool) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                start, end = span(node)
                out.append((node.name, start, end))
                if isinstance(node, ast.ClassDef) and not nested:
                    take(node.body, True)

    take(getattr(tree, "body", []), False)
    return out


def _parse(text: str | None) -> list[tuple[str, int, int]] | None:
    if text is None:
        return None
    try:
        return _defs(ast.parse(text))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _changed_head_lines(old: str | None, new: str) -> set[int]:
    if old is None:
        return set(range(1, new.count("\n") + 2))
    matcher = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"), autojunk=False)
    lines: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            lines.update(range(j1 + 1, j2 + 1))
    return lines


def _module_name(path: str) -> str | None:
    if not path.endswith(".py") or not path.startswith(("scripts/", "src/")):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _read_text(path: Path) -> str | None:
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES or b"\x00" in data[:8192]:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _terms(root: Path, files: list[dict[str, Any]], merge_base_texts: dict[str, Any]) -> list[tuple[str, str, str]]:
    terms: dict[str, tuple[str, str]] = {}

    def add(term: str, kind: str, source: str) -> None:
        if term and term not in terms:
            terms[term] = (kind, source)

    for item in files:
        path = str(item["filename"])
        add(path, "path", path)
        if item.get("previous_filename"):
            add(str(item["previous_filename"]), "path", str(item["previous_filename"]))
        module = _module_name(path)
        if module:
            add(module, "module", path)
    for item in files:
        path = str(item["filename"])
        if not path.endswith(".py"):
            continue
        head_text = None if item["status"] == "removed" else _read_text(root / path)
        old = merge_base_texts.get(item.get("previous_filename") or path)
        old = old if isinstance(old, str) else None
        head_defs = _parse(head_text) or []
        old_defs = _parse(old) or []
        names_at_head = {name for name, _, _ in head_defs}
        edited = _changed_head_lines(old, head_text) if head_text is not None else set()
        for name, start, end in head_defs:
            if any(start <= line <= end for line in edited):
                add(name, "symbol", path)
        for name, _, _ in old_defs:
            if name not in names_at_head:
                add(name, "removed_symbol", path)
    kept = [(t, k, s) for t, (k, s) in terms.items() if k in ("path", "module") or _name_ok(t)]
    return kept[:MAX_TERMS]


def _name_ok(name: str) -> bool:
    return len(name) >= MIN_NAME and not (name.startswith("__") and name.endswith("__"))


def _rank(per_file: dict[str, list[int]]) -> list[tuple[str, int]]:
    ordered = sorted(per_file, key=lambda p: (_priority(p), p))
    out: list[tuple[str, int]] = []
    for depth in range(PER_FILE_KEPT):
        for path in ordered:
            if depth < len(per_file[path]):
                out.append((path, per_file[path][depth]))
    return out


def reference_hits(
    root: Path,
    files: list[dict[str, Any]],
    merge_base_texts: dict[str, Any],
    review_scope_files: str,
    plan_path: str | None = None,
) -> str:
    terms = _terms(root, files, merge_base_texts)
    in_bundle = set(BUNDLE_HEADER.findall(review_scope_files))
    found: dict[str, dict[str, list[int]]] = {t: {} for t, _, _ in terms}
    totals: dict[str, int] = dict.fromkeys(found, 0)
    scanned = skipped = 0
    if terms:
        ordered = sorted((t for t, _, _ in terms), key=len, reverse=True)
        combined = re.compile(f"(?<![{WORD}])(?:" + "|".join(re.escape(t) for t in ordered) + f")(?![{WORD}])")
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != ".git" and not os.path.islink(os.path.join(current, d)))
            for name in sorted(names):
                full = Path(current) / name
                rel = full.relative_to(root).as_posix()
                if rel == plan_path:
                    skipped += 1
                    continue
                text = _read_text(full)
                if text is None:
                    skipped += 1
                    continue
                scanned += 1
                if not combined.search(text):
                    continue
                for number, line in enumerate(text.split("\n"), 1):
                    for match in set(combined.findall(line)):
                        totals[match] += 1
                        lines = found[match].setdefault(rel, [])
                        if len(lines) < PER_FILE_KEPT:
                            lines.append(number)
    out_terms = []
    for term, kind, source in terms:
        ranked = _rank(found[term])
        shown = ranked[:MAX_HITS_PER_TERM]
        out_terms.append(
            {
                "term": term,
                "kind": kind,
                "source": source,
                "hits": [{"path": p, "line": n, "in_bundle": p in in_bundle} for p, n in shown],
                "truncated": totals[term] > len(shown),
            }
        )
    return json.dumps({"terms": out_terms, "files_scanned": scanned, "files_skipped": skipped}, sort_keys=True)
