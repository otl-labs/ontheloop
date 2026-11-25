"""Mirror of scripts/checks/decisions/entry_resolution.py on synthetic logs (Decision numbers 9001 and up)."""

from __future__ import annotations

import pytest

from scripts.checks.decisions.entry_resolution import EntryResolutionError, live_block, resolve_entry


def _live(number: int, body: str = "Body.") -> str:
    head = f"## Decision {number}: Entry {number} (Decided)\n\n**Status:** Decided\n**Date:** 2026-01-01\n\n"
    return head + f"**Decision:** {body}\n\n---\n\n"


def _stub(number: int, successor: int, names: str = "") -> str:
    return (
        f"## Decision {number}: Entry {number} (Decided)\n\n**Status:** Superseded\n**Date:** 2026-01-01\n\n"
        f"**Decision:** Superseded by Decision {successor}, which restates its rulings{names}.\n\n"
        f"**Superseded by: Decision {successor}**\n\n---\n\n"
    )


def test_live_entry_returns_its_block() -> None:
    text = "# Decisions\n\n" + _live(9001, "Pinned prose.") + _live(9002)
    block = live_block(text, 9001, naming=(9002,))
    assert block is not None and "Pinned prose." in block and "Decision 9002:" not in block
    entry = resolve_entry(text, 9001)
    assert not entry.compacted and entry.successor is None and entry.hops == ()


def test_stub_naming_amender_resolves_to_none() -> None:
    text = _live(9010) + _stub(9001, 9010, " and names its amenders: Decision 9002")
    assert live_block(text, 9001, naming=(9002,)) is None
    entry = resolve_entry(text, 9001)
    assert entry.compacted and entry.successor == 9010 and entry.hops == (9010,)


def test_stub_not_naming_amender_raises() -> None:
    text = _live(9010) + _stub(9001, 9010)
    with pytest.raises(EntryResolutionError, match="does not name"):
        live_block(text, 9001, naming=(9002,))


def test_edgeless_noter_needs_only_a_resolving_pointer() -> None:
    text = _live(9010) + _stub(9001, 9010)
    assert live_block(text, 9001) is None
    assert live_block(text, 9001, naming=()) is None


def test_naming_does_not_match_a_longer_number() -> None:
    text = _live(9010) + _stub(9001, 9010, " and names its amenders: Decision 90021")
    with pytest.raises(EntryResolutionError):
        live_block(text, 9001, naming=(9002,))


def test_dangling_pointer_raises() -> None:
    with pytest.raises(EntryResolutionError, match="Decision 9001 points at Decision 9099"):
        live_block(_stub(9001, 9099), 9001)


def test_two_hop_chain_resolves_to_the_first_live_entry() -> None:
    text = _live(9030) + _stub(9020, 9030) + _stub(9001, 9020)
    entry = resolve_entry(text, 9001)
    assert entry.successor == 9030 and entry.hops == (9020, 9030)


def test_cycle_raises() -> None:
    with pytest.raises(EntryResolutionError, match="cycle"):
        resolve_entry(_stub(9001, 9002) + _stub(9002, 9001), 9001)


def test_missing_or_duplicated_number_raises() -> None:
    with pytest.raises(EntryResolutionError, match="found 0"):
        live_block(_live(9002), 9001)
    with pytest.raises(EntryResolutionError, match="found 2"):
        live_block(_live(9001) + _live(9001), 9001)
