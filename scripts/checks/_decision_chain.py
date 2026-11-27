"""Supersession chain walk over Decision bodies, shared by the consolidation checks and the raise-marker guard.

A compacted entry carries `**Superseded by: Decision N**` (Decision 149), and chain consolidations supersede
successors in turn (Decision 278), so a chain may be longer than one hop. Top-level placement per Decision 104: the
consolidation manifest check and the inventory (decisions domain) walk it today; rec-5719's raise-marker guard
(scripts/checks/_marker_guard.py) is the planned third consumer. entry_resolution.resolve_entry is a separate walk
(prose-pin fixture reads, no declaration test) and stays separate.
"""

from __future__ import annotations

from collections.abc import Mapping

from scripts.decisions_md import extract_entry_envelope, extract_superseded_by, extract_title_borne_supersedes


def superseded(rows: Mapping[int, dict], number: int, block: str) -> bool:
    """The index row's currency when the entry has a row, else whether its body carries a Superseded by marker."""
    row = rows.get(number)
    return str(row.get("currency", "")).startswith("superseded") if row else bool(extract_superseded_by(block))


def declared_supersedes(block: str) -> set[int]:
    """The numbers an entry declares it supersedes, in its own envelope or its heading title."""
    listed = (extract_entry_envelope(block) or {}).get("supersedes")
    title = extract_title_borne_supersedes(block.split("\n", 1)[0])
    return set(title) | {n for n in (listed if isinstance(listed, list) else []) if isinstance(n, int)}


def resolve_live(
    entries: Mapping[int, str], number: int, rows: Mapping[int, dict] | None = None
) -> tuple[int | None, list[int]]:
    """(first entry on the Superseded by chain that is not superseded, or None; the numbers walked, in order).

    A hop needs a header for the next number and that entry's declaration of the one before it. A repeat, a missing
    header, a superseded entry with no marker or an undeclared hop ends the walk with None.
    """
    path = [number]
    while (block := entries.get(number)) is not None and number not in path[:-1]:
        if not superseded(rows or {}, number, block):
            return number, path
        nxt = extract_superseded_by(block).removeprefix("dec-")
        if not nxt.isdigit() or int(nxt) not in entries:
            return None, path
        if number not in declared_supersedes(entries[int(nxt)]):
            return None, path
        number = int(nxt)
        path.append(number)
    return None, path
