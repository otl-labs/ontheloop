"""Projection version 2: run_outcome, failure_id, run_ref, served_model and restrict_result (rec-5742)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.persona_contracts import ROOT
from scripts.persona_contracts.findings import BLOCK_MARKER, PROJECTION_VERSION, RunIdentity, project_result, restrict_result

CONTRACT = ROOT / "docs" / "contracts" / "telemetry_persona_findings.yaml"
REF = "litellm:gha:9/1/run/1/run"


def _identity(lane: str = "actions", agent: str = "code-review") -> RunIdentity:
    return RunIdentity(None, None, None, agent, "a" * 64, lane, run_ref=REF, served_model="m1")


def _finding(n: int = 1) -> dict[str, Any]:
    return {"id": f"a.py:{n}:testing", "severity": "High", "origin": "judgement", "anchor": f"a.py:{n}"}


def test_a_persona_failure_projects_one_parsed_row_with_its_failure_id() -> None:
    failure = {"outcome": "failure", "failure": "review_incomplete", "detail": "x"}
    for kwargs in (
        {"result": failure},
        {"text": BLOCK_MARKER + json.dumps(failure)},
        {"result": None, "run_outcome": "persona_failure", "failure_id": "review_incomplete"},
    ):
        rows = project_result(_identity(), **kwargs)
        assert len(rows) == 1
        row = rows[0]
        assert (row.parse_status, row.verdict, row.kind, row.finding_ordinal) == ("parsed", None, "verdict", 0)
        assert (row.run_outcome, row.failure_id) == ("persona_failure", "review_incomplete")
    doc = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    assert "run_outcome is persona_failure" in doc["fields"]["verdict"]["semantics"]
    assert "persona failure" in doc["fields"]["parse_status"]["semantics"]
    assert doc["contract"]["contract_version"] == 2 and PROJECTION_VERSION == 2


def test_unreadable_blocks_and_restrict_result() -> None:
    absent = project_result(_identity("in_session"), text="no block")[0]
    invalid = project_result(_identity("in_session"), text=BLOCK_MARKER + "{bad")[0]
    assert (absent.run_outcome, invalid.run_outcome) == ("unreadable", "unreadable")
    assert project_result(_identity(), result={"verdict": "NOPE", "findings": []})[0].run_outcome == "unreadable"
    full = {
        "verdict": "REVISE",
        "summary": "model text",
        "findings": [{**_finding(), "title": "t", "detail": "d"}],
        "extra": 1,
    }
    assert restrict_result("code_reviewer", full) == {"verdict": "REVISE", "findings": [_finding()]}
    assert restrict_result("code-review", full) == {"verdict": "REVISE", "findings": [_finding()]}
    assert restrict_result("code_reviewer", {"verdict": "REVISE", "findings": [{"id": 3}]}) is None
    assert restrict_result("canary_persona", full) is None and restrict_result("code_reviewer", "x") is None
    scout = {
        "verdict": "FLAGS_FOUND",
        "cite": [{"decision": 7, "title": "t"}],
        "contradictions": [{"id": "c:1", "decision": 5, "severity": "WARN", "why": "w"}],
        "spirit": [],
        "roadmap": {"status": "x", "rows": [{"id": "r:T1", "item": "T1", "lane": "CITE", "note": "n"}]},
    }
    got = restrict_result("decision_scout", scout)
    assert got is not None and got["roadmap"] == {"rows": [{"id": "r:T1", "item": "T1", "lane": "CITE"}]}
    assert got["cite"] == [{"decision": 7}] and "why" not in json.dumps(got)
    rows = project_result(_identity(agent="decision-scout"), result=got)
    assert [r.kind for r in rows] == ["verdict", "contradiction", "roadmap", "cite"] and {r.run_outcome for r in rows} == {
        "verdict"
    }


def test_host_failure_and_run_identity_reach_every_row() -> None:
    host = project_result(_identity(), result=None, run_outcome="host_failure", failure_id="timed_out")
    assert len(host) == 1 and (host[0].parse_status, host[0].verdict, host[0].run_outcome) == (
        "content_unavailable",
        None,
        "host_failure",
    )
    assert host[0].failure_id == "timed_out"
    rows = project_result(
        _identity(), result={"verdict": "REVISE", "findings": [_finding(1), _finding(2)]}, subject_sha="1" * 40
    )
    assert len(rows) == 3
    for row in rows:
        assert (row.run_ref, row.served_model, row.run_outcome, row.failure_id) == (REF, "m1", "verdict", None)
        assert (row.tenant_id, row.project_id, row.agent_run_id, row.lane, row.subject_sha) == (
            None,
            None,
            None,
            "actions",
            "1" * 40,
        )
    assert Path(CONTRACT).exists()
