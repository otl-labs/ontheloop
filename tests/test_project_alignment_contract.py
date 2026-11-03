"""Content contract for PLAN-project-alignment: /plan Step 6 is named "project alignment" and defined once in
docs/contracts/project-alignment.yaml; the retired Step 6a/6b labels and the step-6b-confirmation gate value are gone from
every live surface (a hard cut), and the roadmap carries the dated sequencing brackets.

The stale-reference sweep runs over whitespace-normalized text with amendment_log rows stripped (history legitimately
names the retired labels). Its negative case proves the sweep is not vacuous.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "project-alignment.yaml"
CONTRACT_REF = "docs/contracts/project-alignment.yaml"
PLAN_MD = REPO_ROOT / ".claude" / "commands" / "plan.md"
PLANNING_SKILL = REPO_ROOT / ".claude" / "skills" / "planning" / "SKILL.md"
OVERSEER_SKILL = REPO_ROOT / ".claude" / "skills" / "overseer" / "SKILL.md"
DISPATCH = REPO_ROOT / "docs" / "contracts" / "overseer-dispatch.yaml"
ROUTER = REPO_ROOT / "docs" / "contracts" / "file-router.yaml"
ROADMAP = REPO_ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
ENTRIES = REPO_ROOT / "config" / "agent" / "verification_registry" / "entries"

_RETIRED_GATE = "step-6b-confirmation"
_RETIRED_LABEL = re.compile(r"Steps?\s+(?:\d+/)*6[ab]\b")
_RETIRED_HEADINGS = (
    "Confirmation Gate (Workflow",
    "Present and Confirm",
    "Present Findings and Confirm",
    "Workflow Step 6a",
    "Workflow Step 6b",
)
_SWEEP_ROOTS = (".claude", "docs/contracts", "scripts", "tests", "config")
_SWEEP_SUFFIXES = {".md", ".yaml", ".yml", ".py", ".json", ".toml", ".sh"}
_SWEEP_EXCLUDED = {
    "docs/contracts/project-alignment.yaml",
    "tests/test_project_alignment_contract.py",
}
_AMENDMENT_LOG = re.compile(r"^amendment_log:[ \t]*\n(?:(?:[ \t].*)?\n)*", re.MULTILINE)


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", _AMENDMENT_LOG.sub("", text))


def stale_hits(text: str) -> list[str]:
    flat = _normalized(text)
    hits = [_RETIRED_GATE] if _RETIRED_GATE in flat else []
    hits += [m.group(0) for m in _RETIRED_LABEL.finditer(flat)]
    hits += [h for h in _RETIRED_HEADINGS if h in flat]
    return hits


def _sweep_files() -> list[Path]:
    files: list[Path] = []
    for root in _SWEEP_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if (
                path.is_file()
                and path.suffix in _SWEEP_SUFFIXES
                and "__pycache__" not in path.parts
                and rel not in _SWEEP_EXCLUDED
            ):
                files.append(path)
    return files


def _tier_items() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in _load(ROADMAP)["tier_items"]}


def _criterion(item: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(c for c in item["exit_criteria"] if c["id"] == cid)


class TestContractShape:
    def test_envelope_is_ratified_class_d_with_the_plan_command_as_evaluator(self) -> None:
        contract = _load(CONTRACT_PATH)["contract"]
        assert contract["class"] == "D"
        assert contract["status"] == "ratified"
        assert "PLAN-project-alignment" in contract["ratified_via"]
        assert contract["subject"] == "project-alignment"
        assert contract["evaluator"] == {"agent_surface": ".claude/commands/plan.md"}
        assert CONTRACT_REF in PLAN_MD.read_text(encoding="utf-8")

    def test_two_ordered_tasks(self) -> None:
        tasks = _load(CONTRACT_PATH)["tasks"]
        assert [(t["id"], t["order"]) for t in tasks] == [("decision_scout_recon", 1), ("alignment_sign_off", 2)]

    def test_three_carriers_with_the_pr_flow_pointer_only(self) -> None:
        doc = _load(CONTRACT_PATH)
        carriers = doc["carriers"]
        assert set(carriers) == {"in_session", "overseer_relay", "pr_flow"}
        assert carriers["pr_flow"]["status"] == "future"
        pointer = " ".join(str(carriers["pr_flow"]["mechanism"]).split())
        for token in ("T4.30 c5", "T4.29 c5", "Decision 205 clause 1", "Decision 229 clause 4"):
            assert token in pointer
        assert "gate value is project-alignment" in " ".join(str(carriers["overseer_relay"]["mechanism"]).split())

    def test_distinct_from_the_scouts_intent_alignment_lane(self) -> None:
        distinct = " ".join(str(_load(CONTRACT_PATH)["definition"]["distinct_from"]).split())
        assert "Decision 152" in distinct and "intent-alignment" in distinct

    def test_autonomy_points_at_the_fork_classification(self) -> None:
        autonomy = _load(CONTRACT_PATH)["autonomy"]
        assert "autonomy_tiers.plan_fork_classification" in autonomy["pointer"]
        assert "PLAN-step-6b-fork-classification" in autonomy["provenance"]
        frame = " ".join(str(autonomy["frame"]).split())
        assert "Decision 211" in frame and "Decision 235" in frame

    def test_legacy_labels_are_documentation_only(self) -> None:
        legacy = _load(CONTRACT_PATH)["legacy_labels"]
        assert legacy["status"] == "documentation_only"
        assert [row["retired"] for row in legacy["map"]] == ["Step 6a", "Step 6b", _RETIRED_GATE]


class TestRouting:
    def test_one_route_whose_topic_is_the_subject(self) -> None:
        routes = [r for r in _load(ROUTER)["routes"] if r["topic"] == "project-alignment"]
        assert len(routes) == 1  # count-coupling-ok: uniqueness of one topic, not a collection size
        assert routes[0]["targets"] == [CONTRACT_REF]

    def test_both_registry_rows_exist(self) -> None:
        for check_id in ("router-route-project-alignment", "project-alignment-contract-shape"):
            row = _load(ENTRIES / f"{check_id}.yaml")
            assert row["check_id"] == check_id
            assert row["plan_slug"] == "project-alignment"


class TestCarrierSurfaces:
    @pytest.mark.parametrize("path", [PLAN_MD, PLANNING_SKILL, OVERSEER_SKILL])
    def test_relay_surface_carries_the_carrier_literal(self, path: Path) -> None:
        assert "gate: project-alignment" in _normalized(path.read_text(encoding="utf-8"))

    def test_dispatch_contract_gate_enumeration_and_amendment_row(self) -> None:
        doc = _load(DISPATCH)
        trampoline = doc["gate_request_trampoline"]
        assert "project-alignment" in trampoline["request_schema"]
        assert any(s.get("gate") == "project-alignment" for s in doc["trampoline_sequence"]["stages"])
        applies_to = " ".join(str(doc["autonomy_tiers"]["plan_fork_classification"]["applies_to"]).split())
        assert CONTRACT_REF in applies_to
        rows = [r for r in doc["amendment_log"] if "PLAN-project-alignment" in str(r.get("summary"))]
        assert len(rows) == 1  # count-coupling-ok: exactly one row for this plan
        assert rows[0]["change_class"] == "accepted_values_narrow" and rows[0]["semantic_break"] is True
        assert _RETIRED_GATE in rows[0]["summary"] and "project-alignment" in rows[0]["summary"]


class TestRoadmap:
    def test_t4_2_intent_names_the_decision_scout_gate_not_step_6a(self) -> None:
        intent = _tier_items()["T4.2"]["intent"]
        assert "Decision Scout Gate (project alignment)" in intent
        assert not _RETIRED_LABEL.search(" ".join(intent.split()))

    def test_t4_30_c5_bracket_supersedes_the_after_step_6b_ordering(self) -> None:
        c5 = _criterion(_tier_items()["T4.30"], "c5")
        text = " ".join(c5["text"].split())
        assert c5["status"] == "open"
        assert "/plan opens a draft plan PR after Step 6b" in text
        bracket = text[text.index("[Amendment 2026-10-24, PLAN-project-alignment") :]
        for token in ("supersedes the original 'after Step 6b' ordering", "ready_for_review", "draft plan PR"):
            assert token in bracket

    def test_t4_29_c5_bracket_marks_ready_on_the_sign_off(self) -> None:
        c5 = _criterion(_tier_items()["T4.29"], "c5")
        text = " ".join(c5["text"].split())
        assert c5["status"] == "open"
        bracket = text[text.index("[Amendment 2026-10-24, PLAN-project-alignment") :]
        assert "only on the operator's alignment sign-off" in bracket


class TestRegistryShardRepair:
    def test_plan_md_glosses_split_outside_the_verbatim_menu(self) -> None:
        flat = " ".join(PLAN_MD.read_text(encoding="utf-8").split())
        menu = "offers accept-with-deferral / re-scope / split / abandon / one more round"
        assert menu in flat
        assert "; split means split the plan per Step 8's Planning-time split" in flat
        assert "split the plan" in flat.replace(menu, "")

    def test_shard_message_names_step_6_and_keeps_both_assertions(self) -> None:
        shard = _load(ENTRIES / "plan-obligations-command-wired.yaml")
        script = shard["check_spec"]["command"][2]
        assert "Step 6 (alignment sign-off) does not invoke the obligation query" in script
        assert "Steps 4/" not in script
        assert 'assert "scripts.roadmap.plan_obligations" in t' in script
        assert 'assert "split the plan" in t.lower()' in script

    def test_verification_registry_records_the_in_place_edit(self) -> None:
        doc = _load(REPO_ROOT / "docs" / "contracts" / "verification-registry.yaml")
        assert "plan-obligations-command-wired" in doc["governance_notes"]
        assert "PLAN-project-alignment" in doc["governance_notes"]
        newest = doc["amendment_log"][0]
        assert "PLAN-project-alignment" in newest["summary"] and newest["semantic_break"] is False


class TestStaleReferenceSweep:
    def test_no_live_surface_carries_a_retired_label(self) -> None:
        offenders = {
            path.relative_to(REPO_ROOT).as_posix(): hits
            for path in _sweep_files()
            if (hits := stale_hits(path.read_text(encoding="utf-8", errors="replace")))
        }
        assert not offenders, f"retired project-alignment labels still live: {offenders}"

    def test_sweep_is_not_vacuous(self) -> None:
        assert stale_hits("rides gate: step-6b-confirmation") == [_RETIRED_GATE]
        assert stale_hits("at Step\n6b") == ["Step 6b"]
        assert stale_hits("Steps 4/6b") == ["Steps 4/6b"]
        assert stale_hits("## Present and Confirm") == ["Present and Confirm"]

    def test_sweep_strips_only_amendment_log_rows(self) -> None:
        text = 'amendment_log:\n  - {summary: "step-6b-confirmation"}\n\nkey: value\n'
        assert stale_hits(text) == []
        assert stale_hits(text + "other: step-6b-confirmation\n") == [_RETIRED_GATE]

    def test_sweep_covers_the_known_surfaces(self) -> None:
        rels = {p.relative_to(REPO_ROOT).as_posix() for p in _sweep_files()}
        assert {
            ".claude/commands/plan.md",
            ".claude/skills/overseer/SKILL.md",
            "docs/contracts/overseer-dispatch.yaml",
            "tests/test_plan_fork_classification_contract.py",
        } <= rels
        assert not rels & _SWEEP_EXCLUDED
