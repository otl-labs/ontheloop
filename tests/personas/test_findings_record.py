"""The text-free findings line and the code-built TLDR line (rec-5742)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.personas import findings_record

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "contracts" / "telemetry_persona_findings.yaml"
URL = "https://github.com/o/r/runs/5"
FACTS = {
    "route": "code_reviewer",
    "head_sha": "a" * 40,
    "run_id": "9",
    "run_attempt": 1,
    "run_ref": "litellm:gha:9/1/run/1/run",
    "session_started_at": None,
    "definition_sha256": "b" * 64,
    "served_models": ["m1"],
    "run_outcome": "verdict",
    "failure_id": None,
}


def _finding(n: int, **over: Any) -> dict[str, Any]:
    return {"id": f"src/x.py:{n}:testing", "severity": "High", "origin": "judgement", "anchor": f"src/x.py:{n}", **over}


def _accepted(field: str) -> tuple[str, ...]:
    doc = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return tuple(doc["fields"][field]["dq_intent"]["accepted_values"]["values"])


def test_record_round_trips_and_holds_no_free_text() -> None:
    hostile = _finding(1, id="x --> <script>", anchor="a b")
    result = {"verdict": "REVISE", "findings": [_finding(2), hostile]}
    line = findings_record.render(FACTS, result)
    assert "\n" not in line and line.count("-->") == 1 and "<script" not in line and line.endswith(" -->")
    rec = findings_record.parse("x\n" + line + "\n")
    assert rec is not None and rec["route"] == "code_reviewer" and rec["findings_total"] == 2
    assert rec["redacted"] == 2 and rec["truncated"] is False
    assert rec["result"]["findings"][0] == _finding(2)
    assert rec["result"]["findings"][1]["id"] == findings_record.REDACTED
    assert findings_record.line_of("a\n" + line) == line and findings_record.line_of("none") is None
    assert findings_record.parse("<!-- persona-verdict-findings v1 {bad} -->") is None
    assert findings_record.parse(None) is None
    empty = findings_record.parse(findings_record.render({**FACTS, "run_outcome": "host_failure"}, None))
    assert empty is not None and empty["result"] is None and empty["findings_total"] == 0
    nested = {
        "verdict": "FLAGS_FOUND",
        "cite": [{"decision": 7}],
        "roadmap": {"rows": [{"id": "roadmap:T1", "item": "T1", "lane": "CITE"}]},
    }
    assert findings_record.parse(findings_record.render(FACTS, nested))["findings_total"] == 2  # type: ignore[index]


def test_record_caps_findings_and_marks_truncated() -> None:
    many = {"verdict": "REVISE", "findings": [{"id": f"a:{n}", "severity": "nit"} for n in range(450)]}
    rec = findings_record.parse(findings_record.render(FACTS, many))
    assert rec is not None and rec["findings_total"] == 450 and rec["truncated"] is True
    assert len(rec["result"]["findings"]) == findings_record.MAX_FINDINGS
    wide = {"verdict": "REVISE", "findings": [_finding(n, anchor="y" * 190, id="z" * 190) for n in range(200)]}
    line = findings_record.render(FACTS, wide)
    rec = findings_record.parse(line)
    assert len(line.encode()) <= findings_record.MAX_LINE_BYTES and rec is not None and rec["truncated"] is True
    assert 0 < len(rec["result"]["findings"]) < 200 and rec["findings_total"] == 200
    assert findings_record.parse(findings_record.render(FACTS, {"verdict": "PROCEED", "findings": []}))["truncated"] is False  # type: ignore[index]


def test_tldr_accepted_values_match_the_contract() -> None:
    assert findings_record.VERDICTS == _accepted("verdict")
    assert findings_record.SEVERITIES == _accepted("severity")
    assert findings_record.ORIGINS == _accepted("origin")
    assert findings_record.RUN_OUTCOMES == _accepted("run_outcome")


def test_tldr_line_prints_typed_values_only() -> None:
    result = {"verdict": "REVISE", "findings": [_finding(1), _finding(2, severity="Low", origin="mechanical"), _finding(3)]}
    rec = findings_record.parse(findings_record.render(FACTS, result))
    got = findings_record.tldr_line("code_reviewer", "failure", rec, URL)
    assert got == f"persona-verdict/code_reviewer (failure): REVISE 3 findings High=2 Low=1 mechanical=1 judgement=2 {URL}"
    ok = findings_record.parse(findings_record.render(FACTS, {"verdict": "PROCEED", "findings": []}))
    assert findings_record.tldr_line("code_reviewer", "success", ok, URL) == (
        f"persona-verdict/code_reviewer (success): PROCEED 0 findings {URL}"
    )
    failed = findings_record.parse(
        findings_record.render({**FACTS, "run_outcome": "persona_failure", "failure_id": "x"}, None)
    )
    assert findings_record.tldr_line("code_reviewer", "failure", failed, URL).endswith(f"persona_failure 0 findings {URL}")
    none = f"persona-verdict/code_reviewer (success): no findings record {URL}"
    assert findings_record.tldr_line("code_reviewer", "success", None, URL) == none
    evil = {**(rec or {}), "run_outcome": "<b>x</b>"}
    assert findings_record.tldr_line("code_reviewer", "success", evil, URL) == none
    assert findings_record.tldr_line("Bad Route", "success", rec, URL).endswith(f"no findings record {URL}")
    odd = {**(rec or {}), "result": {"verdict": "SECRET TEXT", "findings": [_finding(1, severity="owned")]}}
    printed = findings_record.tldr_line("code_reviewer", "weird", odd, URL)
    assert "SECRET" not in printed and "owned" not in printed and "weird" not in printed
    assert re.fullmatch(r"[A-Za-z0-9_/ ():=.,\-]+ https://\S+", printed)


def test_the_module_imports_stdlib_only() -> None:
    code = (
        "import sys\nfor m in ('yaml','pydantic','jsonschema','boto3'): sys.modules[m] = None\n"
        "import scripts.personas.findings_record\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert json.dumps(sorted(findings_record.RECORD_KEYS))
