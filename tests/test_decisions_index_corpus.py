"""Corpus-data checks moved from tests/test_decisions_index.py (PLAN-decision-index-slim-rows): typed edges and
the on-demand triage excerpts, checked against the real corpus. Excerpts come from build_excerpts, joined on number."""

from __future__ import annotations

from scripts.decisions_index import build_excerpts, build_index
from scripts.decisions_md import parse_decisions_md


def _excerpt_rows() -> list[dict]:
    return build_excerpts(None)


class TestTypedEdgeSpotChecks:
    """VP step 3: edges spanning both derivation paths (title extraction, superseded_by
    inverse map, title-borne supersedes) and both files (live + archive)."""

    def test_dec150_amends_105_title_extraction(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert 105 in d[150]["amends"]

    def test_dec143_amends_81_title_extraction_with_clause_suffix(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert 81 in d[143]["amends"]

    def test_dec144_amends_both_98_and_92_multi_target(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[144]["amends"] == [92, 98]

    def test_dec90_supersedes_dec42_live_to_archive_inverse(self) -> None:
        """42 lives in DECISIONS_ARCHIVE.md; its superseded_by inverts onto live dec-90."""
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert 42 in d[90]["supersedes"]

    def test_dec69_superseded_by_78_and_inverse_symmetry(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[69]["superseded_by"] == 78
        assert 69 in d[78]["supersedes"]

    def test_dec117_supersedes_44_title_and_inverse_agree(self) -> None:
        """Both derivation paths agree here: 117's title says 'Supersedes Decision 44' AND
        44's body carries '**Superseded by: Decision 117**' -- the union produces one edge,
        not a duplicate, and the REVERSE edge (44 supersedes 117) must NOT appear."""
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[117]["supersedes"] == [44]
        assert d[44]["supersedes"] == []
        assert d[44]["superseded_by"] == 117

    def test_dec52_title_borne_supersedes_multi_target_plural_form(self) -> None:
        """Decision 52's title 'Supersedes Decisions 37, 40, 49' -- the plural/comma
        title-borne form, direction n->target (52 supersedes each, not the reverse)."""
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[52]["supersedes"] == [37, 40, 49]
        assert 52 not in d[37]["supersedes"]


class TestTriageExcerptIntegration:
    """The generator's triage_excerpt fields, checked against the real corpus via independent
    re-derivation (never a hardcoded entry count -- Decision 55 test-count-coupling)."""

    def test_every_entry_carries_the_three_fields(self) -> None:
        for entry in _excerpt_rows():
            assert "triage_excerpt" in entry
            assert "triage_excerpt_source" in entry
            assert "triage_excerpt_truncated" in entry
            assert isinstance(entry["triage_excerpt"], str)
            assert isinstance(entry["triage_excerpt_source"], str)
            assert isinstance(entry["triage_excerpt_truncated"], bool)

    def test_no_excerpt_exceeds_320_chars(self) -> None:
        for entry in _excerpt_rows():
            assert len(entry["triage_excerpt"]) <= 320

    def test_non_empty_excerpt_always_names_a_source(self) -> None:
        for entry in _excerpt_rows():
            if entry["triage_excerpt"]:
                assert entry["triage_excerpt_source"] in {"Intent", "Problem", "Context", "Decision"}
            else:
                assert entry["triage_excerpt_source"] == ""

    def test_empty_excerpt_set_matches_independent_rederivation(self) -> None:
        """Re-derive the residual (no-marker) set straight from parse_decisions_md rows,
        independent of build_index()'s own excerpt logic, and cross-check equality -- not a
        hardcoded count. Intersected with live_numbers since rederived_empty is drawn from BOTH
        files, but the indexed side is now live-only (rec-3012 skeletonized archive rows)."""
        indexed_empty = {x["number"] for x in _excerpt_rows() if not x["triage_excerpt"]}
        rows = parse_decisions_md()
        live_numbers = {x["number"] for x in _excerpt_rows()}
        rederived_empty = {
            row["decision_id"]
            for row in rows
            if not (row.get("intent") or "").strip()
            and not (row.get("problem") or "").strip()
            and not (row.get("context") or "").strip()
            and not (row.get("decision_text") or "").strip()
        } & live_numbers
        assert indexed_empty == rederived_empty

    def test_excerpt_coverage_is_high(self) -> None:
        """At most 4 LIVE entries lack any quotable marker (measured 121/125 at authoring time,
        rec-3012 having narrowed both sides from the pre-skeletonization 135/139) -- expressed as
        a coverage floor, not an exact count, so new decisions don't break this."""
        total = len(_excerpt_rows())
        with_excerpt = sum(1 for x in _excerpt_rows() if x["triage_excerpt"])
        assert with_excerpt >= total - 4

    def test_truncated_flag_matches_source_text_length(self) -> None:
        """Cross-check truncated against an independent re-derivation of the winning fallback
        field's raw length, never trusting the generator's own flag in isolation."""
        rows_by_id = {row["decision_id"]: row for row in parse_decisions_md()}
        for entry in _excerpt_rows():
            if not entry["triage_excerpt_source"]:
                continue
            field_by_source = {
                "Intent": "intent",
                "Problem": "problem",
                "Context": "context",
                "Decision": "decision_text",
            }
            row_key = field_by_source[entry["triage_excerpt_source"]]
            raw = (rows_by_id[entry["number"]].get(row_key) or "").strip()
            assert entry["triage_excerpt_truncated"] == (len(raw) > 320)
            if entry["triage_excerpt_truncated"]:
                assert entry["triage_excerpt"] == raw[:320]
            else:
                assert entry["triage_excerpt"] == raw
