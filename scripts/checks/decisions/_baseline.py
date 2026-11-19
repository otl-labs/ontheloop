"""Shared origin/main decision baseline readers (PLAN-decision-entry-flow-governance,
Decision 153 fast-tier budget).

Single git-read primitive for the --pre consumers that need origin/main decision state --
validate_decision_entry_conformance (new-vs-baseline routing) and validate_decisions_size (the
per-entry cap's new-entry classification). Memoized per root so a single --pre run pays
for exactly one `git show` per file, not one per consumer.

Reachability delegates to scripts.checks._common.origin_main_reachable -- never a private
duplicate (Decision 134 clause 3 shared-parser mandate; this module supersedes the former
private _origin_main_reachable in validate_decision_entry_conformance.py, deleted rather than
relocated).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from scripts.checks import _common
from scripts.decisions_md import iter_decision_headings, iter_decision_sections

_DECISIONS_REL_PATHS = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")

# Shared injection-seam type for every --pre consumer's baseline_reader parameter (mirrors the
# vp_replay / graduation_completeness precedents) -- defined once here so
# validate_decision_entry_conformance and validate_decisions_size import the identical alias
# instead of each declaring their own.
BaselineReaderFn = Callable[[Path], Optional[set[int]]]

# Companion seam for the body reader: per-corpus-file {decision number -> heading-inclusive
# raw block} at origin/main. Keyed by the same repo-relative path strings as
# _DECISIONS_REL_PATHS so a consumer can tell a live-file baseline from an archive one.
BaselineBodies = dict[str, dict[int, str]]
BaselineBodyReaderFn = Callable[[Path], Optional[BaselineBodies]]

_cache: dict[Path, Optional[set[int]]] = {}
_file_cache: dict[tuple[Path, str], Optional[str]] = {}
_body_cache: dict[Path, Optional[BaselineBodies]] = {}


def baseline_decision_numbers(root: Path) -> Optional[set[int]]:
    """Decision numbers present at origin/main, via the shared '#{2,3}' grammar.

    Returns None (advisory-skip sentinel) when origin/main cannot be resolved at all -- a
    detached/shallow clone, a throwaway test repo with no remote, or plain unreachability.
    Memoized per root: a second call with the same root returns the cached result without a
    further git invocation.
    """
    if root in _cache:
        return _cache[root]
    if not _common.origin_main_reachable(root):
        _cache[root] = None
        return None
    numbers: set[int] = set()
    for rel in _DECISIONS_REL_PATHS:
        result = _common.run(
            ["git", "show", f"origin/main:{rel}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=root,
        )
        if result.returncode != 0:
            continue  # file absent at origin/main (new file) -- not an unreachable-baseline case
        numbers.update(int(m.group(1)) for m in iter_decision_headings(result.stdout))
    _cache[root] = numbers
    return numbers


def baseline_decision_bodies(root: Path) -> Optional[BaselineBodies]:
    """Per-corpus-file {decision number -> heading-inclusive raw block} at origin/main.

    The body counterpart to baseline_decision_numbers, for consumers that must compare a
    current body against its baseline text rather than merely test number membership. Blocks
    come from the shared iter_decision_sections grammar -- never a private header regex.

    Returns None on the same advisory-skip sentinel as baseline_decision_numbers (origin/main
    unresolvable at all). A corpus file that simply does not exist at origin/main yields an
    empty dict for that path, which is distinct from the None sentinel: absent-at-baseline is
    a real, representable state (every number in it is new), unreachable-baseline is not.
    Memoized per root, independently of the numbers cache.
    """
    if root in _body_cache:
        return _body_cache[root]
    if not _common.origin_main_reachable(root):
        _body_cache[root] = None
        return None
    bodies: BaselineBodies = {}
    for rel in _DECISIONS_REL_PATHS:
        text = base_file_text(root, rel)
        bodies[rel] = {int(m.group(1)): block for m, block in iter_decision_sections(text)} if text is not None else {}
    _body_cache[root] = bodies
    return bodies


def base_file_text(root: Path, rel: str) -> Optional[str]:
    """The file's text at origin/main, or None when git fails or the path is absent there. Memoized per (root, rel)."""
    key = (root, rel)
    if key not in _file_cache:
        result = _common.run(
            ["git", "show", f"origin/main:{rel}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
        )
        _file_cache[key] = result.stdout if result.returncode == 0 else None
    return _file_cache[key]
