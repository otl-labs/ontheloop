"""The post-job pins: each mutation of the live persona-verdict.yml is refused with its own message; the live file passes."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.ci_guards import _persona_verdict_keyed as k
from scripts.checks.ci_guards import _persona_verdict_post as p
from scripts.checks.ci_guards import validate_persona_verdict_chain as v

LIVE = Path(_common.ROOT)


def _doc() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load((LIVE / v.VERDICT).read_text(encoding="utf-8")))


def _workflows() -> dict[str, Any]:
    return {f.name: yaml.safe_load(f.read_text(encoding="utf-8")) for f in (LIVE / ".github/workflows").glob("*.yml")}


def _post(doc: dict[str, Any]) -> dict[str, Any]:
    job: dict[str, Any] = doc["jobs"]["post"]
    return job


def _named(doc: dict[str, Any], name: str) -> dict[str, Any]:
    (found,) = [s for s in _post(doc)["steps"] if s.get("name") == name]
    return found


def _move_after(doc: dict[str, Any], name: str, after: str) -> None:
    steps = _post(doc)["steps"]
    step = _named(doc, name)
    steps.remove(step)
    steps.insert(steps.index(_named(doc, after)) + 1, step)


def _problems(edit: Callable[[dict[str, Any]], None] | None, workflows: dict[str, Any] | None = None) -> list[str]:
    doc = _doc()
    if edit:
        edit(doc)
    return p.post_problems(doc, workflows if workflows is not None else _workflows())


def test_live_file_passes() -> None:
    assert p.post_problems(_doc(), _workflows()) == []


def _in_workflows(edit: Callable[[dict[str, Any]], None]) -> list[str]:
    flows = _workflows()
    edit(flows)
    return p.post_problems(_doc(), flows)


RED = [
    ("no-environment", "does not name exactly", lambda d: _post(d).pop("environment")),
    ("the-smoke-environment", "does not name exactly", lambda d: _post(d).update({"environment": "persona-verdict"})),
    ("checks-write", "permissions are not exactly", lambda d: _post(d)["permissions"].update({"checks": "write"})),
    ("no-id-token", "permissions are not exactly", lambda d: _post(d)["permissions"].pop("id-token")),
    ("needs-without-keyprep", "needs are not exactly", lambda d: _post(d).update({"needs": ["prepare", "run"]})),
    ("condition", "condition is not", lambda d: _post(d).update({"if": "${{ always() }}"})),
    ("job-continue-on-error", "sets continue-on-error", lambda d: _post(d).update({"continue-on-error": True})),
    ("container", "sets container", lambda d: _post(d).update({"container": "x"})),
    (
        "pip-from-an-index",
        "pip lines are not exactly",
        lambda d: _post(d)["steps"].append({"id": "pip", "run": "pip install requests"}),
    ),
    (
        "self-check-after-the-post-step",
        "steps are not exactly",
        lambda d: _move_after(d, "Check the egress filter", "Post the check runs"),
    ),
    (
        "floor-after-the-mkdir",
        "steps are not exactly",
        lambda d: _move_after(d, "Lay the egress floor", "Create the work directory"),
    ),
    (
        "final-check-missing",
        "steps are not exactly",
        lambda d: _post(d)["steps"].remove(_named(d, "Check the egress filter again")),
    ),
    (
        "final-check-not-last",
        "steps are not exactly",
        lambda d: _move_after(d, "Check the egress filter again", "Download the wheelhouse"),
    ),
    (
        "account-id-on-another-step",
        "post step env is not exactly",
        lambda d: _named(d, "Post the check runs")["env"].update({"X": "${{ secrets.AWS_ACCOUNT_ID }}"}),
    ),
    (
        "gh-token-as-a-secret",
        "post step env is not exactly",
        lambda d: _named(d, "Post the check runs")["env"].update({"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}),
    ),
    (
        "an-upload",
        "uploads",
        lambda d: _post(d)["steps"].append({"uses": "actions/upload-artifact@" + "b" * 40, "with": {"name": "x"}}),
    ),
    (
        "download-of-another-artifact",
        "downloads",
        lambda d: _named(d, "Download prepared inputs")["with"].update({"name": "persona-verdict-snapshot"}),
    ),
    (
        "continue-on-error-on-the-wheels-download",
        "continue-on-error",
        lambda d: _named(d, "Download the wheelhouse").update({"continue-on-error": True}),
    ),
    (
        "no-continue-on-error-on-results",
        "lacks continue-on-error",
        lambda d: _named(d, "Download verdict results").pop("continue-on-error"),
    ),
    (
        "configure-aws-credentials",
        "configure-aws-credentials",
        lambda d: _post(d)["steps"].append({"id": "c", "uses": "aws-actions/configure-aws-credentials@" + "a" * 40}),
    ),
    (
        "a-local-action",
        "uses the local action",
        lambda d: _post(d)["steps"].append({"uses": "./.github/actions/x"}),
    ),
    (
        "a-run-step-without-an-id",
        "run: step without an id",
        lambda d: _post(d)["steps"].append({"run": "echo hi"}),
    ),
    (
        "a-docker-command",
        "runs a docker command",
        lambda d: _post(d)["steps"].append({"id": "dk", "run": "docker run x"}),
    ),
    (
        "another-run-py-verb",
        "run.py verbs are not exactly",
        lambda d: _named(d, "Post the check runs").update({"run": k.RUN_PREFIX + "key-refusal"}),
    ),
    (
        "verb-list-without-the-floor",
        "steps are not exactly",
        lambda d: _post(d)["steps"].remove(_named(d, "Lay the egress floor")),
    ),
]


@pytest.mark.parametrize(("name", "expect", "edit"), RED, ids=[r[0] for r in RED])
def test_each_mutation_is_refused(name: str, expect: str, edit: Callable[[dict[str, Any]], None]) -> None:
    problems = _problems(edit)
    assert any(expect in problem for problem in problems), (name, problems)


def test_a_second_workflow_job_naming_the_app_environment_is_refused() -> None:
    def edit(flows: dict[str, Any]) -> None:
        flows["persona-verdict-override.yml"]["jobs"]["render"]["environment"] = p.ENVIRONMENT

    assert any("names the persona-verdict-app Environment" in x for x in _in_workflows(edit))


def test_another_job_in_the_verdict_workflow_naming_it_is_refused() -> None:
    def edit(flows: dict[str, Any]) -> None:
        flows["persona-verdict.yml"]["jobs"]["wake"]["environment"] = p.ENVIRONMENT

    assert any("job wake names" in x for x in _in_workflows(edit))


def test_the_app_role_name_in_another_workflow_is_refused() -> None:
    def edit(flows: dict[str, Any]) -> None:
        flows["persona-verdict-override.yml"]["jobs"]["render"]["steps"][0]["run"] = f"echo {p.APP_ROLE}"

    assert any("holds the verdict App role name" in x for x in _in_workflows(edit))


def test_no_post_job_is_no_problem_here() -> None:
    doc = _doc()
    del doc["jobs"]["post"]
    assert p.post_problems(doc, _workflows()) == []
