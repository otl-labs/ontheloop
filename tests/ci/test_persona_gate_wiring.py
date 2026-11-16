"""Live-tree wiring of the persona dispatch gates (Decision 205 cl.1; PLAN-persona-trigger-policy).

Workflows are found by content (a persona_gate* step), never by filename.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.checks.ci_guards.validate_persona_trigger_policy import (
    DISPATCH_TERM,
    FAILURE_TERM,
    REFUSED_TERM,
    and_terms,
    split_top,
)
from scripts.ci_rca.designed_refusal import PERSONA_GATE_STEP_PREFIX

WORKFLOWS = Path(".github/workflows")
WRITER = "write-convergence-record"


def _gated_jobs() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
            if any(str(s.get("id", "")).startswith("persona_gate") for s in steps):
                found.append((path.name, job_id, job))
    return found


def _gates(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if str(s.get("id", "")).startswith("persona_gate")]


def test_gated_jobs_exist_in_every_dispatchable_llm_path() -> None:
    assert len(_gated_jobs()) >= 4


def test_every_gate_name_starts_with_the_designed_refusal_prefix() -> None:
    for file, job_id, job in _gated_jobs():
        for gate in _gates(job):
            assert str(gate["name"]).startswith(PERSONA_GATE_STEP_PREFIX), (file, job_id)


def test_record_writes_pass_every_gate_refused_output() -> None:
    checked = 0
    for file, job_id, job in _gated_jobs():
        for step in job["steps"]:
            if WRITER in str(step.get("uses", "")):
                passed = str(step["with"].get("dispatch-refused", ""))
                for gate in _gates(job):
                    assert f"steps.{gate['id']}.outputs.refused" in passed, (file, job_id, gate["id"])
                checked += 1
    assert checked >= 2


def test_each_llm_step_if_is_its_gates_if_plus_the_refused_conjunct() -> None:
    checked = 0
    for file, job_id, job in _gated_jobs():
        for step in job["steps"]:
            refused = [m.group(1) for t in and_terms(step.get("if")) if (m := REFUSED_TERM.fullmatch(t))]
            if not refused:
                continue
            gate = next(g for g in _gates(job) if g["id"] == refused[0])
            want = and_terms(step.get("if")) - {f"steps.{gate['id']}.outputs.refused != 'true'"}
            got = and_terms(gate.get("if"))
            assert got == want or got - {DISPATCH_TERM} == want, (file, job_id, step.get("name"))
            checked += 1
    assert checked >= 5


def test_ci_rca_gate_passes_the_own_failure_arguments_and_its_job_if_is_unchanged_in_substance() -> None:
    owners = []
    for file, job_id, job in _gated_jobs():
        for gate in _gates(job):
            run = str(gate.get("run") or "")
            if "--own-failure" in run:
                owners.append(file)
                for arg in ("--run-json", "--jobs-json"):
                    assert arg in run
                assert f"--watched-from .github/workflows/{file}" in run
                assert _job_if_keeps_the_dispatch_or_failure_expression(job)
    assert len(owners) == 1


def _job_if_keeps_the_dispatch_or_failure_expression(job: dict[str, Any]) -> bool:
    for term in and_terms(job.get("if")):
        parts = split_top(term, "||")
        if DISPATCH_TERM in parts and any(FAILURE_TERM in and_terms(p) for p in parts):
            return True
    return False
