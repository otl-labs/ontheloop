"""The findings line and titles block on fresh covered routes (rec-5742)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import app_token, findings_record, override, post_verdict, rescout, resolve_pr

ROOT = Path(__file__).resolve().parents[2]
SHA = "e" * 40
APP_ID = 424242
CREATED = json.dumps({"id": 1, "app": {"id": APP_ID}}).encode()
REF = "litellm:gha:900/1/run/1/run"
CODE = "code_reviewer"
SCOUT = "decision_scout"
CRITIC = "plan_critic"
PERMISSIVE = {"type": "object"}


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def session(repo: str, env: Any = None) -> Iterator[str]:
        yield "app-token"

    monkeypatch.setattr(app_token, "session", session)
    monkeypatch.setattr(override, "app_id", lambda: APP_ID)
    monkeypatch.setattr(post_verdict.execute, "schema_for", lambda root, route, kind: PERMISSIVE)
    monkeypatch.setattr(post_verdict.execute, "failure_schema_for", lambda root, route: PERMISSIVE)
    monkeypatch.setattr(post_verdict.override, "lookup", lambda *a: None)
    monkeypatch.setenv("GITHUB_RUN_ID", "900")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")


def _finding(n: int, severity: str = "High", title: str = "a title") -> dict[str, Any]:
    return {
        "id": f"src/x.py:{n}:testing",
        "severity": severity,
        "origin": "judgement",
        "anchor": f"src/x.py:{n}",
        "title": title,
        "summary": "model text",
    }


def _run(tmp: Path, monkeypatch: pytest.MonkeyPatch, route: str, out: Any, **extra: Any) -> dict[str, Any]:
    work = tmp / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "results").mkdir()
    res = {"resolved": True, "head_sha": SHA, "allowlisted": True, "number": 7}
    (work / "prepared" / "resolution.json").write_text(json.dumps(res))
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [route], "plan_paths": []}))
    stdout = out if isinstance(out, str) else json.dumps(out)
    record = {"route": route, "exit_code": 0, "timed_out": False, "stdout": stdout, "run_ref": REF, **extra}
    record = {k: v for k, v in record.items() if v is not None}
    (work / "results" / f"{route}.json").write_text(json.dumps(record))
    bodies: list[Any] = []
    monkeypatch.setattr(resolve_pr, "request", lambda m, u, t, b=None, accept="": (bodies.append(b), (201, CREATED))[1])
    post_verdict.post_all(ROOT, work, "o/r", "t")
    return bodies[0]["output"]


def _record(text: str) -> dict[str, Any]:
    parsed = findings_record.parse(text)
    assert parsed is not None
    return parsed


def test_fresh_routes_get_the_findings_line_and_carried_ones_do_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = {"outcome": "success", "verdict": "REVISE", "findings": [_finding(1), _finding(2, "Low", "second")]}
    text = _run(tmp_path / "a", monkeypatch, CODE, out, served_models=["m1"], served_model="m1", definition_sha256="a" * 64)[
        "text"
    ]
    lines = text.split("\n")
    rec = _record(text)
    assert lines[0].startswith(findings_record.LINE_PREFIX) and rec["run_ref"] == REF and rec["run_outcome"] == "verdict"
    assert (
        rec["served_models"] == ["m1"]
        and rec["served_model"] == "m1"
        and rec["findings_total"] == 2
        and rec["head_sha"] == SHA
        and rec["run_attempt"] == 1
    )
    assert rec["result"]["verdict"] == "REVISE" and "title" not in json.dumps(rec) and "model text" not in text.split("\n")[0]
    assert lines[1:] == ["- src/x.py:1:testing: a title", "- src/x.py:2:testing: second"]
    no_ref = _run(tmp_path / "b", monkeypatch, CODE, out, run_ref=None)
    assert "text" not in no_ref
    fixture = _run(tmp_path / "c", monkeypatch, "canary_persona", {"outcome": "success", "verdict": "PROCEED", "findings": []})
    assert "text" not in fixture or findings_record.LINE_PREFIX not in fixture["text"]
    entry = {
        "route": CODE,
        "verdict": "REVISE",
        "blocking_ids": [],
        "cites": [],
        "fingerprints": {},
        "from_head": "a" * 40,
        "from_check_run_id": 5,
    }
    body = post_verdict._carried_body(ROOT, {"head_sha": SHA, "allowlisted": True, "number": 7}, entry, "o/r", "t")
    assert findings_record.LINE_PREFIX not in str(body["text"])


def test_placement_after_record_and_input_lines_with_the_blocking_block_extended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(post_verdict.override, "extractor_for", lambda route: lambda out: ("REVISE", ["b:1"]))
    monkeypatch.setattr(post_verdict.override, "overridable", lambda route: True)
    out = {
        "outcome": "success",
        "verdict": "REVISE",
        "findings": [{**_finding(1, "blocking"), "id": "b:1"}, {**_finding(2, "nit"), "id": "n:2", "title": "n" * 300}],
    }
    monkeypatch.setattr(post_verdict.rescout, "fresh_input", lambda *a: "<!-- persona-verdict-input v1 {} -->")
    text = _run(tmp_path, monkeypatch, CRITIC, out)["text"]
    lines = text.split("\n")
    assert lines[0].startswith(override.RECORD_PREFIX)
    assert lines[1].startswith(rescout.INPUT_PREFIX) and lines[2].startswith(findings_record.LINE_PREFIX)
    assert lines[3] == "- b:1: a title" and lines[4] == "- n:2: " + "n" * post_verdict.TITLE_CHARS and len(lines) == 5


def test_failures_and_overrides_keep_a_findings_line_inside_the_text_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = _run(tmp_path / "a", monkeypatch, CODE, {"outcome": "failure", "failure": "review_incomplete", "detail": "d"})
    rec = _record(failed["text"])
    assert (rec["run_outcome"], rec["failure_id"], rec["result"]) == ("persona_failure", "review_incomplete", None)
    host = _run(tmp_path / "b", monkeypatch, CODE, "junk {")
    assert (
        _record(host["text"])["run_outcome"] == "host_failure" and _record(host["text"])["failure_id"] == "unparseable_output"
    )
    timed = _run(tmp_path / "c", monkeypatch, CODE, "{}", timed_out=True)
    assert _record(timed["text"])["failure_id"] == "timed_out"
    many = [_finding(n, title="t" * 400) for n in range(500)]
    big = {"outcome": "success", "verdict": "REVISE", "findings": many}
    reads = [{"tree": "head", "op": "read", "path": f"p{n}", "result": "ok"} for n in range(2000)]
    text = _run(tmp_path / "d", monkeypatch, CODE, big, reads=reads, not_examined=[f"{n}/" + "x" * 150 for n in range(100)])[
        "text"
    ]
    assert len(text) <= post_verdict.TEXT_LIMIT
    rec = _record(text.split("\n")[0])
    assert rec["truncated"] is True and rec["findings_total"] == 500 and len(rec["result"]["findings"]) <= 200
    assert len(text.split("\n")[0].encode()) <= findings_record.MAX_LINE_BYTES + 100
    approved = {"run_id": 70, "approver": "op", "approved_at": "t", "url": "https://x/70"}
    line = findings_record.render(
        {"route": CRITIC, "run_ref": REF, "run_outcome": "verdict"}, {"verdict": "REVISE", "findings": []}
    )
    source = post_verdict.override.render_record(CRITIC, SHA, "REVISE", ["a:1"], "3") + "\n" + line
    kept = post_verdict._override_form(approved, "REVISE", ["a:1"], source.split("\n")[0], source)[2].split("\n")
    assert kept[0].startswith(override.RECORD_PREFIX) and kept[1] == line and kept[2].startswith(override.OVERRIDE_PREFIX)
