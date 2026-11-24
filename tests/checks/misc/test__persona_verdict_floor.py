"""_persona_verdict_floor: each floor rule refuses its red fixture and the good job passes."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from scripts.checks.misc import _persona_verdict_floor as f

REL = ".github/workflows/persona-verdict.yml"
NAME = "smoke"
FLOOR = "sudo " + f.RUN_PREFIX + "egress-floor"
CHECK = f.RUN_PREFIX + "egress-self-check"
CHECKOUT = "actions/checkout@" + "a" * 40


def _job() -> dict[str, Any]:
    return {
        "steps": [
            {"uses": "step-security/harden-runner@x", "with": {"disable-sudo-and-containers": False}},
            {"uses": CHECKOUT, "with": {"path": "main", "persist-credentials": False}},
            {"name": "f", "id": "f", "run": FLOOR},
            {"run": CHECK},
            {"run": "echo work"},
            {"id": "z", "run": CHECK, "if": "always()"},
        ]
    }


def _problems(edit: Callable[[dict[str, Any]], None] | None = None, rel: str = REL, name: str = NAME) -> list[str]:
    job = copy.deepcopy(_job())
    if edit:
        edit(job)
    return f.floor_problems(rel, name, job)


def _steps(edit: Callable[[list[dict[str, Any]]], None]) -> Callable[[dict[str, Any]], None]:
    return lambda job: edit(job["steps"])


RED = [
    ("switch-true", "must be false", lambda j: j["steps"][0]["with"].update({"disable-sudo-and-containers": True})),
    ("no-floor", "exactly one egress floor", lambda j: j["steps"].pop(2)),
    ("two-floors", "exactly one egress floor", lambda j: j["steps"].insert(3, {"run": FLOOR})),
    ("floor-after-self-check", "neither the filter nor a checkout", lambda j: j["steps"].insert(2, {"run": CHECK})),
    ("checkout-after-floor", "after the egress floor", lambda j: j["steps"].insert(3, {"uses": CHECKOUT})),
    ("floor-env", "keys beyond name, id and run", lambda j: j["steps"][2].update({"env": {"A": "b"}})),
    ("floor-if", "keys beyond name, id and run", lambda j: j["steps"][2].update({"if": "always()"})),
    ("floor-working-directory", "keys beyond name, id and run", lambda j: j["steps"][2].update({"working-directory": "x"})),
    ("floor-continue-on-error", "keys beyond name, id and run", lambda j: j["steps"][2].update({"continue-on-error": True})),
    ("floor-shell", "keys beyond name, id and run", lambda j: j["steps"][2].update({"shell": "bash"})),
    ("checkout-with-token", "with: inputs beyond", lambda j: j["steps"][1]["with"].update({"token": "x"})),
    ("second-sudo-step", "runs sudo", lambda j: j["steps"].insert(4, {"run": "sudo true"})),
    (
        "drill-verb-outside-its-job",
        "must run exactly",
        lambda j: j["steps"][2].update({"run": FLOOR.replace("egress-floor", "egress-revert-drill-control")}),
    ),
    ("no-final-check", "last step must be", lambda j: j["steps"].pop()),
    ("final-without-always", "last step must be", lambda j: j["steps"][-1].pop("if")),
    ("final-with-env", "last step must be", lambda j: j["steps"][-1].update({"env": {"A": "b"}})),
    ("final-with-working-directory", "last step must be", lambda j: j["steps"][-1].update({"working-directory": "x"})),
]


def test_the_good_job_passes() -> None:
    assert _problems() == []


@pytest.mark.parametrize(("name", "message", "edit"), RED, ids=[r[0] for r in RED])
def test_each_red_fixture_is_refused(name: str, message: str, edit: Callable[[dict[str, Any]], None]) -> None:
    assert any(message in p for p in _problems(edit)), _problems(edit)


def _probe_job(verb: str) -> dict[str, Any]:
    job = _job()
    job["steps"][2]["run"] = "sudo " + f.PROBE_PREFIX + verb
    job["steps"][5]["run"] = f.PROBE_PREFIX + "egress-self-check"
    job["steps"][3]["run"] = f.PROBE_PREFIX + "egress-self-check"
    return job


@pytest.mark.parametrize(
    ("job_name", "verb"),
    [
        ("egress-probe", "egress-floor"),
        ("egress-fail-closed", "egress-revert-drill"),
        ("egress-fail-closed-control", "egress-revert-drill-control"),
    ],
)
def test_each_probe_job_takes_only_its_own_floor_verb(job_name: str, verb: str) -> None:
    assert f.floor_problems(f.PROBE, job_name, _probe_job(verb)) == []
    for other in ("egress-floor", "egress-revert-drill", "egress-revert-drill-control"):
        if other != verb:
            assert any("must run exactly" in p for p in f.floor_problems(f.PROBE, job_name, _probe_job(other)))


def test_a_drill_verb_in_a_verdict_workflow_job_is_refused() -> None:
    job = _job()
    job["steps"][2]["run"] = "sudo " + f.RUN_PREFIX + "egress-revert-drill"
    assert any("must run exactly" in p for p in f.floor_problems(REL, NAME, job))


def test_is_floor_line_matches_only_sudo_with_a_known_prefix_and_verb() -> None:
    assert f.is_floor_line(FLOOR) and f.is_floor_line("sudo " + f.PROBE_PREFIX + "egress-revert-drill")
    assert not f.is_floor_line(f.RUN_PREFIX + "egress-floor") and not f.is_floor_line("sudo " + f.RUN_PREFIX + "other")
    assert not f.is_floor_line("")


def test_a_job_with_no_steps_has_nothing_to_check() -> None:
    assert f.floor_problems(REL, NAME, {}) == []


def _container_job() -> dict[str, Any]:
    job = _job()
    job["steps"][2]["run"] = "sudo " + f.PROBE_PREFIX + "egress-floor-container"
    job["steps"][3]["run"] = f.PROBE_PREFIX + "egress-self-check"
    job["steps"][5]["run"] = f.PROBE_PREFIX + "egress-self-check"
    job["steps"][4] = {
        "name": "l",
        "id": "l",
        "run": "x | sudo " + "/usr/bin/python3 -I -S /usr/local/lib/persona-egress/egress_launcher.py probe",
    }
    return job


def test_the_container_floor_verb_and_launcher_line_are_accepted_in_the_class() -> None:
    job = _container_job()
    job["steps"][4]["run"] = (
        f.PROBE_PREFIX
        + "egress-container-request | sudo "
        + ("/usr/bin/python3 -I -S /usr/local/lib/persona-egress/egress_launcher.py probe")
    )
    assert f.floor_problems(f.PROBE, "egress-container", job) == []
    assert f.floor_run(f.PROBE, "egress-container", job) == "sudo " + f.PROBE_PREFIX + "egress-floor-container"


def test_the_launcher_line_is_refused_outside_the_class() -> None:
    job = _job()
    job["steps"][4]["run"] = (
        f.PROBE_PREFIX
        + "egress-container-request | sudo "
        + ("/usr/bin/python3 -I -S /usr/local/lib/persona-egress/egress_launcher.py probe")
    )
    assert any("runs sudo" in p for p in f.floor_problems(REL, NAME, job))


def test_the_plain_floor_verb_is_refused_in_a_class_job() -> None:
    job = _container_job()
    job["steps"][2]["run"] = FLOOR
    assert any("must run exactly" in p or "runs sudo" in p for p in f.floor_problems(f.PROBE, "egress-container", job))
