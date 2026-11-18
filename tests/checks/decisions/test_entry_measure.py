"""Mirror test for scripts/checks/decisions/entry_measure.py (Decision 273 clause 4)."""

from __future__ import annotations

import pytest

from scripts.checks.decisions import entry_measure as em

ENVELOPE = "```yaml\nnumber: 9\nkind:\n  value: direction\n```"
STANZA = "```yaml reversal-conditions\ndecision: 9\n```"
OPTIONS = "**Options considered:** a -- rejected: b.\n\n"
HEAD = "## Decision 9: T (Decided)\n\n"
ALL = {em.METADATA_ENVELOPE, em.REVERSAL_CONDITIONS, em.OPTIONS_CONSIDERED, em.DATED_ANNOTATIONS}


def _entry(annotations: str = "") -> str:
    tail = f"\n{annotations}" if annotations else ""
    return f"{HEAD}{ENVELOPE}\n\n**Status:** Decided\n\n{OPTIONS}**Related:** x\n\n{STANZA}\n{tail}\n---\n\n"


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


class TestSpans:
    def test_envelope_is_the_bare_fence_after_the_heading(self) -> None:
        entry = _entry()
        start, end = em.envelope_span(entry) or (0, 0)
        assert entry[start:end] == ENVELOPE

    def test_envelope_absent_without_a_bare_fence(self) -> None:
        assert em.envelope_span(f"{HEAD}text\n") is None
        assert em.envelope_span(f"{HEAD}{STANZA}\n") is None

    def test_stanza_is_the_reversal_conditions_fence(self) -> None:
        entry = _entry()
        start, end = em.stanza_span(entry) or (0, 0)
        assert entry[start:end] == STANZA
        assert em.stanza_span(f"{HEAD}{ENVELOPE}\n") is None

    def test_options_block_runs_to_the_next_bold_marker(self) -> None:
        block = em.options_block(_entry(), em.OPTIONS_MARKER)
        assert block is not None and block.startswith("**Options considered:** a") and "Related" not in block

    @pytest.mark.parametrize("after", ["```yaml\nx\n```", "## Decision 10: y"])
    def test_options_block_stops_at_a_fence_or_heading(self, after: str) -> None:
        block = em.options_block(f"{HEAD}**Options considered:** a.\n\n{after}\n", em.OPTIONS_MARKER)
        assert block == "**Options considered:** a.\n\n"

    def test_options_block_runs_to_the_end_without_a_following_line(self) -> None:
        assert em.options_block("**Options considered:** a", em.OPTIONS_MARKER) == "**Options considered:** a"
        assert em.options_span(f"{HEAD}no marker\n", em.OPTIONS_MARKER) is None
        assert em.options_block(f"{HEAD}no marker\n", em.OPTIONS_MARKER) is None


class TestAnnotations:
    def test_dated_undated_multiline_and_bracket_forms_all_count(self) -> None:
        annotations = (
            "> **Amended by Decision 5 (2026-01-02):** one\n\n"
            "> **Supersession note:** undated\n\n"
            "> **Note (2026-02-03):** first\n> second line\n\n"
            "[Amendment 2026-03-04: bracketed]\n"
        )
        entry = _entry(annotations)
        spans = em.dated_annotation_spans(entry)
        texts = [entry[a:b].strip() for a, b in spans]
        assert len(texts) == 4
        assert any("second line" in t for t in texts)
        assert any(t.startswith("[Amendment") for t in texts)

    def test_dated_only_drops_undated_paragraphs(self) -> None:
        entry = _entry("> **Supersession note:** undated\n\n> **Note (2026-02-03):** dated\n")
        dated = em.dated_annotation_spans(entry, dated_only=True)
        assert len(dated) == 1 and "dated" in entry[dated[0][0] : dated[0][1]]

    def test_a_blockquote_with_a_non_quoted_line_is_not_an_annotation(self) -> None:
        assert em.dated_annotation_spans(_entry("> **Note (2026-02-03):** x\nplain line\n")) == []

    def test_ordinary_paragraphs_are_not_annotations(self) -> None:
        assert em.dated_annotation_spans(_entry()) == []


class TestMeasuredBytes:
    def test_no_exclusion_is_the_whole_entry(self) -> None:
        entry = _entry()
        assert em.measured_bytes(entry, set()) == _bytes(entry)

    @pytest.mark.parametrize(
        ("name", "removed"),
        [
            (em.METADATA_ENVELOPE, ENVELOPE),
            (em.REVERSAL_CONDITIONS, STANZA),
            (em.OPTIONS_CONSIDERED, "**Options considered:** a -- rejected: b.\n\n"),
        ],
    )
    def test_each_exclusion_removes_exactly_its_span(self, name: str, removed: str) -> None:
        entry = _entry()
        assert em.measured_bytes(entry, {name}) == _bytes(entry) - _bytes(removed)

    def test_dated_annotations_are_removed_with_their_blank_line(self) -> None:
        plain = _entry()
        noted = _entry("> **Note:** grows nothing\n")
        assert em.measured_bytes(noted, {em.DATED_ANNOTATIONS}) == em.measured_bytes(plain, {em.DATED_ANNOTATIONS})
        assert em.measured_bytes(noted, set()) > em.measured_bytes(plain, set())

    def test_overlapping_spans_are_not_counted_twice(self) -> None:
        entry = _entry("> **Note (2026-01-01):** x\n")
        assert 0 < em.measured_bytes(entry, ALL) < _bytes(entry)

    def test_an_entry_with_no_spans_is_unchanged(self) -> None:
        assert em.measured_bytes("## Decision 1: t\n\nbody\n", ALL) == _bytes("## Decision 1: t\n\nbody\n")


class TestAuthored:
    def test_trailing_dated_run_is_dropped_and_separator_kept(self) -> None:
        entry = "## Decision 1: t\n\nbody\n\n> **Note (2026-01-01):** a\n\n[Amendment 2026-01-02: b]\n\n---\n\n"
        assert em.authored(entry) == "## Decision 1: t\n\nbody\n\n---\n\n"
        assert em.authored_bytes(entry) == _bytes(em.authored(entry))

    def test_an_entry_without_trailers_is_unchanged(self) -> None:
        assert em.authored("## Decision 1: t\n\nbody\n\n---\n\n") == "## Decision 1: t\n\nbody\n\n---\n\n"

    def test_an_unclosed_bracket_is_not_a_trailer(self) -> None:
        entry = "## Decision 1: t\n\nbody\n\n[Amendment 2026-01-02: open\n\n---\n"
        assert em.authored(entry) == entry


class TestEdges:
    def test_an_annotation_glued_to_the_closing_separator_is_still_found(self) -> None:
        entry = f"{HEAD}body\n\n> **Note (2026-01-01):** x\n---\n\n"
        assert len(em.dated_annotation_spans(entry)) == 1

    def test_an_annotation_inside_the_options_block_is_removed_once(self) -> None:
        entry = f"{HEAD}**Options considered:** a.\n\n> **Note (2026-01-01):** x\n\n**Related:** y\n\n---\n\n"
        both = em.measured_bytes(entry, {em.OPTIONS_CONSIDERED, em.DATED_ANNOTATIONS})
        assert both == em.measured_bytes(entry, {em.OPTIONS_CONSIDERED})
