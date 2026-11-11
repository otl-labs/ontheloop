"""rec-5050 acceptance: the decision_scout, plan_critic and code_reviewer verdict contracts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

INDEX = "docs/contracts/executor-personas.yaml"
ROADMAP = "docs/ROADMAP-PLATFORM.yaml"

# persona -> (contract path, rendered prompt path, tethered skill path, ADD criterion, verdict enum)
PERSONAS = {
    "decision_scout": (
        "docs/contracts/personas/decision_scout.yaml",
        "docs/contracts/personas/rendered/decision_scout.txt",
        ".claude/skills/decision-scout/SKILL.md",
        "T4.30:c3",
        ["NO_FLAGS", "FLAGS_FOUND", "BLOCK"],
    ),
    "plan_critic": (
        "docs/contracts/personas/plan_critic.yaml",
        "docs/contracts/personas/rendered/plan_critic.txt",
        ".claude/skills/plan-critique/SKILL.md",
        "T4.29:c3",
        ["PROCEED", "REVISE"],
    ),
    "code_reviewer": (
        "docs/contracts/personas/code_reviewer.yaml",
        "docs/contracts/personas/rendered/code_reviewer.txt",
        ".claude/skills/code-review/SKILL.md",
        "T4.28:c4",
        ["PROCEED", "REVISE"],
    ),
}

CONTROL_INPUTS = {"change_id", "head_sha"}

_SKILL_ID = re.compile(r"^(\*\*)?([0-9]+[a-z]?(-[0-9]+)?)\.")


def _load(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _skill_step_ids(persona: str) -> dict[str, str]:
    """Skill step id -> the bracketed marker its template must carry."""
    lines = (ROOT / PERSONAS[persona][2]).read_text(encoding="utf-8").splitlines()
    ids: dict[str, str] = {}
    if persona in ("decision_scout", "plan_critic"):
        for line in lines:
            m = _SKILL_ID.match(line)
            if m:
                ids[m.group(2)] = f"[step {m.group(2)}]"
        return ids
    section = ""
    for line in lines:
        if line.startswith("## "):
            section = line
        m = re.match(r"^## Step ([0-9]+[a-z]?):", line)
        if m:
            ids[m.group(1)] = f"[step {m.group(1)}]"
        m = re.match(r"^([1-9])\. ", line)
        if m and section == "## Review Process":
            ids[m.group(1)] = f"[step {m.group(1)}]"
        m = re.match(r"^### ([0-9]+)\. ", line)
        if m:
            ids[f"dimension {m.group(1)}"] = f"[dimension {m.group(1)},"
        if line.startswith("## V3 Post-Deploy Evidence"):
            ids["v3 evidence"] = "[v3 evidence]"
    return ids


_OMITTED = "omitted: retired (Decision 247)"
STEP_MAP = {
    "decision_scout": dict.fromkeys(["0", "1", "2", "3", "4", "5", "6", "7", "8", "8b", "9", "10"], "template"),
    "plan_critic": {
        **dict.fromkeys(
            ["1", "2", "3", "4", "5", "5b", "6", "7", "8", "9", "10", "11", "12", "12b", "12c", "12c-1"]
            + ["12e", "12f", "12g", "12h", "12i", "12j", "12k", "12l", "12m", "12n", "12o", "12p", "13", "14"],
            "template",
        ),
        "12d": _OMITTED,
    },
    "code_reviewer": {
        **dict.fromkeys(["0", "0b", "1", "2", "3", "4", "5", "6", "v3 evidence"], "template"),
        **{f"dimension {n}": "template" for n in range(1, 12)},
    },
}


def test_three_verdict_personas_registered_pending() -> None:
    rows = {r["persona"]: r for r in _load(INDEX)["personas"]}
    for persona, (contract, rendered, _skill, add, _enum) in PERSONAS.items():
        row = rows[persona]
        assert row["kind"] == "verdict"
        assert row["contract"] == contract
        assert row["rendered_prompt"] == rendered
        assert row["implementation"] == {"status": "pending", "owning_item": add}


def test_verdict_enum_and_tether() -> None:
    for persona, (contract, _r, skill, _add, enum) in PERSONAS.items():
        c = _load(contract)
        assert c["kind"] == "verdict"
        assert c["output_schema"]["properties"]["verdict"]["enum"] == enum
        tether = c["skill_tether"]
        assert tether["skill"] == Path(skill).parent.name
        line = tether["verdict_line"]
        assert line in (ROOT / skill).read_text(encoding="utf-8")
        tokens = set(re.findall(r"[A-Z][A-Z_]+", line.partition(":")[2]))
        assert tokens == set(enum)


def test_capabilities_are_read_input_over_every_input_only() -> None:
    for persona, (contract, *_rest) in PERSONAS.items():
        c = _load(contract)
        inputs = set(c["input_schema"]["properties"]) - CONTROL_INPUTS
        assert set(c["capabilities"]) == {f"read_input:{i}" for i in inputs}, persona
        assert len(c["capabilities"]) == len(inputs)


def test_input_roles_and_failure_ids() -> None:
    expected_inputs = {
        "decision_scout": {"plan", "approved_text", "north_star", "decisions_index", "decisions", "tag_shortlist"}
        | {"north_star_carriers", "roadmap_projection"},
        "plan_critic": {"plan", "approved_text", "approved_text_record", "scope_files", "project_context", "north_star"}
        | {"decisions", "roadmap_items", "plan_obligations", "affected_artifacts", "governance_contracts"},
        "code_reviewer": {"plan", "change_files", "diff", "review_scope", "review_scope_files", "project_context"}
        | {"review_exemptions", "review_dispositions", "weakening_candidates", "ci_results", "change_evidence"}
        | {"review_guidance"},
    }
    common = {"budget_exhausted", "schema_invalid", "input_incomplete", "injection_suspected"}
    for persona, (contract, *_rest) in PERSONAS.items():
        c = _load(contract)
        assert set(c["input_schema"]["properties"]) - CONTROL_INPUTS == expected_inputs[persona]
        ids = {f["id"] for f in c["failure_outputs"]}
        assert common <= ids
    scout = {f["id"] for f in _load(PERSONAS["decision_scout"][0])["failure_outputs"]}
    assert {"input_unusable", "triage_incomplete"} <= scout


def test_budget_provisional_until_add_criterion() -> None:
    for persona, (contract, _r, _s, add, _e) in PERSONAS.items():
        b = _load(contract)["budget"]
        assert b["provisional"]["until"] == add
        assert b["max_revisions"] == 2


def test_step_id_map_matches_skill_and_template() -> None:
    for persona, (contract, *_rest) in PERSONAS.items():
        ids = _skill_step_ids(persona)
        assert set(ids) == set(STEP_MAP[persona]), (persona, set(ids) ^ set(STEP_MAP[persona]))
        template = _load(contract)["prompt_template"]
        for step_id, disposition in STEP_MAP[persona].items():
            if disposition == "template":
                assert ids[step_id] in template, (persona, step_id)
            else:
                assert disposition.startswith("omitted: ")


def test_scout_roadmap_id_pattern_matches_every_roadmap_item() -> None:
    out = _load(PERSONAS["decision_scout"][0])["output_schema"]["properties"]["roadmap"]
    pattern = None

    def walk(node: object) -> None:
        nonlocal pattern
        if isinstance(node, dict):
            p = node.get("pattern")
            if isinstance(p, str) and p.startswith("^roadmap:"):
                pattern = p
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(out)
    assert pattern, "roadmap row id pattern not found"
    rx = re.compile(pattern)
    items = _load(ROADMAP)["tier_items"]
    assert items
    for item in items:
        assert rx.match(f"roadmap:{item['id']}"), item["id"]


def test_scout_report_template_unchanged() -> None:
    text = (ROOT / PERSONAS["decision_scout"][2]).read_text(encoding="utf-8")
    assert re.search(r"### Verdict\s*\n+\s*NO_FLAGS \| FLAGS_FOUND \| BLOCK", text)
