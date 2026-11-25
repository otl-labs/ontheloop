"""Supersession-aware reads of one Decision entry, shared by tests and graduated record one-liners (rec-5628).

A test or record that pins another Decision's prose reads the entry through live_block: a live entry returns its block,
a compacted stub (Decision 278) satisfies the read once its pointer resolves and it names the amenders a real stub must
carry. Coupling: the naming test imports validate_supersession_annotations._is_annotated, the one definition of "names
Decision N"; promoting it to scripts.decisions_md is out of scope.
"""

from __future__ import annotations

import dataclasses

from scripts.checks.decisions.validate_supersession_annotations import _is_annotated
from scripts.decisions_md import extract_superseded_by, is_compacted_stub, iter_decision_sections


class EntryResolutionError(ValueError):
    """The entry is absent, duplicated, or its supersession pointer does not resolve."""


@dataclasses.dataclass(frozen=True)
class ResolvedEntry:
    number: int
    block: str
    compacted: bool
    successor: int | None
    hops: tuple[int, ...]


def _section(text: str, number: int) -> str:
    blocks = [block for match, block in iter_decision_sections(text) if int(match.group(1)) == number]
    if len(blocks) != 1:
        raise EntryResolutionError(f"DECISIONS.md must carry exactly one Decision {number}, found {len(blocks)}")
    return blocks[0]


def resolve_entry(text: str, number: int) -> ResolvedEntry:
    block = _section(text, number)
    if not is_compacted_stub(block):
        return ResolvedEntry(number, block, False, None, ())
    hops: list[int] = []
    current, current_block = number, block
    while is_compacted_stub(current_block):
        target = int(extract_superseded_by(current_block).split("-")[1])
        if target == number or target in hops:
            raise EntryResolutionError(f"Decision {number}: supersession cycle through Decision {target}")
        hops.append(target)
        try:
            current_block = _section(text, target)
        except EntryResolutionError as exc:
            raise EntryResolutionError(f"Decision {current} points at Decision {target}: {exc}") from exc
        current = target
    return ResolvedEntry(number, block, True, current, tuple(hops))


def live_block(text: str, number: int, *, naming: tuple[int, ...] = ()) -> str | None:
    """The entry's block when it is live; None when it is a compacted stub whose pointer resolves and which names
    every number in `naming`. Pass in `naming` only Decisions with a supersession edge to the entry: a stub need
    carry no others. A waiver excuses a live entry's annotation, never a stub, so the naming test is strict."""
    entry = resolve_entry(text, number)
    if not entry.compacted:
        return entry.block
    missing = [n for n in naming if not _is_annotated(n, entry.block)]
    if missing:
        raise EntryResolutionError(
            f"Decision {number} is a compacted stub that does not name Decision(s) {missing}: a real stub names every amender"
        )
    return None


__all__ = ["EntryResolutionError", "ResolvedEntry", "live_block", "resolve_entry"]
