"""Tests for the derived-at-read telemetry_persona_findings relation (rec-4434, plan persona-findings-contract)."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.contracts import load_contract
from scripts.persona_contracts import ROOT, load_index
from scripts.persona_contracts import load_contract as load_persona_contract
from scripts.persona_contracts.findings import (
    BLOCK_MARKER,
    LISTS,
    PROJECTION_VERSION,
    FindingRow,
    RunIdentity,
    project_result,
    projected_schema,
)

FIXTURES = Path(__file__).parent / "fixtures" / "persona_findings"
CONTRACT = ROOT / "docs" / "contracts" / "telemetry_persona_findings.yaml"
SKILL = ROOT / ".claude" / "skills" / "code-review" / "SKILL.md"
SHA = "1" * 40


def _identity(agent_type: str = "code-review") -> RunIdentity:
    return RunIdentity("t1", "p1", "run-1", agent_type, "a" * 64, "in_session")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _line(obj: Any) -> str:
    return BLOCK_MARKER + json.dumps(obj)


def _schema(persona_id: str) -> dict[str, Any]:
    row = next(r for r in load_index().personas if r.persona == persona_id)
    return load_persona_contract(ROOT / row.contract).output_schema


def test_code_review_findings_block_yields_one_row_per_finding() -> None:
    rows = project_result(_identity(), text=_fixture("code_review_two_findings.txt"), source_event_id="ev-1")
    assert [r.finding_ordinal for r in rows] == [0, 1, 2]
    assert [r.kind for r in rows] == ["verdict", "finding", "finding"]
    assert {r.verdict for r in rows} == {"REVISE"}
    assert [r.severity for r in rows] == [None, "High", "Low"]
    assert [r.origin for r in rows] == [None, "judgement", "mechanical"]
    assert [r.anchor for r in rows] == [None, "src/x.py:12", "src/y.py:3"]
    assert [r.finding_id for r in rows] == [None, "src/x.py:12:testing", "src/y.py:3:documentation"]
    for r in rows:
        assert (r.tenant_id, r.project_id, r.agent_run_id, r.agent_type) == ("t1", "p1", "run-1", "code-review")
        assert (r.definition_sha256, r.lane) == ("a" * 64, "in_session")
        assert (r.subject_sha, r.subject_dirty) == (SHA, True)
        assert (r.parse_status, r.projection_version, r.source_event_id) == ("parsed", 2, "ev-1")
        assert "Two issues" not in repr(r)


def test_a_run_without_findings_keeps_its_verdict_row() -> None:
    rows = project_result(_identity(), text=_fixture("code_review_no_findings.txt"))
    assert len(rows) == 1
    assert (rows[0].kind, rows[0].verdict, rows[0].subject_dirty) == ("verdict", "PROCEED", False)


def test_unreadable_results_keep_only_the_verdict_row() -> None:
    cases: dict[str, dict[str, Any]] = {
        "block_absent": {"text": _fixture("code_review_no_block.txt")},
        "block_invalid": {"text": BLOCK_MARKER + "{not json"},
        "content_unavailable": {},
    }
    bad_severity = {
        "verdict": "REVISE",
        "findings": [{"id": "a:1:testing", "severity": "Urgent", "origin": "mechanical", "anchor": "a:1"}],
    }
    for status, kwargs in cases.items():
        rows = project_result(_identity(), **kwargs)
        assert [(r.parse_status, r.verdict, r.kind) for r in rows] == [(status, None, "verdict")]
    rows = project_result(_identity(), text=_line(bad_severity))
    assert [(r.parse_status, r.verdict) for r in rows] == [("block_invalid", None)]


def test_masked_text_still_projects() -> None:
    masked = project_result(_identity(), text=_fixture("code_review_masked.txt"))
    plain = project_result(_identity(), text=_fixture("code_review_two_findings.txt"))
    assert masked == plain
    assert "MASKED" in _fixture("code_review_masked.txt")


def test_validated_results_project_for_every_verdict_persona() -> None:
    critic = {
        "verdict": "REVISE",
        "findings": [{"id": "6:plan", "severity": "blocking", "origin": "judgement", "anchor": "scope"}],
    }
    scout = {
        "verdict": "FLAGS_FOUND",
        "cite": [{"decision": 7, "title": "t", "reason": "r"}],
        "contradictions": [{"id": "contradict:5", "decision": 5, "severity": "WARN"}],
        "spirit": [{"id": "spirit:NS.6", "target": "NS.6", "severity": "NOTE"}],
        "roadmap": {"rows": [{"id": "roadmap:T4.1", "item": "T4.1", "lane": "CONFLICT"}]},
        "related": [],
    }
    rows = project_result(_identity("plan-critique"), result=critic)
    assert [(r.kind, r.finding_id, r.severity, r.anchor) for r in rows] == [
        ("verdict", None, None, None),
        ("finding", "6:plan", "blocking", "scope"),
    ]
    rows = project_result(_identity("decision-scout"), result=scout)
    assert [(r.kind, r.finding_id, r.severity, r.anchor) for r in rows[1:]] == [
        ("contradiction", "contradict:5", "WARN", "dec:5"),
        ("spirit", "spirit:NS.6", "NOTE", "NS.6"),
        ("roadmap", "roadmap:T4.1", "CONFLICT", "T4.1"),
        ("cite", "cite:7", None, "dec:7"),
    ]
    assert all(r.parse_status == "parsed" and r.verdict == "FLAGS_FOUND" and r.subject_dirty is None for r in rows)
    assert [r.finding_ordinal for r in rows] == [0, 1, 2, 3, 4]


def _accepted(field: Any) -> set[str]:
    assert field.dq_intent is not None
    return set(field.dq_intent["accepted_values"]["values"])


def test_lists_cover_every_finding_list_of_each_persona_contract() -> None:
    contract = load_contract(CONTRACT)
    assert contract.fields is not None
    verdicts = _accepted(contract.fields["verdict"])
    severities = _accepted(contract.fields["severity"])
    for persona_id, specs in LISTS.items():
        schema = _schema(persona_id)
        paths = {s.path for s in specs}
        expected: set[str] = set()
        for name, prop in schema["properties"].items():
            if prop.get("type") == "array" and "severity" in prop.get("items", {}).get("properties", {}):
                expected.add(name)
        if persona_id == "decision_scout":
            expected |= {"cite", "roadmap.rows"}
        assert expected <= paths, persona_id
        for spec in specs:
            node = schema
            for part in spec.path.split("."):
                node = node["properties"][part]
            assert node["type"] == "array", spec.path
        assert set(schema["properties"]["verdict"]["enum"]) <= verdicts
        for spec in specs:
            node = schema
            for part in spec.path.split("."):
                node = node["properties"][part]
            sev = node["items"]["properties"].get("severity") or node["items"]["properties"].get("lane")
            if sev:
                assert set(sev["enum"]) <= severities


def test_the_check_holds_types_and_enums_but_not_id_patterns() -> None:
    def item(**kw: Any) -> dict[str, Any]:
        return {"id": "src/x.py:12:Testing", "severity": "High", "origin": "judgement", "anchor": "src/x.py:12", **kw}

    rows = project_result(_identity(), result={"verdict": "REVISE", "findings": [item()]})
    assert (rows[1].finding_id, rows[1].parse_status) == ("src/x.py:12:Testing", "parsed")
    bads: list[dict[str, Any]] = [{"verdict": "REVISE", "findings": [item(severity=3)]}, {"findings": []}]
    for bad in bads:
        rows = project_result(_identity(), result=bad)
        assert [(r.parse_status, r.verdict) for r in rows] == [("block_invalid", None)]
    rows = project_result(
        _identity(), result={"verdict": "PROCEED", "findings": [], "subject_sha": "ABC", "subject_dirty": "false"}
    )
    assert (rows[0].subject_sha, rows[0].subject_dirty, rows[0].verdict) == (None, None, "PROCEED")
    assert project_result(_identity("implement-agent"), result={"verdict": "PROCEED"}) == []
    assert project_result(_identity("nonsense"), text="x") == []


def test_the_code_review_skill_prints_the_block_the_projection_reads() -> None:
    lines = [ln for ln in SKILL.read_text(encoding="utf-8").splitlines() if ln.startswith(BLOCK_MARKER)]
    assert len(lines) == 1
    named = set(re.findall(r'"(\w+)":', lines[0]))
    schema = projected_schema("code_reviewer", _schema("code_reviewer"))
    read = set(schema["properties"]) - {"verdict"} | {"verdict", "subject_sha", "subject_dirty"}
    read |= set(schema["properties"]["findings"]["items"]["properties"])
    assert named == read


_BARE = re.compile(r'(?<=": )\[(N|true\|false[^\]]*)\]')
_CHOICE = re.compile(r"^\[(\w+(?:\|\w+)+)\]$")
_NO_SUBJECT = {"decision_scout"}


def _skill_block(persona_id: str) -> str:
    row = next(r for r in load_index().personas if r.persona == persona_id)
    skill = load_persona_contract(ROOT / row.contract).skill_tether.skill
    text = (ROOT / ".claude" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.startswith(BLOCK_MARKER)]
    assert len(lines) == 1, persona_id
    return lines[0][len(BLOCK_MARKER) :]


def _schema_keys(schema: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name, sub in schema.get("properties", {}).items():
        keys |= {name} | _schema_keys(sub)
    return keys | (_schema_keys(schema["items"]) if "items" in schema else set())


def _fill(value: Any, schema: dict[str, Any], keys: set[str]) -> Any:
    if isinstance(value, dict):
        props = schema.get("properties", {})
        keys |= set(value)
        return {k: _fill(v, props[k], keys) if k in props else v for k, v in value.items()}
    if isinstance(value, list):
        assert len(value) == 1  # count-coupling-ok: the template shows one example item per list
        return [_fill(value[0], schema["items"], keys)]
    if "enum" in schema:
        choice = _CHOICE.match(value)
        assert choice and set(choice.group(1).split("|")) == set(schema["enum"]), (value, schema["enum"])
        return schema["enum"][0]
    assert isinstance(value, int if schema.get("type") == "integer" else str), value
    return value


def test_every_verdict_skill_prints_the_block_the_projection_reads() -> None:
    for row in load_index().personas:
        if row.persona not in LISTS:
            continue
        schema = projected_schema(row.persona, _schema(row.persona))
        example = json.loads(_BARE.sub(lambda m: "1" if m.group(1) == "N" else "true", _skill_block(row.persona)))
        keys: set[str] = set()
        filled = _fill(example, schema, keys)
        subject = set() if row.persona in _NO_SUBJECT else {"subject_sha", "subject_dirty"}
        assert keys == _schema_keys(schema) | subject, row.persona
        identity = RunIdentity("t1", "p1", "run-1", row.registry_key, "a" * 64, "in_session")
        rows = project_result(identity, text="report\n" + _line(filled))
        assert rows[0].parse_status == "parsed", row.persona
        assert [r.kind for r in rows[1:]] == [s.kind for s in LISTS[row.persona]], row.persona


def test_contract_fields_match_the_row() -> None:
    contract = load_contract(CONTRACT)
    assert contract.contract.class_.value == "A"
    assert contract.fields is not None
    assert all(f.derivation is not None and f.derivation["timing"] == "read" for f in contract.fields.values())
    assert list(contract.fields) == [f.name for f in dataclasses.fields(FindingRow)]
    assert PROJECTION_VERSION == 2


@pytest.mark.parametrize("name", ["code_review_two_findings.txt", "code_review_no_findings.txt"])
def test_fixture_marker_is_last_line(name: str) -> None:
    assert _fixture(name).splitlines()[-1].startswith(BLOCK_MARKER)
