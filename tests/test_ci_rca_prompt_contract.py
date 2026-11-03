"""Prompt-content contract tests for .claude/agents/scheduled/ci-rca.md.

Guards required tokens and the absence of stale positional CLI forms after
the Phase 3 prompt rewrite (PLAN-ci-rca-prompt-rewrite).
"""

import re
from pathlib import Path

import pytest

_AGENT_MD = Path(__file__).parent.parent / ".claude" / "agents" / "scheduled" / "ci-rca.md"


@pytest.fixture(scope="module")
def agent_text() -> str:
    return _AGENT_MD.read_text(encoding="utf-8")


class TestRequiredTokensPresent:
    def test_reads_evidence_bundle(self, agent_text: str) -> None:
        """Agent reads the evidence bundle from the local path."""
        assert "bundle" in agent_text.lower()
        assert "cat" in agent_text

    def test_guidance_source_ci_rca(self, agent_text: str) -> None:
        """Agent calls --guidance --source ci_rca to load the schema."""
        assert "--guidance" in agent_text
        assert "--source ci_rca" in agent_text

    def test_context_v2_json_flag_present(self, agent_text: str) -> None:
        """Agent uses --context-v2-json when filing the rec."""
        assert "--context-v2-json" in agent_text

    def test_filed_marker_present(self, agent_text: str) -> None:
        """Agent emits FILED: <rec_id> as the terminal output marker."""
        assert "FILED:" in agent_text

    def test_priority_critical_capitalized(self, agent_text: str) -> None:
        """Agent uses --priority Critical (capital C, not lowercase)."""
        assert "--priority Critical" in agent_text

    def test_file_rec_flag_used(self, agent_text: str) -> None:
        """Agent uses --file-rec (not positional file_rec subcommand)."""
        assert "--file-rec" in agent_text

    def test_no_autonomous_fix_invariant_present(self, agent_text: str) -> None:
        """No-autonomous-fix Hard Rule is preserved (Decision 55/72)."""
        assert "autonomous fix" in agent_text.lower() or "autonomous" in agent_text.lower()


_SYSTEMIC_KEYWORDS = (
    "gate",
    "tier",
    "policy",
    "contract",
    "gap",
    "missing",
    "absent",
    "placement",
    "scope",
    "invariant",
    "enforcement",
)


class TestCirca07MultiFilingAndPromptStringAlignment:
    def test_primary_cause_only_sentence_absent(self, agent_text: str) -> None:
        """CIRCA-07: the primary-cause-only collapse instruction is gone."""
        assert "primary cause only" not in agent_text.lower()

    def test_one_rec_per_bundle_language_present(self, agent_text: str) -> None:
        assert "one rec per bundle" in agent_text.lower()

    def test_filed_per_rec_category_grammar_present(self, agent_text: str) -> None:
        """Step 6 documents the one-line-per-rec FILED: <rec_id> <failure_category> grammar."""
        assert "FILED: <rec_id> <failure_category>" in agent_text

    def test_extracts_local_bundle_path(self, agent_text: str) -> None:
        """Matches the workflow's renamed prompt string 'Local bundle path:' (CIRCA-03(b))."""
        assert "Local bundle path:" in agent_text


class TestCirca04WhyChainCeilingAndKeywords:
    def test_40_400_ceiling_present(self, agent_text: str) -> None:
        assert "40-400" in agent_text

    def test_stale_40_250_ceiling_absent(self, agent_text: str) -> None:
        assert "40-250" not in agent_text

    def test_all_systemic_keywords_listed_verbatim(self, agent_text: str) -> None:
        for kw in _SYSTEMIC_KEYWORDS:
            assert f"`{kw}`" in agent_text, f"systemic keyword {kw!r} not listed verbatim"

    def test_file_line_citation_requirement_present(self, agent_text: str) -> None:
        assert "file:line citation" in agent_text.lower()


class TestStaleFormsAbsent:
    def test_no_positional_file_rec(self, agent_text: str) -> None:
        """Stale positional 'ops_data_portal file_rec' form is absent."""
        assert not re.search(r"ops_data_portal\s+file_rec(\s|$)", agent_text)

    def test_no_positional_get_rec_write_guidance(self, agent_text: str) -> None:
        """Stale positional 'ops_data_portal get_rec_write_guidance' form is absent."""
        assert not re.search(r"ops_data_portal\s+get_rec_write_guidance(\s|$)", agent_text)

    def test_no_five_category_taxonomy_step(self, agent_text: str) -> None:
        """Legacy free-form 5-category taxonomy step (hardcoded category list) is absent."""
        stale_categories = [
            "IAM gap",
            "Schema drift",
            "Dependency gap",
        ]
        for cat in stale_categories:
            assert cat not in agent_text, f"Stale taxonomy category still present: {cat!r}"

    def test_no_lowercase_priority_critical(self, agent_text: str) -> None:
        """Stale '--priority critical' (lowercase) is absent; only '--priority Critical' is used."""
        assert "--priority critical" not in agent_text


class TestRepairTrackInstructions:
    """Decision 226: the agent records repair_track and passes High only for a Track A red."""

    def test_repair_track_with_both_values(self, agent_text: str) -> None:
        assert "repair_track" in agent_text
        assert "test_defect" in agent_text and "product_regression" in agent_text

    def test_high_priority_only_for_track_a(self, agent_text: str) -> None:
        assert "--priority High" in agent_text
        assert "High` for a Track A red" in agent_text

    def test_preventive_action_omitted_only_for_track_a(self, agent_text: str) -> None:
        assert "Omit `preventive_action` only when `repair_track` is `test_defect`" in agent_text
        assert "except preventive_action for a Track A red" in agent_text

    def test_hard_rules_name_the_fast_lane(self, agent_text: str) -> None:
        assert "the fast lane for a red the portal files High (Decision 226)" in agent_text
        assert "--priority Critical" in agent_text
        assert "autonomous fix" in agent_text
