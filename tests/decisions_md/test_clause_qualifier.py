"""A clause qualifier owns every number it enumerates (PLAN-decisions-md-clause-qualifier-grammar).

The shared title-relation grammar must never read a clause list such as "cl.1/5/6/7" as Decisions
5, 6 and 7 (the false positive that blocked PR #1393, T1.17 c4).
"""

from __future__ import annotations

import pytest

from scripts.decisions_md import _extract_title_relation_targets

_D199 = (
    "Telemetry event-journal model -- append-only lifecycle events, write-boundary-derived identity, "
    "derived-at-read state/friction/cost (amends Decisions 95, 96 clauses 1 and 3, 97 clauses 1-4)"
)
_D215 = (
    "The full local recommendations cache is retired; completeness reads are bounded named verbs "
    "that fail closed (amends Decision 88 clause 1(ii) and Decision 103)"
)
_D230 = (
    "Production telemetry gets its own catalog -- an owner role per catalog, one login per catalog binding, "
    "operator admin verbs to create it, and only the telemetry functions on its prefixes (amends Decision 216 "
    "clauses 1 and 3, Decision 213 clauses 1, 3 and 5, Decision 126 and Decision 81 clause 6)"
)

_CASES: list[tuple[str, str, list[int]]] = [
    ("repro_slash_list", "This amends Decision 213 cl.1/5/6/7 and more.", [213]),
    (
        "dec213_body_prose",
        "amends Decision 81 cl.1/5/6/7, 91 cl.1 and 126's admin tier",
        [81, 91],
    ),
    ("dec199_header", _D199, [95, 96, 97]),
    ("dec215_header", _D215, [88, 103]),
    ("dec230_header", _D230, [216, 213, 126, 81]),
    ("singular_subclause_then_target", "amends Decision 81 clause 3(2), 91", [81, 91]),
    ("singular_section_then_target", "amends Decision 81 section 2, 91", [81, 91]),
    ("singular_section_sign_then_target", "amends Decision 81 § 2, 91", [81, 91]),
    ("plain_plural_list", "supersedes Decisions 5, 6 and 7", [5, 6, 7]),
    ("open_prose_after_clause", "amends Decision 81 clause 6, 2027 planning cycle", [81]),
    ("plural_clause_range", "amends Decision 97 clauses 1-4", [97]),
    ("pinned_plural_range_then_bare", "amends Decision 81 clauses 1-2, 91", [81]),
    ("pinned_plural_and_then_bare", "amends Decision 81 clauses 1, 5 and 91", [81]),
]


class TestClauseEnumerationIsNotAVictim:
    @pytest.mark.parametrize(("title", "expected"), [c[1:] for c in _CASES], ids=[c[0] for c in _CASES])
    def test_targets(self, title: str, expected: list[int]) -> None:
        relation = "supersedes" if title.startswith("supersedes") else "amends"
        assert _extract_title_relation_targets(title, relation) == expected
