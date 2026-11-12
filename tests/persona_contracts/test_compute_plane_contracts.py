"""T4.10a c1 and c5 acceptance: the plan_agent and implement_agent compute-plane contracts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

INDEX = "docs/contracts/executor-personas.yaml"
PLAN_COMMAND = ".claude/commands/plan.md"
IMPLEMENT_COMMAND = ".claude/commands/implement.md"
CONTROL_INPUTS = {"change_id", "head_sha"}

# persona -> (contract, rendered prompt, command, owning criterion, registry key, status enum, budget turns)
PERSONAS = {
    "plan_agent": (
        "docs/contracts/personas/plan_agent.yaml",
        "docs/contracts/personas/rendered/plan_agent.txt",
        PLAN_COMMAND,
        "T4.31:c2",
        "planning",
        ["drafted", "decomposed", "escalated"],
        150,
        "T4.31:c1",
    ),
    "implement_agent": (
        "docs/contracts/personas/implement_agent.yaml",
        "docs/contracts/personas/rendered/implement_agent.txt",
        IMPLEMENT_COMMAND,
        "T4.15:c14",
        "implement-agent",
        ["implemented", "continuation", "escalated"],
        200,
        "T4.15:c6",
    ),
}

_TEMPLATE = "template"
_COORD = "omitted: coordinator"
STEP_MAP = {
    "plan_agent": {
        **dict.fromkeys(["2", "4", "5", "8"], _TEMPLATE),
        **dict.fromkeys(["1", "3", "6", "7", "9", "10", "11", "12"], _COORD),
    },
    "implement_agent": {
        **dict.fromkeys(["2", "3"], _TEMPLATE),
        **dict.fromkeys(["1", "4", "5", "6", "7", "8", "9"], _COORD),
    },
}


def _load(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _command_step_ids(rel: str) -> set[str]:
    text = (ROOT / rel).read_text(encoding="utf-8")
    return set(re.findall(r"^## Step ([0-9]+[a-z]?):", text, flags=re.MULTILINE))


def _template(persona: str) -> str:
    return " ".join(_load(PERSONAS[persona][0])["prompt_template"].split())


def test_two_compute_plane_personas_registered_pending() -> None:
    rows = {r["persona"]: r for r in _load(INDEX)["personas"]}
    for persona, (contract, rendered, _cmd, owning, key, *_rest) in PERSONAS.items():
        row = rows[persona]
        assert row["kind"] == "compute_plane"
        assert row["contract"] == contract
        assert row["rendered_prompt"] == rendered
        assert row["implementation"] == {"status": "pending", "owning_item": owning}
        assert row["registry_key"] == key


def test_capabilities_and_input_roles() -> None:
    for persona, (contract, *_rest) in PERSONAS.items():
        c = _load(contract)
        assert c["kind"] == "compute_plane"
        inputs = set(c["input_schema"]["properties"]) - CONTROL_INPUTS
        extra = {"read_repo", "edit_repo", "run_tests"} if persona == "implement_agent" else {"read_repo"}
        assert set(c["capabilities"]) == extra | {f"read_input:{i}" for i in inputs}, persona
    impl_inputs = set(_load(PERSONAS["implement_agent"][0])["input_schema"]["properties"])
    assert impl_inputs - CONTROL_INPUTS == {
        "plan",
        "approved_text",
        "work_items",
        "review_findings",
        "ci_results",
        "continuation",
        "rec_write_guidance",
    }
    assert "verification" not in impl_inputs
    plan_inputs = set(_load(PERSONAS["plan_agent"][0])["input_schema"]["properties"])
    assert plan_inputs - CONTROL_INPUTS == {
        "work_item",
        "related_work_items",
        "unbuilt_plans",
        "operator_direction",
        "revision_request",
        "rec_write_guidance",
    }


def test_status_enum_failure_ids_and_budget() -> None:
    common = {"budget_exhausted", "schema_invalid", "input_incomplete", "injection_suspected"}
    for persona, (contract, _r, _cmd, _o, _k, enum, turns, until) in PERSONAS.items():
        c = _load(contract)
        assert c["output_schema"]["properties"]["status"]["enum"] == enum
        ids = {f["id"] for f in c["failure_outputs"]}
        assert common | {"input_unusable", "workspace_mismatch"} <= ids
        assert c["budget"]["max_turns"] == turns
        assert c["budget"]["max_wall_clock_seconds"] == 3600
        assert c["budget"]["max_revisions"] == 2
        assert c["budget"]["provisional"]["until"] == until


def test_step_disposition_maps_cover_command_steps() -> None:
    for persona, (contract, _r, command, *_rest) in PERSONAS.items():
        assert set(STEP_MAP[persona]) == _command_step_ids(command), f"{command} gained or lost a step"
        template = _template(persona)
        for step, disposition in STEP_MAP[persona].items():
            if disposition == _TEMPLATE:
                assert f"[step {step}]" in template, (persona, step)
            else:
                assert disposition.startswith("omitted: "), (persona, step)


def test_leash_sentences() -> None:
    impl = _template("implement_agent")
    assert "Never weaken a check to obtain green" in impl
    assert "Never run the full validation" in impl
    assert "record it in left_to_coordinator" in impl
    required = _load(PERSONAS["implement_agent"][0])["output_schema"]["required"]
    assert "left_to_coordinator" in required
    assert "change no file and file nothing" in _template("plan_agent")
