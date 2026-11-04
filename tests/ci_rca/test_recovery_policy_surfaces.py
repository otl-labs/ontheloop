"""Decision 226: the Decision entry, the red-main-recovery contract and every prose surface that stated the old halt.

Pins read the shipped files; the verbatim Decision text is checked at code review against the approved draft.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "contracts" / "red-main-recovery.yaml"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _yaml(rel: str) -> dict:
    return yaml.safe_load(_read(rel))


def _entry(number: int) -> str:
    text = _read("docs/DECISIONS.md")
    match = re.search(rf"^## Decision {number}:.*?(?=^---$)", text, re.M | re.S)
    assert match, f"Decision {number} entry missing"
    return match.group(0)


class TestDecisionEntry:
    def test_header_and_clause_labels(self) -> None:
        entry = _entry(226)
        assert entry.startswith("## Decision 226: Red-main recovery is priced by repair track")
        for label in (
            "1. **Two repair tracks.**",
            "2. **Fast lane for A**",
            "3. **The halt narrows to overlap**",
            "4. **Repair SLO.**",
            "5. **A preventive only for a recurring class.**",
        ):
            assert label in entry
        assert "amends: [72, 73, 217]" in entry

    def test_amended_decisions_carry_the_annotation(self) -> None:
        for number in (72, 73, 217):
            assert re.search(r"^> \*\*Amended by Decision 226 \(\d{4}-\d{2}-\d{2}\):\*\*", _entry(number), re.M), number

    def test_slo_hours_match_the_contract(self) -> None:
        entry = _entry(226)
        hours = _yaml("docs/contracts/red-main-recovery.yaml")["recovery_policy"]["slo_hours"]
        assert re.search(rf"within {hours} hours", entry)

    def test_coverage_line_names_residuals(self) -> None:
        entry = _entry(226)
        assert "likely-resolved split" in entry and "Step 8's logged deferral" in entry
        for rec in ("rec-4441", "rec-4442", "rec-4444"):
            assert rec in entry

    def test_clause_wording_matches_the_contract_predicate(self) -> None:
        entry = _entry(226)
        assert "code accepts it only" in entry and "with no regression" in entry
        assert "**Fast lane for A**" in entry

    def test_entry_stays_under_the_size_cap(self) -> None:
        assert authored_bytes(decision_entry(_read("docs/DECISIONS.md"), 226)) <= cap_bytes()


class TestContractPolicy:
    def test_contract_is_a_ratified_class_d_naming_its_surface(self) -> None:
        contract = _yaml("docs/contracts/red-main-recovery.yaml")["contract"]
        assert (contract["class"], contract["status"]) == ("D", "ratified")
        surface = contract["evaluator"]["agent_surface"]
        assert (ROOT / surface).is_file()

    def test_policy_values_match_the_code_constants(self) -> None:
        from scripts.ops_portal import closure_gate

        policy = _yaml("docs/contracts/red-main-recovery.yaml")["recovery_policy"]
        assert policy["decision"] == 226
        assert isinstance(policy["slo_hours"], int) and policy["slo_hours"] > 0
        assert policy["test_path_prefix"] == closure_gate._TEST_PATH_PREFIX
        assert tuple(policy["bookkeeping_prefixes"]) == closure_gate._BOOKKEEPING_PREFIXES
        assert policy["preventive"]["window_days"] == 90 and policy["preventive"]["threshold"] == 2

    def test_halt_rules_in_order(self) -> None:
        rules = _yaml("docs/contracts/red-main-recovery.yaml")["recovery_policy"]["halt"]["rules"]
        assert [r.split(" ", 1)[0] for r in rules] == ["0", "1", "2", "3", "4", "5", "6"]

    def test_coverage_selectors_exist_and_residuals_are_named(self) -> None:
        coverage = _yaml("docs/contracts/red-main-recovery.yaml")["recovery_policy"]["coverage"]
        for key in ("cl1_track_and_priority", "cl3_cl4_verdicts"):
            for selector in coverage[key]:
                path = ROOT / selector.split("::")[0]
                assert path.is_file(), selector
                for part in selector.split("::")[1:]:
                    assert part in path.read_text(encoding="utf-8"), selector
        assert sorted(coverage["residuals"].values()) == ["rec-4441", "rec-4442", "rec-4444"]

    def test_lifecycle_projection_fields_and_disposition(self) -> None:
        lifecycle = _yaml("docs/contracts/ci-rca-lifecycle.yaml")
        fields = lifecycle["projection_fields"]
        assert fields["repair_track"]["authored_by"] == "agent"
        assert fields["repair_track_confirmed"]["authored_by"].startswith("portal")
        assert fields["workflow_tier"]["authored_by"].startswith("portal")
        assert "red-main-recovery.yaml" in lifecycle["trigger_scope"]["default_branch_disposition"]

    def test_fast_lane_procedure(self) -> None:
        proc = _yaml("docs/contracts/red-main-recovery.yaml")["procedure"]["fast_lane_repair"]
        assert "Resolves: rec-NNNN" in proc and "closure_artifact" in proc
        assert "handoff_sequence" in proc and "--verify-head" not in proc

    def test_priority_derivation_declared_unrealized(self) -> None:
        derivation = _yaml("docs/contracts/ops_recommendations.yaml")["fields"]["priority"]["derivation"]
        note = derivation["source_rules"]["ci_rca"]
        assert "derive_ci_rca_priority" in note and "Decision 226" in note
        assert derivation["realized"] is False

    def test_scope_boundary_note_and_single_residual(self) -> None:
        text = _read("docs/contracts/implement-scope-boundary.yaml")
        assert "fast-lane repair PRs" in text and "rec-4442" in text
        assert text.count("status: residual_agent_obligation") >= 1
        assert text.count("This is the SOLE residual row in this map.") == 1

    def test_file_router_routes_the_contract(self) -> None:
        routes = _yaml("docs/contracts/file-router.yaml")
        entry = next(r for r in routes["routes"] if r["topic"] == "red-main-recovery")
        assert "docs/contracts/red-main-recovery.yaml" in entry["targets"] and "Decision 226" in entry["description"]


class TestProseSurfacesPointAtTheCheck:
    def test_surfaces_name_the_check_or_the_decision(self) -> None:
        for rel in (
            "AGENTS.md",
            ".claude/skills/planning/SKILL.md",
            ".claude/skills/overseer/SKILL.md",
            ".claude/commands/overseer.md",
            ".claude/commands/orient.md",
        ):
            text = _read(rel)
            assert "red_overlap" in text or "Decision 226" in text, rel
        assert "red_overlap" in _read("docs/contracts/overseer-dispatch.yaml")

    def test_orient_skill_routes_per_command_row(self) -> None:
        skill = _read(".claude/skills/orient/SKILL.md")
        assert "they block overlapping work" in skill and "Route each per the command's Section 2 row" in skill
        assert "they block other work" not in skill and "suggest a `/plan` prompt to resolve it" not in skill
        assert "If a HARD BLOCK Critical rec exists, prepend a zero-th prompt:" in skill
        assert "fast_lane_repair" in _read(".claude/commands/orient.md")

    def test_old_halt_phrases_are_gone(self) -> None:
        stale = (
            "/plan cannot scope unrelated work",
            "an open critical rec, or a null list",
            "halt-on-open-ci_rca is a hard dispatch block",
            "before dispatching any new slice",
        )
        for rel in (
            ".claude/skills/planning/SKILL.md",
            ".claude/skills/overseer/SKILL.md",
            ".claude/commands/overseer.md",
            ".claude/commands/orient.md",
            ".claude/skills/orient/SKILL.md",
            "AGENTS.md",
        ):
            text = _read(rel)
            for phrase in stale:
                assert phrase not in text, (rel, phrase)

    def test_source_registry_no_longer_blocks_new_feature_work(self) -> None:
        text = _read("config/agent/data_quality/source_registry.yaml")
        assert "block new feature work until reviewed" not in text
        assert "scripts.ci_rca.red_overlap" in text

    def test_agents_md_keeps_the_pinned_literals(self) -> None:
        text = _read("AGENTS.md")
        for literal in ("forward-fix, never auto-revert", "source=ci_rca", "priority=critical"):
            assert literal in text

    def test_planning_step_8_runs_the_evaluator(self) -> None:
        text = _read(".claude/skills/planning/SKILL.md")
        assert "## Related-Work Check (Workflow Step 8)" in text
        assert "scripts.ci_rca.red_overlap --plan" in text and "docs/contracts/red-main-recovery.yaml" in text
