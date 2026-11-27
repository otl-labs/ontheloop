"""Mirror of scripts/checks/_decision_chain.py: the supersession walk on small corpora and on the real one."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import _decision_chain as chain
from scripts.checks.decisions import consolidation
from scripts.decisions_md import extract_superseded_by


def _stub(n: int, to: int, supersedes: list[int] | None = None) -> str:
    envelope = f"```yaml\nsupersedes: {supersedes}\n```\n\n" if supersedes else ""
    return f"## Decision {n}: s\n\n{envelope}**Status:** Superseded\n\n**Superseded by: Decision {to}**\n"


def _live(n: int, supersedes: object = None, title: str = "t") -> str:
    envelope = f"```yaml\nnumber: {n}\nsupersedes: {supersedes}\n```\n\n" if supersedes is not None else ""
    return f"## Decision {n}: {title}\n\n{envelope}**Status:** Decided\n"


CASES = {
    "live": ({1: _live(1)}, 1, None, (1, [1])),
    "absent": ({}, 7, None, (None, [7])),
    "one declared hop": ({1: _stub(1, 2), 2: _live(2, [1])}, 1, None, (2, [1, 2])),
    "envelope then title hop": (
        {1: _stub(1, 2), 2: _stub(2, 3, [1]), 3: _live(3, title="x (supersedes Decision 2)")},
        1,
        None,
        (3, [1, 2, 3]),
    ),
    "undeclared hop": ({1: _stub(1, 2), 2: _live(2)}, 1, None, (None, [1])),
    "non-list supersedes": ({1: _stub(1, 2), 2: _live(2, "1")}, 1, None, (None, [1])),
    "next header missing": ({1: _stub(1, 9)}, 1, None, (None, [1])),
    "cycle": ({1: _stub(1, 2, [2]), 2: _stub(2, 1, [1])}, 1, None, (None, [1, 2, 1])),
    "row superseded, no marker": ({1: _live(1)}, 1, {1: {"currency": "superseded_pointer"}}, (None, [1])),
    "row current beats marker": ({1: _stub(1, 2), 2: _live(2, [1])}, 1, {1: {"currency": "current"}}, (1, [1])),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_resolve_live(name: str) -> None:
    entries, number, rows, expected = CASES[name]
    assert chain.resolve_live(entries, number, rows) == expected


def test_declared_supersedes_reads_envelope_and_title() -> None:
    block = "## Decision 5: x (supersedes Decision 3)\n\n```yaml\nsupersedes: [4]\n```\n"
    assert chain.declared_supersedes(block) == {3, 4}
    assert chain.declared_supersedes("## Decision 6: plain\n") == set()


def test_every_declared_chain_in_the_real_corpus_ends_at_a_live_entry() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs/DECISIONS.md").exists()
    entries = consolidation._entries(root)
    rows = {r["number"]: r for r in consolidation.load_rows(root) if isinstance(r.get("number"), int)}
    declared = [
        n
        for n, block in entries.items()
        if (nxt := extract_superseded_by(block).removeprefix("dec-")).isdigit()
        and n in chain.declared_supersedes(entries.get(int(nxt), ""))
    ]
    assert declared
    assert [n for n in declared if chain.resolve_live(entries, n, rows)[0] is None] == []
