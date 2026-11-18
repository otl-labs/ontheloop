"""Curated retrieval probes for the bounded decision-scout protocol
(PLAN-decision-scout-bounded-retrieval, VP step 7).

A mechanical proxy: for each (approach keyword-set -> expected decision number) pair, assert the
COMMITTED docs/decisions-index.json projection carries discriminating signal for the expected
entry -- i.e. the keyword-set's match score against that entry's title+triage_excerpt is both
non-zero and at least as high as any other entry's score, so a triage pass over the index alone
would surface the right decision. This is a proxy, not the real measurement: VP step 9's live
scout dispatch (comparing whole-file vs bounded CITE/SPIRIT sets across the same probe briefs) is
what actually proves recall did not regress.

Scored against the LIVE projection only (migration step 5 of
audits/contract-first-governance-33c8667.yaml): rec-3012 skeletonized live:false rows, dropping
triage_excerpt, and decision-scout's own Phase 1 triage already filters to live:true before any
downstream read -- this harness's population matches the scout's, not the raw committed file.

The `pg_dump_backup` probe is the one required to NOT share vocabulary with its target: its
keywords appear nowhere in Decision 100's title, only in its triage_excerpt (the Problem-marker
fallback) -- proving the excerpt field, not the title alone, carries the discriminating signal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INDEX_PATH = _REPO_ROOT / "docs" / "decisions-index.json"

# _PROBES rows carry probe_name, keywords, expected_decision_number, shares_title_vocabulary
_PROBES: tuple[tuple[str, tuple[str, ...], int, bool], ...] = (
    # No title vocabulary overlap: Decision 100's title never says "pg_dump"/"pg_restore" --
    # only its Problem-marker excerpt does. Discriminates via triage_excerpt alone.
    ("pg_dump_backup", ("pg_dump", "pg_restore", "scratch-DB"), 100, False),
    # Title overlap (control): these keywords appear verbatim in each target's own title.
    ("roadmap_ceiling", ("10,000 lines", "ROADMAP-PLATFORM.yaml"), 114, True),
    ("rca_executor", ("autonomous executor", "RCA-First"), 55, True),
    ("neon_egress", ("egress", "Neon catalog"), 88, True),
    ("spirit_axis", ("SPIRIT axis", "quotability-gated"), 152, True),
)


def _load_index() -> list[dict[str, Any]]:
    """Live rows only -- migration step 5 skeletonized live:false rows out of triage_excerpt,
    matching decision-scout's own Phase 1 filter (it never triages an archived entry)."""
    entries = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))["decisions"]
    return [e for e in entries if e["live"]]


def _score(entry: dict[str, Any], keywords: tuple[str, ...], *, title_only: bool = False) -> int:
    haystack = entry["title"] if title_only else f"{entry['title']} {entry.get('triage_excerpt', '')}"
    haystack = haystack.lower()
    return sum(1 for kw in keywords if kw.lower() in haystack)


class TestCuratedRetrievalProbes:
    def test_every_probe_targets_a_real_index_entry(self) -> None:
        entries = _load_index()
        numbers = {e["number"] for e in entries}
        for name, _keywords, expected, _title_overlap in _PROBES:
            assert expected in numbers, f"probe {name!r} targets decision {expected}, not in the index"

    def test_probe_names_are_unique(self) -> None:
        names = [p[0] for p in _PROBES]
        assert len(names) == len(set(names))

    def test_each_probe_discriminates_to_its_expected_entry(self) -> None:
        """The expected entry's keyword-match score must be non-zero AND at least as high as
        every other entry's score -- i.e. a triage pass over the index would surface it as a
        top candidate, not buried among false positives."""
        entries = _load_index()
        for name, keywords, expected, _title_overlap in _PROBES:
            scores = {e["number"]: _score(e, keywords) for e in entries}
            expected_score = scores[expected]
            max_score = max(scores.values())
            assert expected_score >= 1, f"probe {name!r}: no signal at all for decision {expected}"
            assert expected_score == max_score, (
                f"probe {name!r}: decision {expected} scored {expected_score}, "
                f"but max score in the index is {max_score} (not discriminating)"
            )

    def test_no_title_vocabulary_probe_relies_on_the_excerpt(self) -> None:
        """For the probe explicitly marked shares_title_vocabulary=False, confirm the keywords
        truly have zero signal in the title alone -- the excerpt is doing the real work."""
        entries = _load_index()
        by_number = {e["number"]: e for e in entries}
        no_overlap_probes = [p for p in _PROBES if not p[3]]
        assert no_overlap_probes, "expected at least one probe with shares_title_vocabulary=False"
        for name, keywords, expected, _title_overlap in no_overlap_probes:
            entry = by_number[expected]
            title_only_score = _score(entry, keywords, title_only=True)
            full_score = _score(entry, keywords)
            assert title_only_score == 0, f"probe {name!r} was expected to have zero title overlap, got {title_only_score}"
            assert full_score >= 1, f"probe {name!r}: excerpt should supply the signal title lacks"

    def test_title_vocabulary_probes_actually_overlap_the_title(self) -> None:
        """Sanity check on the control probes marked shares_title_vocabulary=True."""
        entries = _load_index()
        by_number = {e["number"]: e for e in entries}
        overlap_probes = [p for p in _PROBES if p[3]]
        assert overlap_probes
        for name, keywords, expected, _title_overlap in overlap_probes:
            entry = by_number[expected]
            title_only_score = _score(entry, keywords, title_only=True)
            assert title_only_score >= 1, f"probe {name!r} was expected to overlap the title, got 0"
