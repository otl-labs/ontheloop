"""Contract test for docs/contracts/implement-scope-boundary.yaml (PLAN-inline-defer-boundary-
contract, rec-3332 items 2-4, Decision 59 discharge, Decision 181).

Covers: both invariants declared, the CONTENT enforcement-coverage map (a single rec-owned
residual, never an unqualified binary invariant), sanction_rows as conditional trigger ->
derived-path rows (never a flat allowlist), AGENTS.md and the implement skill pointing at the
contract rather than restating it, the file-router route (topic-equals-subject + a dated
amendment_log entry), Decision 181's required Significance envelope and reversal conditions, and
AC 10's tri-surface STOP-disposition identity (contract row, check module failure guidance,
deviation trigger branch (b)).
"""

from __future__ import annotations

import re
import typing
from pathlib import Path
from typing import Any

import yaml

from scripts.checks.verification import validate_scope_boundary as _check_module
from scripts.decisions_md import iter_decision_sections
from scripts.roadmap.plan_document import WeakeningDisposition

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "implement-scope-boundary.yaml"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "implement" / "SKILL.md"
CODE_REVIEW_SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "code-review" / "SKILL.md"
FILE_ROUTER_PATH = REPO_ROOT / "docs" / "contracts" / "file-router.yaml"
DECISIONS_PATH = REPO_ROOT / "docs" / "DECISIONS.md"

_STOP_DISPOSITION = (
    "STOP: an undeclared touched path is never resolved by editing the plan's own scope -- the "
    "escape is a human-directed plan amendment landed as its own reviewed act, never a unilateral append."
)


def _load_contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _decision_block(number: int) -> str:
    content = DECISIONS_PATH.read_text(encoding="utf-8")
    for match, block in iter_decision_sections(content):
        if int(match.group(1)) == number:
            return block
    raise AssertionError(f"Decision {number} not found in DECISIONS.md")


class TestContractShape:
    def test_both_invariants_declared(self) -> None:
        doc = _load_contract()
        invariants = doc["invariants"]
        assert "location" in invariants and "content" in invariants
        assert "Decision 59" in invariants["location"]["authority"]

    def test_location_invariant_names_the_deleted_path_visibility_gap(self) -> None:
        """AC 4: deleted paths are named as a known gap in the contract -- get_changed_files
        existence-filters them out and only get_status_aware_diff carries 'D' rows, so the seam
        choice decides whether a deletion is even visible."""
        doc = _load_contract()
        known_gaps = doc["invariants"]["location"].get("known_gaps")
        assert known_gaps, "location invariant must document the deleted-path visibility gap"
        gaps_text = " ".join(known_gaps)
        assert "get_changed_files" in gaps_text
        assert "get_status_aware_diff" in gaps_text
        assert "deleted" in gaps_text.lower() or "deletion" in gaps_text.lower()

    def test_content_invariant_has_enforcement_coverage_with_single_rec_owned_residual(self) -> None:
        doc = _load_contract()
        coverage = doc["invariants"]["content"]["enforcement_coverage"]
        assert coverage, "CONTENT invariant must carry a non-empty enforcement_coverage map"
        residual_rows = [row for row in coverage if row.get("status") != "mechanical"]
        assert len(residual_rows) == 1, "exactly one residual row is permitted in the CONTENT map"
        residual = residual_rows[0]
        assert residual["status"] == "residual_agent_obligation"
        assert re.match(r"^rec-\d+$", str(residual.get("residual_owner", ""))), (
            f"residual row must carry a real owning rec id, got {residual.get('residual_owner')!r}"
        )
        for row in coverage:
            if row.get("status") == "mechanical":
                assert row.get("enforced_by"), f"mechanical row has no enforcer: {row}"

    def test_content_invariant_never_an_unqualified_binary(self) -> None:
        """Decision 163 point 2: never a bare statement with no coverage map."""
        doc = _load_contract()
        content = doc["invariants"]["content"]
        assert content.get("enforcement_coverage"), "CONTENT must carry a non-empty enforcement_coverage map"

    def test_sanction_rows_are_conditional_not_flat_allowlist(self) -> None:
        doc = _load_contract()
        rows = doc["sanction_rows"]
        assert len(rows) >= 4
        for name, row in rows.items():
            assert "trigger" in row and "kind" in row["trigger"], (
                f"{name} has no trigger.kind -- looks like a flat allowlist entry"
            )
            assert row.get("purpose"), f"{name} has no purpose"

    def test_known_residuals_note_distinct_from_content_coverage_map(self) -> None:
        doc = _load_contract()
        content = doc["invariants"]["content"]
        known = content.get("known_residuals")
        assert known, "known_residuals must be non-empty"
        assert any("R6" in note for note in known)
        coverage_text = " ".join((row.get("note", "") + row.get("surface", "")) for row in content["enforcement_coverage"])
        assert "R6" not in coverage_text, (
            "the R6 split's own residual belongs in known_residuals, not the CONTENT coverage map"
        )

    def test_bookkeeping_row_permits_the_followon_recs_write_back(self) -> None:
        """PLAN-plan-followon-recs-field (PDB-01): /implement Step 7 closure writes a deferred
        half's filed rec id back into the resolved plan's followon_recs, ahead of the item that
        sets implementation_declared true -- sanctioned here alongside closes_criteria."""
        doc = _load_contract()
        row = doc["sanction_rows"]["implementing_plan_bookkeeping"]
        assert "followon_recs" in row["permitted_field_edits"]
        entries = doc["amendment_log"]
        assert any("plan-followon-recs-field" in (e.get("summary") or "") for e in entries)

    def test_sanction_row_eligibility_excludes_hand_written_test_files(self) -> None:
        """AC 3/AC 9: sanction_row_eligibility is a TOP-LEVEL key naming hand-written test files
        ineligible for a sanction row, citing the rec-3357 residual as the reason and rec-3586's
        narrowing as the consequence -- and the rule holds executably: no sanction_rows entry may
        derive a tests/** path."""
        doc = _load_contract()
        eligibility = doc["sanction_row_eligibility"]
        assert "sanction_row_eligibility" not in doc["sanction_rows"], (
            "sanction_row_eligibility must be a top-level sibling of sanction_rows, never nested inside it"
        )
        ineligible = eligibility["ineligible"]
        assert "hand-written test files" in ineligible.lower() or "hand-written test" in ineligible
        assert "rec-3357" in ineligible
        assert "rec-3586" in ineligible

        for name, row in doc["sanction_rows"].items():
            template = row.get("sanctions", {}).get("path_template", "")
            assert not template.startswith("tests/"), f"{name} derives a tests/** path despite sanction_row_eligibility"
            trigger_file = row.get("trigger", {}).get("file", "")
            assert not trigger_file.startswith("tests/"), f"{name}'s trigger names a tests/** path"

    def test_bookkeeping_row_documents_the_two_act_amendment_procedure(self) -> None:
        """AC 4/AC 5: the implementing_plan_bookkeeping row's amendment_procedure states the
        two-act escape (scope amendment lands on main first; a single diff cannot satisfy both
        invariants), and the amendment_log entry that introduces it records both additions plus
        the Decision 75 conscious-choice note. Selecting on "Decision 75" alone would be vacuous
        (the 2026-09-04 secrets_baseline_regeneration entry already carries that phrase) -- select
        on sanction_row_eligibility, a token unique to this change, first."""
        doc = _load_contract()
        row = doc["sanction_rows"]["implementing_plan_bookkeeping"]
        procedure = row["amendment_procedure"]
        assert "lands on" in procedure and "main" in procedure
        assert "single diff" in procedure or "one diff" in procedure

        entries = [e for e in doc["amendment_log"] if "sanction_row_eligibility" in (e.get("summary") or "")]
        assert len(entries) == 1, "expected exactly one amendment_log entry naming sanction_row_eligibility"
        summary = entries[0]["summary"]
        assert "amendment_procedure" in summary
        assert "Decision 75" in summary and "conscious-choice" in summary

    def test_roadmap_liveness_baseline_row_declared_and_argued(self) -> None:
        """AC 1-6 (PLAN-roadmap-liveness-baseline-companion): the roadmap_liveness_baseline_shrink
        row's trigger/sanctions shape, and its amendment_log entry's determinism argument
        (leg A(a)/A(d)), Decision 75 conscious-choice note, and all three named residuals --
        pinned WITHOUT the literal token sanction_row_eligibility (that token is pinned to exactly
        the 2026-09-07 entry above; a token-based selector here would add a second entry carrying
        it and trip that pin)."""
        doc = _load_contract()
        row = doc["sanction_rows"]["roadmap_liveness_baseline_shrink"]
        assert row["trigger"]["kind"] == "scope_contains_file"
        assert row["trigger"]["file"] == "docs/ROADMAP-PLATFORM.yaml"
        assert row["sanctions"]["path_template"] == "config/roadmap_liveness_baseline.yaml"
        assert row.get("prohibited_field_edits") == []

        entries = [e for e in doc["amendment_log"] if e.get("date") == "2026-09-08"]
        assert len(entries) == 1, "expected exactly one amendment_log entry dated 2026-09-08"
        summary = entries[0]["summary"]
        assert "sanction_row_eligibility" not in summary, (
            "the new entry must argue ineligibility descriptively, never with the literal token"
        )

        # Determinism/latitude argument (leg A(a) + leg A(d) jointly pin entries).
        assert "leg A(a)" in summary and "leg A(d)" in summary
        assert "toxic_node_ids" in summary

        # Decision 75 conscious-choice note.
        assert "Decision 75" in summary and "conscious-choice" in summary

        # Three named residuals, scoped to this entry's own summary (never a whole-document
        # search -- "a path a tool mechanically regenerates" is also a verbatim substring of the
        # eligibility clause itself, which would make a doc-wide search vacuous).
        assert "header" in summary.lower() and "unpinned" in summary.lower()
        assert "_BASELINE_SEED" in summary
        assert "a path a tool mechanically regenerates" in summary

    def test_secrets_baseline_retired_from_unmodelled_companions(self) -> None:
        """PLAN-secrets-baseline-sanction-row: .secrets.baseline is no longer an unmodelled
        companion -- it is now the secrets_baseline_regeneration sanction row's own subject --
        while config/composite_action_body_baseline.yaml remains unmodelled (still below the
        evidence bar for its own trigger rule)."""
        doc = _load_contract()
        companions = {row["path"] for row in doc["unmodelled_companions"]}
        assert ".secrets.baseline" not in companions
        assert "config/composite_action_body_baseline.yaml" in companions
        row = doc["sanction_rows"]["secrets_baseline_regeneration"]
        assert row["trigger"]["kind"] == "scope_file_in_secrets_baseline"
        assert row["sanctions"]["path_template"] == ".secrets.baseline"


class TestProseSurfacesPointAtContract:
    def test_agents_md_safety_bullet_points_at_contract(self) -> None:
        text = AGENTS_PATH.read_text(encoding="utf-8")
        assert "implement-scope-boundary.yaml" in text
        assert "Decision 59" in text

    def test_implement_skill_deviation_trigger_has_three_branches_and_pointers(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        i = text.find("### Deviation trigger")
        assert i >= 0, "### Deviation trigger section absent"
        section = re.split(r"\n#{2,3} ", text[i:], maxsplit=1)[0]
        assert "(a)" in section and "(b)" in section and "(c)" in section
        assert "Fable Advice-Consult Protocol" in section
        assert "overseer/SKILL.md" in section
        assert "3-fix-attempt" in section and "VF-08" in section

    def test_implement_skill_deviation_trigger_adds_no_auto_file_path(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        i = text.find("### Deviation trigger")
        section = re.split(r"\n#{2,3} ", text[i:], maxsplit=1)[0]
        for token in ("file_rec", "ops_data_portal", "--file-rec"):
            assert token not in section

    def test_code_review_skill_requires_weakening_dispositions(self) -> None:
        """PLAN-test-weakening-surfacing: the Testing section's Weakening candidates bullet and
        the Output Format's Weakening Dispositions section, asserted on the bullet alone (since
        "High" already appears elsewhere in the Testing section)."""
        text = CODE_REVIEW_SKILL_PATH.read_text(encoding="utf-8")
        i = text.find("### 3. Testing")
        assert i >= 0
        section = re.split(r"\n#{2,3} ", text[i:], maxsplit=1)[0]
        bullet_match = re.search(r"- \*\*Weakening candidates\*\*.*?(?=\n- |\Z)", section, re.DOTALL)
        assert bullet_match, "the Weakening candidates bullet is missing from the Testing section"
        bullet = bullet_match.group(0)
        assert "scripts.checks.verification.validate_weakening_candidates" in bullet
        assert "review_dispositions" in bullet
        assert "High" in bullet

        output_format_idx = text.find("### Test Completeness")
        assert output_format_idx >= 0
        tail = text[output_format_idx:]
        assert "### Weakening Dispositions" in tail
        assert "weakening_dispositions" in tail


class TestFileRouterRegistration:
    def test_route_topic_equals_subject(self) -> None:
        doc = yaml.safe_load(FILE_ROUTER_PATH.read_text(encoding="utf-8"))
        routes = {r["topic"]: r for r in doc["routes"]}
        assert "implement-scope-boundary" in routes
        assert routes["implement-scope-boundary"]["targets"] == ["docs/contracts/implement-scope-boundary.yaml"]

    def test_amendment_log_has_dated_entry_for_this_change(self) -> None:
        doc = yaml.safe_load(FILE_ROUTER_PATH.read_text(encoding="utf-8"))
        entries = doc["amendment_log"]
        assert any("implement-scope-boundary" in (e.get("summary") or "") for e in entries)


class TestDecision181:
    def test_significance_envelope_present(self) -> None:
        block = _decision_block(181)
        fence = "```yaml"
        i = block.find(fence)
        assert i >= 0, "Decision 181 must carry a fenced yaml metadata envelope"
        end = block.find("```", i + len(fence))
        env = yaml.safe_load(block[i + len(fence) : end])
        assert env["number"] == 181
        sig = env["significance"]
        assert sig["value"] == "numbered_decision"
        justification = sig["justification"]
        assert "implement-scope-boundary.yaml" in justification
        assert "amendment" in justification.lower()

    def test_reversal_conditions_present(self) -> None:
        block = _decision_block(181)
        assert "eversal" in block

    def test_points_at_contract_rather_than_restating(self) -> None:
        block = _decision_block(181)
        assert "implement-scope-boundary.yaml" in block
        assert "Decision 59" in block
        assert "Decision 163" in block


class TestTriSurfaceStopDispositionIdentity:
    """AC 10: an undeclared touched path has exactly ONE disposition, stated identically in the
    contract's implementing_plan_bookkeeping row, the check module's failure guidance, and the
    deviation trigger's branch (b)."""

    def test_contract_row_carries_disposition(self) -> None:
        doc = _load_contract()
        row = doc["sanction_rows"]["implementing_plan_bookkeeping"]
        assert _STOP_DISPOSITION in row["disposition_on_violation"]

    def test_deviation_trigger_branch_b_carries_same_disposition(self) -> None:
        doc = _load_contract()
        branches = {b["id"]: b for b in doc["deviation_trigger"]["branches"]}
        assert _STOP_DISPOSITION in branches["b"]["description"]

    def test_check_module_failure_guidance_carries_same_disposition(self) -> None:
        assert _check_module._STOP_DISPOSITION == _STOP_DISPOSITION


class TestWeakeningSurfacingContract:
    """PLAN-test-weakening-surfacing (rec-3357, Decision 181 point 2): the two new MECHANICAL
    rows, the narrowed rec-4413 residual, review_dispositions, the plan-schema Literal pin, and
    permitted_field_edits."""

    def test_added_skip_row_is_mechanical(self) -> None:
        doc = _load_contract()
        rows = doc["invariants"]["content"]["enforcement_coverage"]
        row = next(r for r in rows if r.get("enforced_by") == "validate_test_skip_citations")
        assert row["status"] == "mechanical"

    def test_recorded_disposition_row_is_mechanical(self) -> None:
        doc = _load_contract()
        rows = doc["invariants"]["content"]["enforcement_coverage"]
        row = next(r for r in rows if r.get("enforced_by") == "validate_weakening_dispositions")
        assert row["status"] == "mechanical"

    def test_single_residual_row_owned_by_rec_4413(self) -> None:
        doc = _load_contract()
        rows = doc["invariants"]["content"]["enforcement_coverage"]
        residual_rows = [r for r in rows if r.get("status") != "mechanical"]
        assert len(residual_rows) == 1
        residual = residual_rows[0]
        assert residual["residual_owner"] == "rec-4413"
        note = residual["note"]
        assert "DOMINANT" in note
        assert "validate_weakening_candidates" in note
        assert "validate_weakening_dispositions" in note
        assert "test-oracle" in note
        assert "review_dispositions" in note

    def test_review_dispositions_has_exactly_six_keys_with_cite_forms(self) -> None:
        doc = _load_contract()
        review_dispositions = doc["invariants"]["content"]["review_dispositions"]
        assert set(review_dispositions) == {
            "refactor",
            "planned-change",
            "retirement",
            "growth-safe-pin",
            "owned-skip",
            "weakening",
        }
        for key in ("refactor", "planned-change", "retirement", "growth-safe-pin", "owned-skip"):
            assert "cite" in review_dispositions[key].lower()

    def test_permitted_field_edits_includes_weakening_dispositions(self) -> None:
        doc = _load_contract()
        row = doc["sanction_rows"]["implementing_plan_bookkeeping"]
        assert "weakening_dispositions" in row["permitted_field_edits"]

    def test_plan_schema_literal_equals_contract_keys(self) -> None:
        doc = _load_contract()
        contract_keys = set(doc["invariants"]["content"]["review_dispositions"])
        annotation = WeakeningDisposition.model_fields["disposition"].annotation
        literal_values = set(typing.get_args(annotation))
        assert literal_values == contract_keys

    def test_ineligible_cites_rec_4413_and_still_rec_3357(self) -> None:
        doc = _load_contract()
        ineligible = doc["sanction_row_eligibility"]["ineligible"]
        assert "rec-4413" in ineligible
        assert "rec-3357" in ineligible

    def test_exactly_one_amendment_log_entry_names_this_plan(self) -> None:
        doc = _load_contract()
        entries = [e for e in doc["amendment_log"] if "test-weakening-surfacing" in (e.get("summary") or "")]
        assert len(entries) == 1


class TestPlanDefectLoop:
    """PLAN-plan-defect-loop: the escalation, amendment procedure and plan-time declaration clauses."""

    def test_escalation_names_the_report_fields_and_routing(self) -> None:
        escalation = _load_contract()["deviation_trigger"]["escalation"]
        for field in ("plan, branch, failing_check", "evidence", "defect_class", "proposed_amendment"):
            assert field in escalation
        for defect_class in ("scope_companion", "vp_text", "approved_text", "contract", "repeated_failure"):
            assert defect_class in escalation
        assert "never build to planner" in escalation
        assert "Decision 229 cl.2" in escalation

    def test_escalation_keeps_the_always_ask_exceptions(self) -> None:
        escalation = _load_contract()["deviation_trigger"]["escalation"]
        assert "standing direction" in escalation
        assert (
            "an approved_text or contract fix, or any loosened assertion, still needs the human's own approval" in escalation
        )
        assert "nothing is loosened to obtain green" in escalation

    def test_amendment_procedure_names_the_prohibited_field_edits_case_and_commit_subject(self) -> None:
        procedure = _load_contract()["sanction_rows"]["implementing_plan_bookkeeping"]["amendment_procedure"]
        assert "prohibited_field_edits" in procedure
        assert "plan({slug}): scope amendment -- <reason>" in procedure

    def test_hand_written_test_clause_records_the_two_plan_time_declarations(self) -> None:
        ineligible = _load_contract()["sanction_row_eligibility"]["ineligible"]
        assert "tests/test_session_preflight_decomposition.py's frozen_report_key set" in ineligible
        assert "config/structural_size_budgets.yaml" in ineligible
        assert "declared at plan time, never sanctioned" in ineligible

    def test_one_amendment_log_entry_names_this_plan(self) -> None:
        entries = [e for e in _load_contract()["amendment_log"] if "PLAN-plan-defect-loop" in (e.get("summary") or "")]
        assert len(entries) == 1
        assert entries[0]["date"] == "2026-10-31"

    def test_contract_stays_within_the_effective_line_cap(self) -> None:
        lines = [
            ln
            for ln in CONTRACT_PATH.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        assert len(lines) <= 500
