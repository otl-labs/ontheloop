"""PI-1..PI-3 and the WA-2 normaliser through record_turn over synthetic trees (the sessions.py and record_turn.py wiring)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from src.turn_capture.persona_identity import PERSONA_SKILLS, PLUGIN_PREFIX, persona_of, skill_name
from src.turn_capture.record_turn import record_turn
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    assistant,
    child,
    make_files,
    mem_tree,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
)
from tests.fixtures.turn_capture_v11_cases import (
    SCOUT_BODY,
    SCOUT_RECORD,
    SYNC_ARGS,
    SYNC_BODY,
    SYNC_RECORD,
    definition_record,
    persona_subagent_persona_background_files,
)

ROOT = Path(__file__).resolve().parents[2]
HEADER = "Base directory for this skill: /w/.claude/skills/x"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(kid: list[dict[str, Any]], *, files: dict[str, str] | None = None):
    if files is None:
        root = [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {"prompt": "p", "subagent_type": "general-purpose"})]),
            tool_result("r1", 8, "tuA", "done", pid="p1", tur={"agentId": "agP1", "isAsync": False, "status": "completed"}),
            assistant("a2", 9, "m2", [text_block("wrapped up")]),
        ]
        files = make_files(root, {"agP1": [child(r, "agP1") for r in kid]})
    result = record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True)
    return result.agents, result.sessions, result.diagnostics


def skill_call(uid: str, t: float, mid: str, tool_id: str, skill: Any, args: str | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"skill": skill}
    if args is not None:
        spec["args"] = args
    return assistant(uid, t, mid, [tool_use_block(tool_id, "Skill", spec)])


def kid_with(skill: Any, record_text: Any, args: str | None = None) -> list[dict[str, Any]]:
    return [
        prompt("kp1", 2.5, "kq1", "task"),
        skill_call("k1", 3, "mk1", "tuK", skill, args),
        tool_result("kr1", 3.2, "tuK", "Launching skill"),
        definition_record("kd1", 3.3, "kq1", "tuK", record_text),
        assistant("k2", 4, "mk2", [text_block("done")]),
    ]


def types(agents: list[dict[str, Any]]) -> set[str]:
    return {a["agent_type"] for a in agents}


def test_skill_subagent_run_carries_persona_and_definition_hash() -> None:
    kid = kid_with("plan-critique", SYNC_RECORD, SYNC_ARGS)
    agents, _, diag = run(kid)
    assert len(agents) == 2
    for row in agents:
        assert row["agent_type"] == "plan-critique"
        assert row["definition_sha256"] == hashlib.sha256(SYNC_BODY.encode("utf-8")).hexdigest()
    assert diag["persona_run"] >= 1


def test_skill_name_normalises() -> None:
    assert skill_name("/ontheloop:Plan-Critique ") == "plan-critique"
    assert skill_name("ontheloop:ontheloop:x") == "ontheloop:x"
    assert skill_name("other:code-review") == "other:code-review"
    assert skill_name("//x") == "/x"


def test_plugin_prefixed_and_slash_names_map() -> None:
    for name in ("ontheloop:code-review", "/code-review", "/ontheloop:code-review"):
        agents, _, _ = run(kid_with(name, f"{HEADER}\n\nbody"))
        assert types(agents) == {"code-review"}, name
        assert {a["definition_sha256"] for a in agents} == {sha("body")}
    agents, _, _ = run(kid_with("other:code-review", f"{HEADER}\n\nbody"))
    assert types(agents) == {"claude-code-subagent"}


def test_workflow_reads_a_plugin_prefixed_skill_name() -> None:
    _, sessions, _ = run(kid_with("ontheloop:planning", f"{HEADER}\n\nbody"))
    assert {s["workflow"] for s in sessions if s["entity_ref"].endswith("agP1") or s["event_kind"] == "open"} >= {"plan"}


def test_string_content_and_no_args_hash_the_whole_body() -> None:
    agents, _, _ = run(kid_with("code-review", f"{HEADER}\n\nline one\n\nARGUMENTS: not a trailer"))
    assert {a["definition_sha256"] for a in agents} == {sha("line one\n\nARGUMENTS: not a trailer")}
    kid = kid_with("code-review", "x")
    kid[3] = {**kid[3], "message": {"role": "user", "content": f"{HEADER}\n\nplain text"}}
    agents, _, _ = run(kid)
    assert {a["definition_sha256"] for a in agents} == {sha("plain text")}


def test_absent_definition_keeps_the_subagent_key() -> None:
    kid = kid_with("code-review", "x")
    del kid[3]
    agents, _, diag = run(kid)
    assert types(agents) == {"claude-code-subagent"} and all("definition_sha256" not in a for a in agents)
    assert diag["persona_definition_absent"] == 1
    kid = kid_with("code-review", "x")
    kid[3] = {**kid[3], "message": {"role": "user", "content": [text_block(HEADER + "\n\na"), text_block("b")]}}
    agents, _, diag = run(kid)
    assert types(agents) == {"claude-code-subagent"} and all("definition_sha256" not in a for a in agents)
    assert diag["persona_definition_absent"] == 1


def test_unreadable_definition_keeps_the_subagent_key() -> None:
    two = kid_with("code-review", f"{HEADER}\n\na")
    two.insert(4, definition_record("kd2", 3.35, "kq1", "tuK", f"{HEADER}\n\nb"))
    cases = [
        two,
        kid_with("code-review", f"{HEADER}\nno blank line"),
        kid_with("code-review", "Base directory for this skill: p\nX\n\nY"),
        kid_with("code-review", f"{HEADER}\n\nbody", "some args"),
    ]
    for kid in cases:
        agents, _, diag = run(kid)
        assert types(agents) == {"claude-code-subagent"} and all("definition_sha256" not in a for a in agents)
        assert diag["persona_definition_unreadable"] == 1


def test_first_readable_persona_call_wins() -> None:
    kid = [
        prompt("kp1", 2.5, "kq1", "task"),
        skill_call("k1", 3, "mk1", "tu1", "code-review"),
        tool_result("kr1", 3.1, "tu1", "x"),
        skill_call("k2", 3.2, "mk2", "tu2", "plan-critique"),
        tool_result("kr2", 3.3, "tu2", "x"),
        definition_record("kd2", 3.4, "kq1", "tu2", f"{HEADER}\n\ncritic"),
        skill_call("k3", 3.5, "mk3", "tu3", "decision-scout"),
        tool_result("kr3", 3.6, "tu3", "x"),
        definition_record("kd3", 3.7, "kq1", "tu3", f"{HEADER}\n\nscout"),
        assistant("k4", 4, "mk4", [text_block("done")]),
    ]
    agents, _, diag = run(kid)
    assert types(agents) == {"plan-critique"} and {a["definition_sha256"] for a in agents} == {sha("critic")}
    assert diag["persona_definition_absent"] == 1


def test_non_persona_skill_is_ignored() -> None:
    kid = [
        prompt("kp1", 2.5, "kq1", "task"),
        skill_call("k1", 3, "mk1", "tu1", "dataviz"),
        tool_result("kr1", 3.1, "tu1", "x"),
        definition_record("kd1", 3.2, "kq1", "tu1", f"{HEADER}\n\nchart"),
        skill_call("k2", 3.3, "mk2", "tu2", "decision-scout"),
        tool_result("kr2", 3.4, "tu2", "x"),
        definition_record("kd2", 3.5, "kq1", "tu2", SCOUT_RECORD),
        assistant("k3", 4, "mk3", [text_block("done")]),
    ]
    agents, _, _ = run(kid)
    assert types(agents) == {"decision-scout"} and {a["definition_sha256"] for a in agents} == {sha(SCOUT_BODY)}


def test_call_after_the_open_bound_is_not_read() -> None:
    kid = [
        prompt("kp1", 2.5, "kq1", "task"),
        assistant("k1", 3, "mk1", [text_block("first turn")]),
        prompt("kp2", 3.5, "kq2", "next"),
        skill_call("k2", 4, "mk2", "tu1", "decision-scout"),
        tool_result("kr1", 4.1, "tu1", "x"),
        definition_record("kd1", 4.2, "kq2", "tu1", SCOUT_RECORD),
        assistant("k3", 5, "mk3", [text_block("done")]),
    ]
    agents, _, _ = run(kid)
    assert types(agents) == {"claude-code-subagent"}


def test_background_run_carries_the_persona_on_its_notice_close() -> None:
    agents, _, _ = run([], files=persona_subagent_persona_background_files())
    assert [a["event_kind"] for a in agents] == ["open", "close"]
    assert types(agents) == {"decision-scout"} and {a["definition_sha256"] for a in agents} == {sha(SCOUT_BODY)}


def test_a_non_string_skill_input_is_ignored() -> None:
    kid = [
        prompt("kp1", 2.5, "kq1", "task"),
        skill_call("k1", 3, "mk1", "tu1", ["code-review"]),
        tool_result("kr1", 3.1, "tu1", "x"),
        skill_call("k2", 3.2, "mk2", "tu2", "decision-scout"),
        tool_result("kr2", 3.3, "tu2", "x"),
        definition_record("kd2", 3.4, "kq1", "tu2", SCOUT_RECORD),
        assistant("k3", 4, "mk3", [text_block("done")]),
    ]
    agents, _, diag = run(kid)
    assert types(agents) == {"decision-scout"} and diag.get("persona_definition_absent", 0) == 0


def test_no_open_bound_reads_no_persona() -> None:
    from collections import Counter  # noqa: PLC0415

    diag: Counter[str] = Counter()
    assert persona_of(None, None, diag) is None and not diag  # type: ignore[arg-type]


def test_persona_skills_equal_the_verdict_personas_rows() -> None:
    contract = yaml.safe_load((ROOT / "docs/contracts/executor-personas.yaml").read_text(encoding="utf-8"))
    want = {}
    for row in contract["personas"]:
        if row["kind"] != "verdict":
            continue
        tether = yaml.safe_load((ROOT / row["contract"]).read_text(encoding="utf-8"))["skill_tether"]["skill"]
        want[tether] = row["registry_key"]
    assert PERSONA_SKILLS == want
    plugin = json.loads((ROOT / ".claude/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert PLUGIN_PREFIX == plugin["name"] + ":"
    for skill in want:
        body = (ROOT / ".claude/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "$ARGUMENTS" not in body and "${" not in body, skill
