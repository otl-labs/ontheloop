"""Text-reference channel: tracked files a query file's own text names (the references the import graph cannot see).

37% of rec anchors are yaml/md/yml/tf/sh, so imports alone miss them. Matching is precise -- a repo path, a quoted
token or a dotted module name, never a bare substring (Decision 135 cl.1(b)). The two patterns below are this
module's OWN; their source is scripts/checks/deps/affected_tests.py's _quoted_token_pattern (a whole quoted string,
optionally with a path prefix ending in '/') and _dotted_token_pattern (an identifier-boundary dotted name). Those are
private to Decision 135's selection module and are never imported here; promoting them would touch that module.
Outbound only: inbound mentions measured at zero lift.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from scripts.recs_related.envelope import CHANNEL_OK, unavailable

_QUOTED = re.compile(r"""['"]((?:[\w.\-]+/)*)([\w.\-]+)['"]""")
_PATH_TOKEN = re.compile(r"(?<![\w./\-])([\w.\-]+(?:/[\w.\-]+)+)")
_DOTTED = re.compile(r"(?<![\w.])((?:src|scripts|tests)(?:\.\w+)+)(?!\w)")
_MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class Mentions:
    paths: frozenset[str]
    status: str


def _basename_index(tracked: frozenset[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for t in tracked:
        index[posixpath.basename(t)].append(t)
    return index


def _module_to_path(dotted: str, tracked: frozenset[str]) -> str | None:
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        base = "/".join(parts[:end])
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if candidate in tracked:
                return candidate
    return None


def mentions_of(path: str, repo_root: Path, tracked: frozenset[str]) -> Mentions:
    """Tracked files named in *path*'s text; an untracked or unreadable file reports the channel unavailable."""
    if path not in tracked:
        return Mentions(frozenset(), unavailable(f"{path} is untracked, so there is no text to scan"))
    try:
        with (repo_root / path).open("rb") as handle:
            text = handle.read(_MAX_BYTES).decode("utf-8", errors="replace")
    except OSError as exc:
        return Mentions(frozenset(), unavailable(f"{path} is unreadable: {exc.strerror or exc}"))

    found: set[str] = set()
    for match in _PATH_TOKEN.finditer(text):
        candidate = match.group(1).rstrip(".")
        if candidate in tracked:
            found.add(candidate)
    by_name = _basename_index(tracked)
    for match in _QUOTED.finditer(text):
        prefix, name = match.group(1), match.group(2)
        if prefix:
            if f"{prefix}{name}" in tracked:
                found.add(f"{prefix}{name}")
        elif len(by_name.get(name, ())) == 1:
            found.add(by_name[name][0])
    for match in _DOTTED.finditer(text):
        resolved = _module_to_path(match.group(1), tracked)
        if resolved is not None:
            found.add(resolved)
    found.discard(path)
    return Mentions(frozenset(found), CHANNEL_OK)
