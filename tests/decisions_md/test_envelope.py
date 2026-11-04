"""Tests for scripts/decisions_md.py's typed metadata envelope (PLAN-decision-entry-flow-
governance, Decision 167): extract_entry_envelope() well-formedness, parse_decisions_md's
envelope-first / bold-marker-fallback precedence, byte-reconstruction preservation under an
envelope, and the bare-`yaml` info-string boundary against Decision 133's monitored
```yaml reversal-conditions stanza.

Together with test_parse.py, this package gives scripts/decisions_md.py 100% per-file coverage
(it carries no config/coverage_baseline.yaml entry).
"""

from __future__ import annotations

from pathlib import Path

from scripts.decisions_md import (
    PLACEHOLDER_DATE,
    PLACEHOLDER_NUMBER,
    _iter_decision_sections,
    extract_entry_envelope,
    iter_decision_headings,
    iter_placeholder_headings,
    parse_decisions_md,
)


class TestExtractEntryEnvelopeWellFormedness:
    def test_no_fence_returns_none(self) -> None:
        block = "## Decision 1: No envelope (Decided)\n\n**Status:** Decided\n"
        assert extract_entry_envelope(block) is None

    def test_well_formed_fence_returns_dict(self) -> None:
        block = "## Decision 1: Has envelope (Decided)\n\n```yaml\nnumber: 1\nstatus: Decided\n```\n\n**Status:** Decided\n"
        envelope = extract_entry_envelope(block)
        assert envelope == {"number": 1, "status": "Decided"}

    def test_blank_line_between_heading_and_fence_is_tolerated(self) -> None:
        block = "## Decision 1: Spaced (Decided)\n\n\n```yaml\nnumber: 1\n```\n"
        assert extract_entry_envelope(block) == {"number": 1}

    def test_malformed_yaml_returns_none(self) -> None:
        block = "## Decision 1: Broken (Decided)\n\n```yaml\nnumber: [unclosed\n```\n"
        assert extract_entry_envelope(block) is None

    def test_non_dict_payload_returns_none(self) -> None:
        block = "## Decision 1: List payload (Decided)\n\n```yaml\n- one\n- two\n```\n"
        assert extract_entry_envelope(block) is None

    def test_fence_not_immediately_after_heading_returns_none(self) -> None:
        """A fenced yaml block appearing LATER in the body (not immediately after the heading)
        does not count -- this is the placement rule, not a generic fence scan."""
        block = "## Decision 1: Late fence (Decided)\n\n**Status:** Decided\n\nsome prose\n\n```yaml\nnumber: 1\n```\n"
        assert extract_entry_envelope(block) is None

    def test_fence_after_a_non_blank_body_line_returns_none(self) -> None:
        block = "## Decision 1: Prose first (Decided)\n\nProse before any fence.\n\n```yaml\nnumber: 1\n```\n"
        assert extract_entry_envelope(block) is None


class TestEnvelopeShapeValidation:
    """Code-review follow-up (PLAN-decision-entry-flow-governance): a known field carrying the
    wrong shape (a scalar where parse_decisions_md/the conformance check expect a list or int)
    must be rejected as malformed at THIS layer -- never reach a downstream consumer's own
    list()/int() coercion and crash it with a raw traceback."""

    def test_scalar_amends_is_rejected(self) -> None:
        block = "## Decision 1: Bad amends (Decided)\n\n```yaml\nnumber: 1\namends: 150\n```\n"
        assert extract_entry_envelope(block) is None

    def test_scalar_supersedes_is_rejected(self) -> None:
        block = "## Decision 1: Bad supersedes (Decided)\n\n```yaml\nnumber: 1\nsupersedes: 150\n```\n"
        assert extract_entry_envelope(block) is None

    def test_non_integer_list_item_in_amends_is_rejected(self) -> None:
        block = "## Decision 1: Non-int amends item (Decided)\n\n```yaml\nnumber: 1\namends: [150, not-a-number]\n```\n"
        assert extract_entry_envelope(block) is None

    def test_non_numeric_number_is_rejected(self) -> None:
        block = "## Decision 1: Bad number (Decided)\n\n```yaml\nnumber: abc\n```\n"
        assert extract_entry_envelope(block) is None

    def test_non_dict_significance_is_rejected(self) -> None:
        block = "## Decision 1: Bad significance (Decided)\n\n```yaml\nnumber: 1\nsignificance: not_a_mapping\n```\n"
        assert extract_entry_envelope(block) is None

    def test_well_formed_list_and_int_fields_are_accepted(self) -> None:
        block = "## Decision 1: Good shapes (Decided)\n\n```yaml\nnumber: 1\namends: [2, 3]\nsupersedes: [4]\n```\n"
        envelope = extract_entry_envelope(block)
        assert envelope == {"number": 1, "amends": [2, 3], "supersedes": [4]}

    def test_absent_optional_fields_do_not_trip_shape_validation(self) -> None:
        block = "## Decision 1: Number only (Decided)\n\n```yaml\nnumber: 1\n```\n"
        assert extract_entry_envelope(block) == {"number": 1}

    def test_malformed_amends_does_not_crash_parse_decisions_md_and_falls_back(self, tmp_path: Path) -> None:
        """The regression this closes: parse_decisions_md's list(envelope["amends"]) call must
        never see a non-list value -- a malformed envelope is treated as wholly absent, falling
        back to the legacy title-relation extraction unchanged."""
        path = tmp_path / "DECISIONS.md"
        path.write_text(
            "## Decision 5: Malformed amends (amends Decision 9) (Decided)\n\n```yaml\nnumber: 5\namends: 150\n```\n",
            encoding="utf-8",
        )
        rows = parse_decisions_md(paths=[path])
        assert rows[0]["amends"] == [9]

    def test_non_numeric_number_does_not_crash_parse_decisions_md(self, tmp_path: Path) -> None:
        path = tmp_path / "DECISIONS.md"
        path.write_text(
            "## Decision 5: Malformed number (Decided)\n\n```yaml\nnumber: abc\nstatus: Decided\n```\n",
            encoding="utf-8",
        )
        rows = parse_decisions_md(paths=[path])
        assert rows[0]["decision_id"] == 5
        assert rows[0]["significance"] == {}


class TestEnvelopeInfoStringBoundary:
    """The envelope fence is a BARE ```yaml info string, never colliding with Decision 133's
    monitored ```yaml reversal-conditions stanza (scripts/preflight/decision_conditions.py
    matches on that exact info string)."""

    def test_decorated_info_string_is_not_treated_as_an_envelope(self) -> None:
        block = (
            "## Decision 1: Reversal stanza, not an envelope (Decided)\n\n"
            "```yaml reversal-conditions\ncondition: some condition\n```\n"
        )
        assert extract_entry_envelope(block) is None

    def test_bare_yaml_fence_is_treated_as_an_envelope(self) -> None:
        block = "## Decision 1: Bare fence (Decided)\n\n```yaml\nnumber: 1\n```\n"
        assert extract_entry_envelope(block) is not None


class TestParseDecisionsMdEnvelopePrecedence:
    """parse_decisions_md prefers envelope values for status / decided_date / amends /
    title_supersedes (the row key sourced from the envelope's `supersedes` field) over the
    legacy bold-marker/title grammar, whenever the envelope declares them."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "DECISIONS.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_envelope_status_overrides_bold_marker(self, tmp_path: Path) -> None:
        content = (
            "## Decision 5: Envelope wins (Decided)\n\n```yaml\nnumber: 5\nstatus: Superseded\n```\n\n**Status:** Decided\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        assert rows[0]["status"] == "Superseded"

    def test_envelope_decided_date_overrides_bold_marker_and_normalizes_yaml_date_type(self, tmp_path: Path) -> None:
        """A bare (unquoted) YAML date scalar parses to a datetime.date -- must normalize to the
        same ISO string a quoted value would produce."""
        content = (
            "## Decision 5: Envelope date wins (Decided)\n\n"
            "```yaml\nnumber: 5\ndecided_date: 2026-01-02\n```\n\n"
            "**Date:** 2099-12-31\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        assert rows[0]["decided_date"] == "2026-01-02"

    def test_envelope_amends_overrides_title_extraction(self, tmp_path: Path) -> None:
        content = (
            "## Decision 5: Envelope amends wins (amends Decision 999) (Decided)\n\n```yaml\nnumber: 5\namends: [1, 2]\n```\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        assert rows[0]["amends"] == [1, 2]

    def test_envelope_supersedes_overrides_title_borne_supersedes(self, tmp_path: Path) -> None:
        content = (
            "## Decision 5: Envelope supersedes wins (Supersedes Decision 999) (Decided)\n\n"
            "```yaml\nnumber: 5\nsupersedes: [3, 4]\n```\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        assert rows[0]["title_supersedes"] == [3, 4]

    def test_envelope_significance_surfaces_on_the_row(self, tmp_path: Path) -> None:
        content = (
            "## Decision 5: Significance row key (Decided)\n\n"
            "```yaml\nnumber: 5\nsignificance:\n  value: numbered_decision\n  justification: reason.\n```\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        assert rows[0]["significance"] == {"value": "numbered_decision", "justification": "reason."}

    def test_no_envelope_significance_defaults_to_empty_dict(self, tmp_path: Path) -> None:
        content = "## Decision 5: No envelope at all (Decided)\n\n**Status:** Decided\n"
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        assert rows[0]["significance"] == {}

    def test_envelope_absent_fields_fall_back_to_legacy_grammar_unchanged(self, tmp_path: Path) -> None:
        """An envelope present but declaring only `number` (no status/decided_date/amends/
        supersedes) must not suppress the legacy fallback for the fields it omits -- proves the
        precedence is per-field, not all-or-nothing."""
        content = (
            "## Decision 5: Partial envelope (amends Decision 9) (Decided)\n\n"
            "```yaml\nnumber: 5\n```\n\n"
            "**Status:** Decided\n\n**Date:** 2026-03-01\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        row = rows[0]
        assert row["status"] == "Decided"
        assert row["decided_date"] == "2026-03-01"
        assert row["amends"] == [9]

    def test_grandfathered_entry_with_no_envelope_parses_identically_to_legacy_grammar(self, tmp_path: Path) -> None:
        """No envelope at all -- every field must resolve via the pre-existing bold-marker/title
        grammar, unchanged (the forward-only fallback guarantee the historical band relies on)."""
        content = (
            "## Decision 5: Grandfathered entry (amends Decision 9) (Decided)\n\n**Status:** Decided\n\n**Date:** 2026-03-01\n"
        )
        rows = parse_decisions_md(paths=[self._write(tmp_path, content)])
        row = rows[0]
        assert row["status"] == "Decided"
        assert row["decided_date"] == "2026-03-01"
        assert row["amends"] == [9]
        assert row["significance"] == {}


class TestByteReconstructionPreservedUnderEnvelope:
    """The envelope fence is ordinary body text as far as sectioning is concerned -- an
    envelope-bearing entry must still byte-reconstruct exactly, same invariant as
    test_parse.py's TestByteReconstruction, exercised here specifically WITH an envelope."""

    def test_envelope_bearing_entry_reconstructs_exactly(self) -> None:
        fixture = (
            "# Preamble\n\n"
            "## Decision 1: Enveloped (Decided)\n\n"
            "```yaml\nnumber: 1\nstatus: Decided\n```\n\n"
            "**Status:** Decided\n\n---\n\n"
            "## Decision 2: Plain (Decided)\n\nBody two.\n"
        )
        sections = _iter_decision_sections(fixture)
        assert len(sections) == 2
        preamble = fixture[: sections[0][0].start()]
        reconstructed = preamble + "".join(rb for _, rb in sections)
        assert reconstructed == fixture

    def test_envelope_bearing_entry_content_hash_reflects_envelope_bytes(self, tmp_path: Path) -> None:
        """The envelope text is part of raw_block (and therefore content_hash) -- changing only
        the envelope changes the hash, same as any other body edit."""
        base = "## Decision 1: X (Decided)\n\n```yaml\nnumber: 1\nstatus: Decided\n```\n\n**Status:** Decided\n"
        changed = "## Decision 1: X (Decided)\n\n```yaml\nnumber: 1\nstatus: Superseded\n```\n\n**Status:** Decided\n"
        base_path = tmp_path / "base" / "DECISIONS.md"
        changed_path = tmp_path / "changed" / "DECISIONS.md"
        base_path.parent.mkdir()
        changed_path.parent.mkdir()
        base_path.write_text(base, encoding="utf-8")
        changed_path.write_text(changed, encoding="utf-8")
        base_rows = parse_decisions_md(paths=[base_path])
        changed_rows = parse_decisions_md(paths=[changed_path])
        assert base_rows[0]["content_hash"] != changed_rows[0]["content_hash"]


_DRAFT = (
    "## Decision {{N}}: Draft title\n\n"
    '```yaml\nnumber: {{N}}\ndecided_date: "{{DATE}}"\nsignificance:\n  value: numbered_decision\n  justification: r.\n'
    "kind:\n  value: direction\n  justification: j.\nserves: [NS.1]\n```\n"
)


class TestServesKindShapes:
    def test_well_formed_serves_and_kind_are_accepted(self) -> None:
        block = "## Decision 1: X\n\n```yaml\nnumber: 1\nserves: [NS.1]\nkind: {value: direction, justification: j}\n```\n"
        assert extract_entry_envelope(block) == {
            "number": 1,
            "serves": ["NS.1"],
            "kind": {"value": "direction", "justification": "j"},
        }

    def test_scalar_serves_is_rejected(self) -> None:
        assert extract_entry_envelope("## Decision 1: X\n\n```yaml\nnumber: 1\nserves: NS.1\n```\n") is None

    def test_non_string_serves_item_is_rejected(self) -> None:
        assert extract_entry_envelope("## Decision 1: X\n\n```yaml\nnumber: 1\nserves: [1]\n```\n") is None

    def test_string_kind_is_rejected(self) -> None:
        assert extract_entry_envelope("## Decision 1: X\n\n```yaml\nnumber: 1\nkind: direction\n```\n") is None

    def test_row_keys_carry_kind_mapping_and_serves_list(self, tmp_path: Path) -> None:
        path = tmp_path / "DECISIONS.md"
        path.write_text(
            "## Decision 1: X\n\n```yaml\nnumber: 1\nserves: [NS.2]\nkind: {value: mechanism, justification: j}\n```\n\n"
            "## Decision 2: Y\n\n**Status:** Decided\n",
            encoding="utf-8",
        )
        rows = parse_decisions_md(paths=[path])
        assert rows[0]["kind"] == {"value": "mechanism", "justification": "j"}
        assert rows[0]["serves"] == ["NS.2"]
        assert rows[1]["kind"] == {} and rows[1]["serves"] == []


class TestPlaceholderGrammar:
    def test_iterator_matches_placeholder_and_digits_only(self) -> None:
        text = "## Decision {{N}}: A\n\n### Decision 7: B\n\n## Decision {{X}}: C\n\n## Decision 9:\nbody\n"
        assert [m.group(1) for m in iter_placeholder_headings(text)] == ["{{N}}", "7"]

    def test_digits_iterator_never_sees_placeholder_headings(self) -> None:
        assert iter_decision_headings("## Decision {{N}}: A\n") == []

    def test_placeholder_draft_parses_only_with_the_flag(self) -> None:
        assert extract_entry_envelope(_DRAFT) is None
        env = extract_entry_envelope(_DRAFT, placeholder=True)
        assert env is not None
        assert env["number"] == PLACEHOLDER_NUMBER and env["decided_date"] == PLACEHOLDER_DATE
        assert env["kind"]["value"] == "direction"

    def test_placeholder_flag_still_rejects_a_wrong_shape(self) -> None:
        assert extract_entry_envelope(_DRAFT.replace("serves: [NS.1]", "serves: NS.1"), placeholder=True) is None

    def test_placeholder_number_is_rejected_without_the_flag_even_with_a_digit_heading(self) -> None:
        assert extract_entry_envelope("## Decision 3: X\n\n```yaml\nnumber: {{N}}\n```\n") is None

    def test_approved_text_fills_both_placeholders(self) -> None:
        import re

        from scripts.roadmap import approved_text

        assert re.escape(PLACEHOLDER_NUMBER) in approved_text._PLACEHOLDERS
        assert re.escape(PLACEHOLDER_DATE) in approved_text._PLACEHOLDERS
