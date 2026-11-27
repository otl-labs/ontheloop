"""Tests for scripts/decisions_md.py (PLAN-daf-etl-parity-fidelity, Decision 134 cl.4 / DAF-01).

Covers the DAF-01 parity-fidelity acceptance assertions: decorated-Decision-marker
tolerance, reversal_conditions extraction, the ISO-only decided_date fallback guard,
raw_block/content_hash presence, plural-cite parsing + dedupe, per-file byte-reconstruction
coverage (with a synthetic sectioner-narrowing mutation proving the assertion is live, not
vacuous), archive coverage, and dual schema-model field sync (DecisionPayload / jsonl_store.
Decision both carry the four new fields as plain, non-Dq-Annotated optional strings).

Moved verbatim from the retired top-level decisions_md test module (PLAN-decision-entry-flow-
governance, split into this package's test_parse.py / test_envelope.py per the
scripts/test_coverage_checker.py concern-split remap -- no assertion here is weakened or dropped
in the move).
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from scripts.decisions_md import (
    _DECISIONS_MD_PATHS,
    _extract_decided_date,
    _extract_multiline_section,
    _extract_related_decisions,
    _extract_section,
    _extract_superseded_by,
    _iter_decision_sections,
    decision_header_numbers,
    extract_amends_edges,
    extract_superseded_by,
    extract_title_borne_supersedes,
    iter_decision_headings,
    iter_decision_sections,
    parse_decisions_md,
    read_jsonl,
)


@pytest.fixture(scope="module")
def parsed_rows() -> dict[int, dict]:
    return {r["decision_id"]: r for r in parse_decisions_md()}


class TestParityAnchors:
    """The DAF-01 acceptance assertions named in the plan and the audit."""

    def test_parity_anchors_dec084_decision_text_non_empty(self, parsed_rows: dict[int, dict]) -> None:
        """Decorated marker '**Decision (four invariants):**' must not yield an empty body."""
        assert parsed_rows[84]["decision_text"]

    def test_parity_anchors_dec114_reversal_conditions_present(self, parsed_rows: dict[int, dict]) -> None:
        assert parsed_rows[114]["reversal_conditions"]

    def test_parity_anchors_dec067_decided_date_empty_or_iso(self, parsed_rows: dict[int, dict]) -> None:
        """The Status-suffix fallback must not harvest a non-date clause as a date."""
        decided_date = parsed_rows[67]["decided_date"]
        assert decided_date == "" or decided_date[:4].isdigit()

    def test_parity_anchors_raw_block_and_content_hash_present_for_every_entry(self, parsed_rows: dict[int, dict]) -> None:
        assert parsed_rows, "expected at least one parsed decision entry"
        for decision_id, row in parsed_rows.items():
            assert row["raw_block"], f"dec-{decision_id:03d} has an empty raw_block"
            content_hash = row["content_hash"]
            assert len(content_hash) == 64, f"dec-{decision_id:03d} content_hash is not 64 chars"
            assert re.fullmatch(r"[0-9a-f]{64}", content_hash), f"dec-{decision_id:03d} content_hash is not hex"


class TestPluralCiteParsing:
    """DAF-01: 'Decisions 69/78' plural cites were previously invisible to the parser."""

    def test_plural_cite_slash_form_parsed_and_deduped(self) -> None:
        text = (
            "**Related:** Decision 84 (...), Decisions 69/78 (Single-Portal Invariant), Decision 70 (...), Decision 70 (dup)."
        )
        assert _extract_related_decisions(text) == [84, 69, 78, 70]

    def test_plural_cite_comma_form_parsed(self) -> None:
        text = "**Related:** Decisions 24, 73 (two-tier CI)."
        assert _extract_related_decisions(text) == [24, 73]

    def test_plural_cite_dec087_related_includes_both_numbers_deduped(self, parsed_rows: dict[int, dict]) -> None:
        """Decision 87's Related line cites 'Decisions 69/78' -- both must parse, deduped."""
        related = parsed_rows[87]["related_decisions"]
        assert 69 in related
        assert 78 in related
        assert related.count(70) == 1


class TestArchiveCoverage:
    """Confirms docs/DECISIONS_ARCHIVE.md is covered identically to the live file."""

    def test_archive_only_entry_dec036_present_with_raw_block_and_hash(self, parsed_rows: dict[int, dict]) -> None:
        """dec-36 exists only in DECISIONS_ARCHIVE.md -- proves archive coverage is wired."""
        assert 36 in parsed_rows
        assert parsed_rows[36]["raw_block"]
        assert len(parsed_rows[36]["content_hash"]) == 64

    def test_archive_and_live_paths_both_configured(self) -> None:
        names = {p.name for p in _DECISIONS_MD_PATHS}
        assert names == {"DECISIONS.md", "DECISIONS_ARCHIVE.md"}


class TestHeaderHelperParity:
    """DAF-03 (PLAN-daf-authoring-grammar): iter_decision_headings() / decision_header_numbers()
    must reproduce the exact population the pre-consolidation regexes did (parity, growth-safe:
    derived from the live files, never a hardcoded count -- Decision 55 / test-count-coupling),
    and the DECISIONS_ARCHIVE.md h3->h2 promote (Decisions 52/53/54) must land in the both-files
    number set.
    """

    # The R1 ratification guard's pre-consolidation private regex (both files, h2-only).
    _R1_PRE_CONSOLIDATION_RE = re.compile(r"^## Decision (\d+):", re.MULTILINE)
    # The preflight open-decisions counter's pre-consolidation private regex (live file only).
    _PREFLIGHT_PRE_CONSOLIDATION_RE = re.compile(r"^## Decision \d+[^\n]*", re.MULTILINE)

    def test_decision_header_numbers_matches_pre_consolidation_r1_regex(self) -> None:
        """R1 used to hand-roll '^## Decision (\\d+):' over both files -- the shared helper (the
        '#{2,3}' grammar) must enumerate an identical population now that the archive promote
        landed (both regexes now see Decisions 52/53/54, previously h3-only and R1-invisible)."""
        r1_numbers: set[int] = set()
        for path in _DECISIONS_MD_PATHS:
            if path.exists():
                r1_numbers.update(int(n) for n in self._R1_PRE_CONSOLIDATION_RE.findall(path.read_text(encoding="utf-8")))
        assert decision_header_numbers() == r1_numbers

    def test_iter_decision_headings_matches_pre_consolidation_preflight_regex_on_live_file(self) -> None:
        """The preflight counter used to hand-roll '^## Decision \\d+[^\\n]*' over the live file
        only -- iter_decision_headings() must yield an identical count (derived, not hardcoded)."""
        live_path = _DECISIONS_MD_PATHS[0]
        assert live_path.name == "DECISIONS.md"
        live_content = live_path.read_text(encoding="utf-8")
        old_count = len(self._PREFLIGHT_PRE_CONSOLIDATION_RE.findall(live_content))
        new_count = len(iter_decision_headings(live_content))
        assert new_count == old_count

    def test_archive_promote_lands_decisions_52_53_54_in_both_files_number_set(self) -> None:
        assert {52, 53, 54} <= decision_header_numbers()

    def test_archive_no_longer_carries_h3_numbered_decision_headers(self) -> None:
        """DPI-07: no '### Decision N:' header remains in the archive after the promote."""
        archive_path = _DECISIONS_MD_PATHS[1]
        assert archive_path.name == "DECISIONS_ARCHIVE.md"
        content = archive_path.read_text(encoding="utf-8")
        assert not re.search(r"^### Decision \d+:", content, re.MULTILINE)

    def test_decision_header_numbers_paths_seam_is_honored_not_ignored(self, tmp_path: Path) -> None:
        """R1-guard hinge: decision_header_numbers(paths=...) must use the GIVEN paths, never
        silently fall back to this module's own repo root -- the R1 guard depends on this to
        honor a patched scripts.checks._common.ROOT."""
        only = tmp_path / "only.md"
        only.write_text("## Decision 7: Test (Decided)\n", encoding="utf-8")
        assert decision_header_numbers(paths=[only]) == {7}

    def test_decision_header_numbers_missing_path_is_skipped(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.md"
        assert decision_header_numbers(paths=[missing]) == set()

    def test_iter_decision_headings_returns_match_objects_in_file_order(self) -> None:
        content = "## Decision 2: Second (Decided)\n\nbody\n\n## Decision 1: First (Decided)\n\nbody\n"
        matches = iter_decision_headings(content)
        assert [int(m.group(1)) for m in matches] == [2, 1]


class TestByteReconstruction:
    """Per-file coverage: preamble + concatenated heading-inclusive raw_blocks == source file."""

    @pytest.mark.parametrize("md_path", _DECISIONS_MD_PATHS, ids=lambda p: p.name)
    def test_byte_reconstruction_reconstructs_source_file_exactly(self, md_path: Path) -> None:
        content = md_path.read_text(encoding="utf-8", errors="replace")
        sections = _iter_decision_sections(content)
        assert sections, f"{md_path} produced no decision sections"
        preamble = content[: sections[0][0].start()]
        reconstructed = preamble + "".join(raw_block for _, raw_block in sections)
        assert reconstructed == content

    def test_byte_reconstruction_synthetic_sectioner_narrowing_mutation_fails(self) -> None:
        """Proves the reconstruction check is a live invariant, not vacuously true.

        A sectioner that silently narrows a raw_block (drops trailing bytes) must fail the
        same byte-equality assertion the real coverage test above relies on -- otherwise
        future drift in the sectioning boundaries would fail silently, not loudly.
        """
        fixture = (
            "# Preamble text\n\n"
            "## Decision 1: First (Decided)\n\nBody one.\n\n---\n\n"
            "## Decision 2: Second (Decided)\n\nBody two.\n"
        )
        sections = _iter_decision_sections(fixture)
        assert len(sections) == 2
        preamble = fixture[: sections[0][0].start()]

        good_reconstruction = preamble + "".join(rb for _, rb in sections)
        assert good_reconstruction == fixture

        # Simulate a sectioner bug: narrow the first raw_block by dropping trailing bytes.
        narrowed_sections = [(sections[0][0], sections[0][1][:-5]), sections[1]]
        bad_reconstruction = preamble + "".join(rb for _, rb in narrowed_sections)
        assert bad_reconstruction != fixture


class TestIntentMarker:
    """Decision 151 / PLAN-dcg-intent-capture (audit finding DCG-06): the optional Intent
    marker's typed extraction. Optional forever -- no required-marker change, no
    retro-enforcement of the historical band; the existing context extraction (Rationale/Key
    details/Context) is untouched.
    """

    _BODY_WITH_INTENT = "**Status:** Decided\n\n**Intent:** Make the durable why quotable.\n\n**Decision:** Do the thing.\n"
    _ENTRY_WITH_INTENT = "## Decision 1: Synthetic (Decided)\n\n" + _BODY_WITH_INTENT
    _ENTRY_WITHOUT_INTENT = "## Decision 1: Synthetic (Decided)\n\n**Status:** Decided\n\n**Decision:** Do the thing.\n"

    def test_extract_multiline_section_returns_intent_marker_body(self) -> None:
        assert _extract_multiline_section(self._BODY_WITH_INTENT, "Intent") == "Make the durable why quotable."

    def test_markerless_block_parses_to_empty_intent(self, tmp_path: Path) -> None:
        path = tmp_path / "DECISIONS.md"
        path.write_text(self._ENTRY_WITHOUT_INTENT, encoding="utf-8")
        rows = parse_decisions_md(paths=[path])
        assert rows[0]["intent"] == ""

    def test_intent_marker_round_trips_through_parse_decisions_md(self, tmp_path: Path) -> None:
        path = tmp_path / "DECISIONS.md"
        path.write_text(self._ENTRY_WITH_INTENT, encoding="utf-8")
        rows = parse_decisions_md(paths=[path])
        assert rows[0]["intent"] == "Make the durable why quotable."

    def test_q3b_intent_only_edit_changes_content_hash(self, tmp_path: Path) -> None:
        """Inserting an **Intent:** marker changes the parser-normalized content_hash -- the
        gate the backfill's differential SCD2 write depends on. No separate intent hash."""
        without_path = tmp_path / "without" / "DECISIONS.md"
        with_path = tmp_path / "with" / "DECISIONS.md"
        without_path.parent.mkdir()
        with_path.parent.mkdir()
        without_path.write_text(self._ENTRY_WITHOUT_INTENT, encoding="utf-8")
        with_path.write_text(self._ENTRY_WITH_INTENT, encoding="utf-8")

        without_rows = parse_decisions_md(paths=[without_path])
        with_rows = parse_decisions_md(paths=[with_path])
        assert without_rows[0]["content_hash"] != with_rows[0]["content_hash"]

    def test_live_corpus_has_at_least_one_non_empty_intent(self, parsed_rows: dict[int, dict]) -> None:
        """The governing Decision's own **Intent:** marker round-trips, robust to renumbering."""
        assert any(row.get("intent") for row in parsed_rows.values())


class TestAmendsEdgeExtraction:
    """DCG-08 / PLAN-dcg-decisions-index: extract_amends_edges() unit tests against the REAL
    committed corpus titles (verbatim, grepped fresh from docs/DECISIONS.md) -- the freshness
    guard only checks determinism (committed == regenerated), never edge-correctness, so these
    tests are the SOLE guard against a wrong 'amends' edge silently committing green.
    """

    def test_dec150_single_target(self) -> None:
        raw_title = (
            "Decision-log growth direction -- significance bar for numbered Decisions + "
            "batch-wave ratified form (amends Decision 105) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [105]

    def test_dec148_excludes_mirrors_clause(self) -> None:
        """'(amends Decision 104, mirrors Decision 132)' -> {104} -- NOT {104, 132}: the
        comma continuation requires a bare number, not another relation word."""
        raw_title = (
            "VF-01 hermetic VP-replay: replay at implement-time, not plan-time; "
            "hermetic-default restored (amends Decision 104, mirrors Decision 132) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [104]

    def test_dec144_multi_target_via_and_excludes_extends_clause(self) -> None:
        """'(...; amends Decision 98 point 1 and Decision 92 point 5; extends Decision 129 ...)'
        -> {98, 92}: 'point N' suffixes are tolerated (not extra targets), the 'and Decision N'
        continuation captures the second target, and the trailing 'extends' clause (a
        different relation word) is excluded."""
        raw_title = (
            "Allow-list inversion: broad-but-bounded deployer under the mandatory permissions "
            "boundary as the target Terraform deploy/IAM model (forward-direction; amends "
            "Decision 98 point 1 and Decision 92 point 5; extends Decision 129 from reads to "
            "writes) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [98, 92]

    def test_dec153_plus_continuation_with_bare_word_qualifier(self) -> None:
        """'(amends Decision 73 enforcement + Decision 135)' -> {73, 135}: 'enforcement' is a
        bare-word qualifier (no trailing number, unlike 'point N'/'cl.N'/'clause N') and '+' is
        a plus-continuation separator -- both landed live in dec-153 mid-session, proving the
        grammar generalizes past the four qualifier/continuation forms seen at plan time."""
        raw_title = (
            "Fast-tier budget assertion waives a full-suite-forced --pre run (warn, not "
            "hard-fail) bounded by a forced-run ceiling derived from the pr-validate job "
            "timeout (amends Decision 73 enforcement + Decision 135) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [73, 135]

    def test_dec143_cl_suffix_tolerated(self) -> None:
        raw_title = (
            "Privileged-verb Lambda decomposition -- scope identities by worst reachable verb; "
            "enforce the boundary in a primitive outside the agent merge loop (amends Decision "
            "81 cl.1) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [81]

    def test_dec135_excludes_ordinal_and_builds_on_clause(self) -> None:
        """'(amends Decision 73, 2nd amendment; builds on 80/104/124/131)' -> {73}, NOT
        {73, 2}: '2nd' is an ordinal (not a plural-list continuation -- \\b fails between the
        digit and the following letter), and 'builds on 80/104/124/131' is a separate clause
        with no 'Decision' token at all."""
        raw_title = (
            "Live, cacheless, strictly-additive affected-set selection replaces the --pre "
            "edited-set tier gate (amends Decision 73, 2nd amendment; builds on "
            "80/104/124/131) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [73]

    def test_dec130_plural_slash_form(self) -> None:
        """'(...; amends Decisions 102/128 scan scope)' -> {102, 128}: the plural 'Decisions
        N/M' slash-list form, and the preceding 'completes Decision 43' clause (before the
        amends anchor) is correctly excluded."""
        raw_title = (
            "Structural-size governance covers the whole repo -- one-time grandfather of "
            "tests/ debt (completes Decision 43; amends Decisions 102/128 scan scope) (Decided)"
        )
        assert extract_amends_edges(raw_title) == [102, 128]

    def test_dec83_case_insensitive_anchor(self) -> None:
        """Title-case 'Amends' (mid-title, after '--') must still match."""
        raw_title = "Branch Protection Now Active -- Amends Decision 89 Premise (Decided)"
        assert extract_amends_edges(raw_title) == [89]

    def test_dec122_does_not_match_inverse_amended_by(self) -> None:
        """Real INVERSE 'amended by Decision N' title -- 'amended' is a different whole word
        from 'amends'; must NOT produce a forward edge."""
        raw_title = (
            "Ratify CD.28 -- executor LLM inference = DeepSeek-direct via LiteLLM (Tier 1), "
            "Anthropic-direct (Tier 2); Bedrock retired (as amended by Decision 116) (Decided)"
        )
        assert extract_amends_edges(raw_title) == []

    def test_dec116_ignores_cd_dot_n_target(self) -> None:
        """'amends CD.28's scheduled-agent clause' -- CD.28 is not 'Decision N'; must not be
        captured (the CD.N-target it precedes, 'Supersedes Decision 49', is a supersedes
        concern, not amends, and is covered separately)."""
        raw_title = (
            "Scheduled-agent provider routing -- routine/non-agentic agents to LiteLLM "
            "(DeepSeek), judgment/agentic agents to claude -p (Supersedes Decision 49; amends "
            "CD.28's scheduled-agent clause) (Decided)"
        )
        assert extract_amends_edges(raw_title) == []

    def test_live_corpus_dec150_amends_105_round_trip(self, parsed_rows: dict[int, dict]) -> None:
        """End-to-end: the live corpus's actual dec-150 row carries amends=[105] via the
        parse_decisions_md wiring, not just the isolated function."""
        assert 105 in parsed_rows[150]["amends"]

    def test_live_corpus_dec143_amends_81_round_trip(self, parsed_rows: dict[int, dict]) -> None:
        assert 81 in parsed_rows[143]["amends"]


class TestTitleBorneSupersedesExtraction:
    """DCG-08: extract_title_borne_supersedes() -- the generic title-relation helper reused
    for the forward 'Supersedes Decision N' idiom, sharing extract_amends_edges' grammar."""

    def test_dec117_single_target(self) -> None:
        raw_title = (
            "Executor self-modification boundary -- capabilities.yaml is the code-level SSOT "
            "(Supersedes Decision 44) (Decided)"
        )
        assert extract_title_borne_supersedes(raw_title) == [44]

    def test_dec52_plural_comma_multi_target(self) -> None:
        raw_title = "Bedrock Migration -- DeepSeek V3.2 Default (Supersedes Decisions 37, 40, 49)"
        assert extract_title_borne_supersedes(raw_title) == [37, 40, 49]

    def test_dec42_does_not_match_inverse_superseded_by(self) -> None:
        """'Superseded by Decision 90' is the inverse direction -- a different whole word
        ('superseded', not 'supersedes') -- must not produce a forward edge."""
        raw_title = "Three-Tier Workflow Architecture (Superseded by Decision 90)"
        assert extract_title_borne_supersedes(raw_title) == []

    def test_dec114_ignores_kg_dot_n_target(self) -> None:
        """'supersedes KG.11's ...' -- KG.11 is not 'Decision N'; must not be captured."""
        raw_title = (
            "Raise the ROADMAP-PLATFORM.yaml size ceiling to 10,000 lines and add a "
            "deterministic guard (supersedes KG.11's 2500-line/50K-token trigger) (Decided)"
        )
        assert extract_title_borne_supersedes(raw_title) == []

    def test_dec113_ignores_prose_target(self) -> None:
        """'supersedes Identity Center ...' -- a prose target, not 'Decision N'."""
        raw_title = (
            "Ratify CD.26 -- Pattern-B agent auth (static-key + chained AssumeRole) "
            "supersedes Identity Center for the personal account (Decided)"
        )
        assert extract_title_borne_supersedes(raw_title) == []


class TestAmendsStaysIndexOnly:
    """Decision 84 warehouse-safety boundary (mirrors the Wave-4 intent-field test pattern,
    inverted: intent is DELIBERATELY added to the backfill surface, amends is DELIBERATELY
    excluded from it). Confirms the 'amends' and 'title_supersedes' parser keys can never reach
    ops_decisions, protecting the Decision-134 fidelity tripwire from an unplanned schema
    perturbation.
    """

    def test_amends_stays_index_only(self) -> None:
        from scripts.ops_portal.decisions import _DECISION_BACKFILL_COLS
        from src.schemas.decision import DecisionPayload

        assert "amends" not in _DECISION_BACKFILL_COLS
        assert DecisionPayload.model_config.get("extra") == "ignore"

        row = next(r for r in parse_decisions_md() if r["decision_id"] == 150)
        assert row.get("amends"), "dec-150 must carry a non-empty amends list for this assertion to be live"

        kept = {k: v for k, v in row.items() if k in _DECISION_BACKFILL_COLS and v not in (None, "")}
        assert "amends" not in kept

    def test_title_supersedes_stays_index_only(self) -> None:
        """The sibling row key (title-borne supersedes source) gets the identical treatment."""
        from scripts.ops_portal.decisions import _DECISION_BACKFILL_COLS
        from src.schemas.decision import DecisionPayload

        assert "title_supersedes" not in _DECISION_BACKFILL_COLS
        assert "title_supersedes" not in DecisionPayload.model_fields
        assert DecisionPayload.model_config.get("extra") == "ignore"

        row = next(r for r in parse_decisions_md() if r["decision_id"] == 117)
        assert row.get("title_supersedes"), "dec-117 must carry a non-empty title_supersedes list for this to be live"

        kept = {k: v for k, v in row.items() if k in _DECISION_BACKFILL_COLS and v not in (None, "")}
        assert "title_supersedes" not in kept

    def test_amends_not_a_decision_payload_field(self) -> None:
        from src.schemas.decision import DecisionPayload

        assert "amends" not in DecisionPayload.model_fields


class TestDualModelFieldSync:
    """DecisionPayload (write-side) and jsonl_store.Decision (read-side) must both carry
    the four DAF-01 fields plus intent (Decision 151 / PLAN-dcg-intent-capture), as plain
    (non-Dq-Annotated) optional strings, with the dual-write invariant preserved.
    """

    _NEW_FIELDS = ("raw_block", "reversal_conditions", "superseded_by", "content_hash")

    def test_dual_model_both_declare_the_four_fields(self) -> None:
        from scripts.executor.jsonl_store import Decision
        from src.schemas.decision import DecisionPayload

        for field in self._NEW_FIELDS:
            assert field in DecisionPayload.model_fields, f"DecisionPayload missing {field}"
            assert field in Decision.model_fields, f"jsonl_store.Decision missing {field}"

    def test_dual_model_fields_are_plain_not_annotated(self) -> None:
        """Never Annotated[...]/DqNotNull -- would redden validate_pydantic_yaml_drift."""
        from src.schemas.decision import DecisionPayload

        hints = typing.get_type_hints(DecisionPayload, include_extras=True)
        for field in self._NEW_FIELDS:
            assert typing.get_origin(hints[field]) is not typing.Annotated, (
                f"DecisionPayload.{field} must be a plain str | None, not Annotated[...]"
            )

    def test_dual_model_record_validates_on_both_and_dual_write_invariant_holds(self) -> None:
        from scripts.executor.jsonl_store import Decision
        from src.schemas.decision import DecisionPayload

        record = {
            "id": "dec-999",
            "decision_id": 999,
            "title": "Synthetic test decision",
            "status": "Decided",
            "created_timestamp": "2026-07-16T00:00:00Z",
            "last_updated_timestamp": "2026-07-16T00:00:00Z",
            "raw_block": "## Decision 999: Synthetic test decision (Decided)\n\n**Decision:** test.",
            "reversal_conditions": "revisit if X changes",
            "superseded_by": "dec-998",
            "content_hash": "a" * 64,
        }
        payload = DecisionPayload.model_validate(record)
        read_side = Decision.model_validate(record)
        assert payload.raw_block == read_side.raw_block == record["raw_block"]
        assert payload.reversal_conditions == read_side.reversal_conditions == record["reversal_conditions"]
        assert payload.superseded_by == read_side.superseded_by == record["superseded_by"]
        assert payload.content_hash == read_side.content_hash == record["content_hash"]

        mismatched = {**record, "decision_id": 998}
        with pytest.raises(Exception, match="Dual-write invariant"):
            DecisionPayload.model_validate(mismatched)
        with pytest.raises(Exception, match="Dual-write invariant"):
            Decision.model_validate(mismatched)

    def test_intent_field_synced_plain_on_both_models(self) -> None:
        """Decision 151 / PLAN-dcg-intent-capture: intent is declared plain (non-Annotated) on
        both models, mirroring the four DAF-01 fields above, and a record carrying it
        validates on both."""
        from scripts.executor.jsonl_store import Decision
        from src.schemas.decision import DecisionPayload

        assert "intent" in DecisionPayload.model_fields, "DecisionPayload missing intent"
        assert "intent" in Decision.model_fields, "jsonl_store.Decision missing intent"

        hints = typing.get_type_hints(DecisionPayload, include_extras=True)
        assert typing.get_origin(hints["intent"]) is not typing.Annotated, (
            "DecisionPayload.intent must be a plain str | None, not Annotated[...]"
        )

        record = {
            "id": "dec-997",
            "decision_id": 997,
            "title": "Synthetic intent test decision",
            "status": "Decided",
            "created_timestamp": "2026-07-24T00:00:00Z",
            "last_updated_timestamp": "2026-07-24T00:00:00Z",
            "intent": "Make the durable why quotable.",
        }
        payload = DecisionPayload.model_validate(record)
        read_side = Decision.model_validate(record)
        assert payload.intent == read_side.intent == record["intent"]


class TestPublicAliases:
    """DAF-03 promotion (PLAN-size-gov-marker-guard): both names are now public, with the
    historical private spelling kept as an identity alias for existing internal callers."""

    def test_iter_decision_sections_is_public_alias_of_private(self) -> None:
        assert iter_decision_sections is _iter_decision_sections

    def test_extract_superseded_by_is_public_alias_of_private(self) -> None:
        assert extract_superseded_by is _extract_superseded_by


class TestExtractSectionHelper:
    """_extract_section is a pre-existing single-line extraction helper (unused elsewhere in
    the codebase currently, but retained public surface); covered directly for completeness."""

    def test_extract_section_returns_matched_value(self) -> None:
        text = "**Foo:** bar baz\n\n**Next:** ignored"
        assert _extract_section(text, "Foo") == "bar baz"

    def test_extract_section_returns_empty_when_no_key_matches(self) -> None:
        assert _extract_section("no markers here", "Missing") == ""


class TestDecidedDateIsoFallback:
    """The Status-suffix fallback in _extract_decided_date (DAF-01 ISO guard)."""

    def test_status_suffix_fallback_accepts_iso_shaped_value(self) -> None:
        text = "**Status:** Decided -- 2026-04-01"
        assert _extract_decided_date(text) == "2026-04-01"

    def test_status_suffix_fallback_rejects_non_iso_value(self) -> None:
        text = "**Status:** Active -- remove when reversal condition is met"
        assert _extract_decided_date(text) == ""


class TestParseDecisionsMdMissingFile:
    def test_parse_decisions_md_skips_nonexistent_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.md"
        assert parse_decisions_md(paths=[missing]) == []


class TestParseDecisionsMdDuplicateDedup:
    """The first-wins dedup at the `if decision_id in seen: continue` site (DPI-06) now emits a
    stderr WARNING naming the dropped decision number -- behavior-preserving: the returned parse
    result and the byte-reconstruction invariant are unchanged.
    """

    def test_duplicate_number_emits_stderr_warning_and_keeps_first_entry(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "DECISIONS.md"
        path.write_text(
            "## Decision 1: First entry (Decided)\n\n**Status:** Decided\n\n---\n\n"
            "## Decision 1: Second entry (Decided)\n\n**Status:** Decided\n",
            encoding="utf-8",
        )
        result = parse_decisions_md(paths=[path])
        err = capsys.readouterr().err
        assert "WARNING: duplicate decision number 1 in DECISIONS.md" in err
        assert len(result) == 1
        assert result[0]["title"] == "First entry"

    def test_no_duplicate_across_files_emits_no_warning(self, tmp_path: Path, capsys) -> None:
        live = tmp_path / "DECISIONS.md"
        archive = tmp_path / "DECISIONS_ARCHIVE.md"
        live.write_text("## Decision 1: Live entry (Decided)\n\n**Status:** Decided\n", encoding="utf-8")
        archive.write_text("## Decision 2: Archive entry (Decided)\n\n**Status:** Decided\n", encoding="utf-8")
        result = parse_decisions_md(paths=[live, archive])
        assert capsys.readouterr().err == ""
        assert len(result) == 2


class TestReadJsonl:
    """read_jsonl -- a pre-existing local-JSONL reader retained on this module."""

    def test_read_jsonl_parses_valid_entries_and_skips_blanks_and_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "sample.jsonl"
        path.write_text('# a comment\n\n{"id": "dec-001"}\n{"id": "dec-002"}\n', encoding="utf-8")
        assert read_jsonl(path) == [{"id": "dec-001"}, {"id": "dec-002"}]

    def test_read_jsonl_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.jsonl"
        assert read_jsonl(missing) == []

    def test_read_jsonl_skips_malformed_json_line(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"id": "dec-001"}\nnot json\n{"id": "dec-002"}\n', encoding="utf-8")
        assert read_jsonl(path) == [{"id": "dec-001"}, {"id": "dec-002"}]

    def test_read_jsonl_skips_schema_comment_line(self, tmp_path: Path) -> None:
        path = tmp_path / "schema.jsonl"
        path.write_text('{"_schema": "v1"}\n{"id": "dec-001"}\n', encoding="utf-8")
        assert read_jsonl(path) == [{"id": "dec-001"}]
