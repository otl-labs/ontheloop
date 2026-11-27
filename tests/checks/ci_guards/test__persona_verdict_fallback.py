"""The run-fallback pins: the live workflow passes; each mutation is refused with its own message."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.ci_guards import _persona_verdict_fallback as f
from scripts.checks.ci_guards import _persona_verdict_keyed as k
from scripts.checks.ci_guards import validate_persona_verdict_chain as v

LIVE = Path(_common.ROOT)


def _jobs() -> dict[str, Any]:
    doc = yaml.safe_load((LIVE / v.VERDICT).read_text(encoding="utf-8"))
    return copy.deepcopy(doc["jobs"])


def _persona(job: dict[str, Any]) -> dict[str, Any]:
    (step,) = [s for s in job["steps"] if s.get("name") == "Run the fixture persona"]
    return step


CASES: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
    (
        "condition-without-drill",
        "condition",
        lambda j: j["run-fallback"].update(
            {"if": k.CONDITIONS["run-fallback"].replace(" || needs.prepare.outputs.fallback_drill == 'true'", "")}
        ),
    ),
    ("condition-extra", "condition", lambda j: j["run-fallback"].update({"if": k.CONDITIONS["run-fallback"] + " || true"})),
    (
        "role-primary-in-fallback",
        "PERSONA_ROLE",
        lambda j: _persona(j["run-fallback"])["env"].update({"PERSONA_ROLE": "primary"}),
    ),
    (
        "role-fallback-in-run",
        "PERSONA_ROLE",
        lambda j: _persona(j["run"])["env"].update({"PERSONA_ROLE": "warm_fetched_escape_hatch"}),
    ),
    ("run-outputs-literal", "run job's outputs", lambda j: j["run"].update({"outputs": {"primary_outage": "true"}})),
    (
        "run-outputs-extra",
        "run job's outputs",
        lambda j: j["run"]["outputs"].update({"x": "${{ steps.run-persona.outputs.x }}"}),
    ),
    ("prepare-outputs-missing", "prepare's outputs", lambda j: j["prepare"]["outputs"].pop("fallback_drill")),
    ("prepare-outputs-no-keyed", "prepare's outputs", lambda j: j["prepare"]["outputs"].pop("keyed")),
    ("prepare-outputs-literal", "prepare's outputs", lambda j: j["prepare"]["outputs"].update({"fallback_drill": "true"})),
    (
        "other-job-names-output",
        "names primary_outage",
        lambda j: j["post"].update({"outputs": {"a": "${{ needs.run.outputs.primary_outage }}"}}),
    ),
    ("strategy", "closed set", lambda j: j["run-fallback"].update({"strategy": {"matrix": {"a": [1, 2]}}})),
    ("matrix-outputs", "closed set", lambda j: j["run-fallback"].update({"outputs": {"a": "b"}})),
]


def test_run_fallback_shape_is_closed() -> None:
    assert f.fallback_problems(_jobs()) == []
    for name, message, edit in CASES:
        jobs = _jobs()
        edit(jobs)
        found = f.fallback_problems(jobs) + k.keyed_problems({"jobs": jobs})
        assert any(message in p for p in found), (name, found)


@pytest.mark.parametrize(
    "edit",
    [
        lambda j: j["run-fallback"].update({"permissions": {"contents": "read"}}),
        lambda j: j["run-fallback"].pop("environment"),
        lambda j: j["run-fallback"].update({"environment": "persona-verdict-app"}),
    ],
)
def test_run_fallback_permissions_and_environment(edit: Callable[[dict[str, Any]], None]) -> None:
    jobs = _jobs()
    edit(jobs)
    assert any("permissions are not" in p or "Environment is not" in p for p in f.fallback_problems(jobs))


def test_persona_id_is_none_without_the_persona_step() -> None:
    assert f._persona_id({}) is None
    assert f._persona_id({"steps": [{"name": "Checkout", "id": "co"}, "not-a-step"]}) is None
    assert f._persona_id(_jobs()["run"]) is not None


def test_run_outputs_refused_when_run_lacks_the_persona_step() -> None:
    jobs = _jobs()
    jobs["run"]["steps"] = [s for s in jobs["run"]["steps"] if s.get("name") != "Run the fixture persona"]
    assert any("run job's outputs" in p for p in f.fallback_problems(jobs))


def test_run_fallback_reading_fixture_is_refused() -> None:
    jobs = _jobs()
    keyed = "needs.prepare.outputs.keyed == 'true'"
    assert keyed in jobs["run-fallback"]["if"]
    jobs["run-fallback"]["if"] = jobs["run-fallback"]["if"].replace(keyed, "needs.prepare.outputs.fixture == 'true'")
    assert any("condition" in p for p in k.keyed_problems({"jobs": jobs}))
