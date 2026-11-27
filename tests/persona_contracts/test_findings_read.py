"""The read-only two-lane findings read over recorded verb and check-run fixtures (rec-5742)."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
from typing import Any

import pytest

from scripts.persona_contracts import ROOT, findings_read
from scripts.personas import findings_record

APP = 55
HEAD = "d" * 40
OTHER = "e" * 40
SRC = ROOT / "scripts" / "persona_contracts" / "findings_read.py"


def _finding(n: int = 1) -> dict[str, Any]:
    return {"id": f"a.py:{n}:testing", "severity": "High", "origin": "judgement", "anchor": f"a.py:{n}"}


def _line(route: str = "code_reviewer", ref: str = "litellm:gha:9/1/run/1/run", head: str = HEAD, **over: Any) -> str:
    facts = {"route": route, "head_sha": head, "run_ref": ref, "run_outcome": "verdict", "served_models": ["m0", "m1"]} | over
    result = None if facts["run_outcome"] != "verdict" else {"verdict": "REVISE", "findings": [_finding()]}
    return findings_record.render(facts, result)


def _check(cid: int, route: str = "code_reviewer", app: int = APP, text: str = "", title: str = "t") -> dict[str, Any]:
    return {"id": cid, "name": f"persona-verdict/{route}", "app": {"id": app}, "output": {"text": text, "title": title}}


class Github:
    def __init__(self, checks: dict[str, list[dict[str, Any]]], runs: list[dict[str, Any]], status: int = 200) -> None:
        self.checks, self.runs, self.status, self.paths = checks, runs, status, []

    def __call__(self, path: str) -> tuple[int, bytes]:
        self.paths.append(path)
        if self.status != 200:
            return self.status, b"{}"
        if "/actions/workflows/" in path:
            return 200, json.dumps({"workflow_runs": self.runs}).encode()
        head = path.split("/commits/")[1].split("/")[0]
        return 200, json.dumps({"check_runs": self.checks.get(head, [])}).encode()


def _run(sha: str, conclusion: str = "success") -> dict[str, Any]:
    return {"display_title": f"persona-verdict pull_request {sha}", "conclusion": conclusion}


def test_both_lanes_project_rows_and_foreign_app_runs_are_ignored() -> None:
    checks = {HEAD: [_check(1, text=_line()), _check(2, app=7, text=_line(ref="litellm:gha:evil/1/run/1/run"))]}
    rows, gaps = findings_read.read_actions(Github(checks, [_run(HEAD)]), "o/r", APP, "2026-11-27", "2026-11-28")
    assert gaps == [] and [r.finding_ordinal for r in rows] == [0, 1]
    first = rows[0]
    assert (first.lane, first.agent_type, first.run_ref, first.run_outcome, first.verdict) == (
        "actions",
        "code-review",
        "litellm:gha:9/1/run/1/run",
        "verdict",
        "REVISE",
    )
    assert (first.tenant_id, first.project_id, first.agent_run_id, first.served_model, first.subject_sha) == (
        None,
        None,
        None,
        "m1",
        HEAD,
    )
    assert rows[1].finding_id == "a.py:1:testing" and rows[1].anchor == "a.py:1"
    failed = _line(run_outcome="persona_failure", failure_id="review_incomplete")
    rows, _ = findings_read.read_actions(Github({HEAD: [_check(3, text=failed)]}, [_run(HEAD)]), "o/r", APP, "d", "d")
    assert [(r.run_outcome, r.failure_id, r.verdict) for r in rows] == [("persona_failure", "review_incomplete", None)]
    lane = _in_session_fixture()
    both = findings_read.read_in_session(*lane, "2026-11-25", "2026-11-25")
    assert {r.lane for r in both} == {"in_session"} and {r.run_ref for r in both} == {"toolu_1"}


def test_missing_records_are_gaps_and_reruns_dedupe_by_run_ref() -> None:
    checks = {
        HEAD: [
            _check(1, text=_line()),
            _check(4, text=_line(ref="litellm:gha:9/1/run/1/run", run_outcome="host_failure", failure_id="timed_out")),
            _check(2, "decision_scout", text="nothing", title="verdict NO_FLAGS (carried from abc)"),
            _check(3, "plan_critic", text="nothing"),
            _check(5, text=_line(head=OTHER)),
        ],
    }
    runs = [_run(HEAD), _run(OTHER, "cancelled"), _run("f" * 40, "cancelled")]
    runs.append({"display_title": "something else", "conclusion": "cancelled"})
    rows, gaps = findings_read.read_actions(Github(checks, runs), "o/r", APP, "a", "b")
    assert (
        {r.run_ref for r in rows} == {"litellm:gha:9/1/run/1/run"} and rows[0].run_outcome == "host_failure" and len(rows) == 1
    )
    assert {(g["head_sha"], g["route"], g["reason"]) for g in gaps} == {
        (HEAD, "decision_scout", "carried"),
        (HEAD, "plan_critic", "no_record"),
        (OTHER, "", "cancelled"),
        ("f" * 40, "", "cancelled"),
    }
    for status, code in ((403, "rate_limited"), (500, "http_500")):
        with pytest.raises(findings_read.ReadError, match=code):
            findings_read.read_actions(Github({}, [], status), "o/r", APP, "a", "b")
    endless = Github({}, [_run(HEAD)] * findings_read.PER_PAGE)
    with pytest.raises(findings_read.ReadError, match="listing_not_complete"):
        findings_read.read_actions(endless, "o/r", APP, "a", "b")


def _b64(text: str) -> dict[str, Any]:
    data = text.encode()
    return {
        "data": base64.b64encode(data).decode(),
        "length": len(data),
        "storage": "inline",
        "masked_bytes": len(data),
        "masked_sha256": hashlib.sha256(data).hexdigest(),
    }


def _in_session_fixture(final: str | None = None) -> tuple[Any, Any]:
    block = "PERSONA_RESULT: " + json.dumps(
        {"verdict": "PROCEED", "findings": [], "subject_sha": "1" * 40, "subject_dirty": False}
    )
    entry = json.dumps({"message": "Report\n" + block}) if final is None else final
    agent = {
        "event_kind": "close",
        "agent_type": "code-review",
        "observation_id": "o1",
        "agent_run_id": "run-1",
        "tenant_id": "t",
        "project_id": "p",
        "session_id": "child",
        "entity_ref": "toolu_1",
        "definition_sha256": "a" * 64,
    }
    trs = [
        {
            "session_id": "child",
            "origin": "agent",
            "purpose": "response",
            "source_ordinal": 62,
            "event_id": "e62",
            "producer": "claude_code",
            "parser_version": 11,
        },
        {
            "session_id": "child",
            "origin": "agent",
            "purpose": "tool_input",
            "source_ordinal": 63,
            "event_id": "e63",
            "producer": "claude_code",
            "parser_version": 11,
        },
        {
            "session_id": "other",
            "origin": "agent",
            "purpose": "response",
            "source_ordinal": 99,
            "event_id": "e99",
            "producer": "claude_code",
            "parser_version": 11,
        },
    ]
    entries = [
        {"table_name": "telemetry_agents", "row_json": json.dumps(agent), "total_rows": 5, "roots_in_tree": 1},
        {
            "table_name": "telemetry_agents",
            "row_json": json.dumps({**agent, "event_kind": "open"}),
            "total_rows": 5,
            "roots_in_tree": 1,
        },
        {
            "table_name": "telemetry_agents",
            "row_json": json.dumps({**agent, "agent_run_id": "r2", "observation_id": None}),
            "total_rows": 5,
            "roots_in_tree": 1,
        },
        *[
            {"table_name": "telemetry_transcripts", "row_json": json.dumps(t), "total_rows": 5, "roots_in_tree": 1}
            for t in trs[:2]
        ],
    ]
    texts = {"e62": "Fine (internal label). Report.", "e63": entry}

    class Named:
        def __call__(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            if verb == "telemetry_root_sessions":
                return [{"session_id": "root"}]
            return entries if params["offset"] == 0 else []

    class Content:
        calls: list[dict[str, Any]] = []

        def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            assert verb == "transcript_row_masked" and params["session_id"] == "child"
            self.calls.append(params)
            return [_b64(texts[params["event_id"]])]

    return Named(), Content()


def test_escaped_entry_is_decoded_and_no_text_is_printed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    named, content = _in_session_fixture()
    rows = findings_read.read_in_session(named, content, "2026-11-25", "2026-11-25")
    assert len(rows) == 1 and (rows[0].verdict, rows[0].parse_status, rows[0].subject_sha) == ("PROCEED", "parsed", "1" * 40)
    assert rows[0].source_event_id == "e63" and rows[0].run_ref == "toolu_1" and rows[0].agent_run_id == "run-1"
    assert findings_read.decode_entry('{"message":"a\\nb","n":3}') == "a\nb" and findings_read.decode_entry("plain") == "plain"
    named, content = _in_session_fixture(final=json.dumps({"message": "no block at all"}))
    rows = findings_read.read_in_session(named, content, "2026-11-25", "2026-11-25")
    assert (rows[0].parse_status, rows[0].run_outcome) == ("block_absent", "unreadable")
    monkeypatch.setattr(findings_read, "verdict_app_id", lambda: APP)
    monkeypatch.setattr(findings_read, "read_in_session", lambda *a: [])
    github = Github({HEAD: [_check(1, text=_line() + "\n- a.py:1:testing: SECRET TITLE")]}, [_run(HEAD)])
    monkeypatch.setattr(findings_read, "_github_get", github)
    assert findings_read.main(["--day-from", "2026-11-27", "--day-to", "2026-11-27", "--lane", "actions"]) == 0
    out = capsys.readouterr().out
    assert "SECRET" not in out and '"lane": "actions"' in out and all(json.loads(line) for line in out.splitlines())
    assert findings_read.main(["--day-from", "bad", "--day-to", "x"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_the_read_imports_no_writer_and_writes_nothing() -> None:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [f"{node.module}.{a.name}" for a in node.names]
    text = SRC.read_text(encoding="utf-8")
    assert not any(bad in name for name in names for bad in ("writer", "ops_data_portal", "file_rec", "update_rec"))
    assert '"POST"' not in text and '"PUT"' not in text and '"PATCH"' not in text and '"DELETE"' not in text
    assert 'request("GET"' in text


def test_a_head_with_one_completed_run_is_not_a_cancelled_gap() -> None:
    runs = [_run(HEAD, "cancelled"), _run(HEAD, "success")]
    rows, gaps = findings_read.read_actions(Github({}, runs), "o/r", APP, "a", "b")
    assert rows == [] and gaps == []
