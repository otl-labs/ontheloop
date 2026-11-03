"""Lexical channel: the plain-literal terms a query file is known by (path, module, stem, basename, symbols).

Terms reach a SQL bind (Decision 84 I-3), so every term is lower-cased and restricted to [a-z0-9_./-] -- no comma or
pipe can corrupt the "term|weight" list. Symbols are read by AST (never imported or executed). Weights live with the
core (related_recs.TERM_WEIGHTS); this module only labels each term with its kind and caps a weighted set.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.recs_related.neighbourhood import module_of

KIND_STEM = "stem"
KIND_MODULE = "module"
KIND_BASENAME = "basename"
KIND_PATH = "path"
KIND_SYMBOL = "symbol"
KIND_USER = "user"

MAX_TERMS = 40
MAX_SYMBOLS = 40
MIN_SYMBOL_LENGTH = 5
MIN_STEM_LENGTH = 3
MIN_USER_TERM_LENGTH = 2

GENERIC_STEMS = frozenset({"__init__", "__main__", "conftest", "manifest", "skill", "handler", "claude", "readme"})

_DISALLOWED = re.compile(r"[^a-z0-9_./-]")


@dataclass(frozen=True)
class Term:
    text: str
    kind: str


def sanitise(raw: str) -> str:
    """Lower-case *raw* and drop every character outside [a-z0-9_./-] (free-text input)."""
    return _DISALLOWED.sub("", raw.lower())


def _clean(raw: str) -> str | None:
    """*raw* lower-cased, or None when it holds a character outside [a-z0-9_./-] (a derived term is never rewritten)."""
    lowered = raw.lower()
    return None if _DISALLOWED.search(lowered) else lowered


def _symbols(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and len(node.name) >= MIN_SYMBOL_LENGTH
    ]
    return names[:MAX_SYMBOLS]


def derive_terms(path: str, repo_root: Path) -> list[Term]:
    """The terms *path* is known by, deduplicated by text (first kind listed wins)."""
    pure = PurePosixPath(path)
    candidates: list[tuple[str | None, str]] = []
    if pure.stem.lower() not in GENERIC_STEMS and len(pure.stem) >= MIN_STEM_LENGTH:
        candidates.append((_clean(pure.stem), KIND_STEM))
    module = module_of(path)
    if module is not None:
        candidates.append((_clean(module), KIND_MODULE))
    if pure.stem.lower() not in GENERIC_STEMS:
        candidates.append((_clean(pure.name), KIND_BASENAME))
    candidates.append((_clean(path), KIND_PATH))
    if pure.suffix == ".py":
        candidates.extend((_clean(name), KIND_SYMBOL) for name in _symbols(repo_root / path))
    seen: set[str] = set()
    out: list[Term] = []
    for text, kind in candidates:
        if text and text not in seen:
            seen.add(text)
            out.append(Term(text, kind))
    return out


def free_text_terms(text: str) -> list[Term]:
    """Whitespace-separated free-text keywords, sanitised and deduplicated."""
    seen: set[str] = set()
    out: list[Term] = []
    for token in text.split():
        cleaned = sanitise(token)
        if len(cleaned) >= MIN_USER_TERM_LENGTH and cleaned not in seen:
            seen.add(cleaned)
            out.append(Term(cleaned, KIND_USER))
    return out


def cap_terms(weighted: dict[str, float], limit: int = MAX_TERMS) -> tuple[dict[str, float], bool]:
    """The *limit* heaviest terms (ties by text) and whether any were dropped -- truncation is reported, never silent."""
    ranked = sorted(weighted.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(ranked[:limit]), len(ranked) > limit
