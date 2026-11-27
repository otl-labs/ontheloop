"""The keyed-job pins: each mutation of the live persona-verdict.yml is refused; the live file passes."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.ci_guards import _persona_verdict_keyed as k
from scripts.checks.ci_guards import validate_persona_verdict_chain as v
from scripts.llm import tier_selection

LIVE = Path(_common.ROOT)


def _doc() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load((LIVE / v.VERDICT).read_text(encoding="utf-8")))


def _evidence() -> dict[str, Any]:
    return yaml.safe_load((LIVE / v.EVIDENCE).read_text(encoding="utf-8"))


def _named(job: dict[str, Any], name: str) -> dict[str, Any]:
    (found,) = [s for s in job["steps"] if s.get("name") == name]
    return found


def _problems(edit: Callable[[dict[str, Any]], None] | None) -> list[str]:
    doc = _doc()
    if edit:
        edit(doc)
    return v.verdict_problems(doc, _evidence())


def test_live_file_passes() -> None:
    assert k.keyed_problems(_doc()) == []
    assert _problems(None) == []


def test_tier_roles_equal_the_committed_tier_selection_roles() -> None:
    assert set(k.TIER_ROLES) == set(tier_selection._ROLES)


def _drop_step(job: str, name: str) -> Callable[[dict[str, Any]], None]:
    return lambda d: d["jobs"][job]["steps"].remove(_named(d["jobs"][job], name))


def _step_update(job: str, name: str, **kw: Any) -> Callable[[dict[str, Any]], None]:
    return lambda d: _named(d["jobs"][job], name).update(kw)


def _if(job: str, text: str) -> Callable[[dict[str, Any]], None]:
    return lambda d: d["jobs"][job].update({"if": text})


def _without(conjunct: str) -> str:
    return k.CONDITIONS["smoke"].replace(f" && {conjunct}", "").replace(f"{conjunct} && ", "")


RED = [
    ("smoke-without-environment", "persona-verdict Environment", lambda d: d["jobs"]["smoke"].pop("environment")),
    (
        "refusal-with-environment",
        "names an Environment",
        lambda d: d["jobs"]["refusal"].update({"environment": "persona-verdict"}),
    ),
    (
        "keyprep-with-environment",
        "names an Environment",
        lambda d: d["jobs"]["keyprep"].update({"environment": "persona-verdict"}),
    ),
    ("wake-id-token", "holds id-token: write", lambda d: d["jobs"]["wake"]["permissions"].update({"id-token": "write"})),
    ("keyprep-id-token", "holds id-token: write", lambda d: d["jobs"]["keyprep"]["permissions"].update({"id-token": "write"})),
    ("no-keyprep-conjunct-smoke", "condition is not", _if("smoke", "${{ " + k.KEYED_IF + " }}")),
    ("no-keyprep-conjunct-refusal", "condition is not", _if("refusal", "${{ " + k.KEYED_IF + " }}")),
    ("keyprep-with-the-keyed-condition", "condition is not", _if("keyprep", "${{ " + k.KEYED_IF + " }}")),
    (
        "no-fixture-conjunct",
        "condition is not",
        _if("smoke", "${{ " + _without("needs.prepare.outputs.fixture == 'true'") + " }}"),
    ),
    (
        "no-allowlisted-conjunct",
        "condition is not",
        _if("refusal", "${{ " + _without("needs.prepare.outputs.allowlisted == 'true'") + " }}"),
    ),
    (
        "no-pull-request-conjunct",
        "condition is not",
        _if("smoke", "${{ " + _without("github.event.workflow_run.event == 'pull_request'") + " }}"),
    ),
    (
        "run-reading-fixture",
        "run job condition is not",
        _if("run", "${{ " + k.KEYED_CONDITION + " }}"),
    ),
    (
        "smoke-reading-keyed",
        "condition is not",
        _if("smoke", "${{ " + k.CONDITIONS["run"] + " }}"),
    ),
    (
        "refusal-reading-keyed",
        "condition is not",
        _if("refusal", "${{ " + k.CONDITIONS["run"] + " }}"),
    ),
    (
        "run-without-keyed-conjunct",
        "run job condition is not",
        _if("run", "${{ " + k.CONDITIONS["run"].replace(" && needs.prepare.outputs.keyed == 'true'", "") + " }}"),
    ),
    (
        "another-secret",
        "other than secrets.GITHUB_TOKEN",
        _step_update(
            "smoke",
            "Smoke the primary role",
            env={
                "AWS_ACCOUNT_ID": k.ACCOUNT_ENV,
                "PERSONA_AWS_REGION": k.REGION_ENV,
                "PERSONA_SMOKE_ROLE": "primary",
                "PERSONA_SNAPSHOT": v.SNAPSHOT,
                "X": "${{ secrets.OTHER }}",
            },
        ),
    ),
    (
        "account-id-before-the-self-check",
        "secrets.AWS_ACCOUNT_ID appears outside",
        lambda d: _named(d["jobs"]["smoke"], "Create the work directory").update({"env": {"A": k.ACCOUNT_ENV}}),
    ),
    (
        "account-id-before-the-post-self-check",
        "secrets.AWS_ACCOUNT_ID appears outside",
        lambda d: _named(d["jobs"]["post"], "Create the work directory").update({"env": {"A": k.ACCOUNT_ENV}}),
    ),
    (
        "account-id-in-the-install-step",
        "secrets.AWS_ACCOUNT_ID appears outside",
        _step_update("smoke", "Install the wheelhouse offline", env={"A": k.ACCOUNT_ENV}),
    ),
    (
        "configure-aws-credentials",
        "configure-aws-credentials",
        lambda d: d["jobs"]["smoke"]["steps"].append({"id": "c", "uses": "aws-actions/configure-aws-credentials@" + "a" * 40}),
    ),
    (
        "pip-install-without-no-index",
        "pip lines are not exactly",
        lambda d: _named(d["jobs"]["smoke"], "Install the wheelhouse offline").update(
            {"run": k.PIP_INSTALL.replace("--no-index ", "")}
        ),
    ),
    (
        "pip-install-without-require-hashes",
        "pip lines are not exactly",
        lambda d: _named(d["jobs"]["smoke"], "Install the wheelhouse offline").update(
            {"run": k.PIP_INSTALL.replace("--require-hashes ", "")}
        ),
    ),
    (
        "pip-in-refusal",
        "pip lines are not exactly",
        lambda d: d["jobs"]["refusal"]["steps"].append({"id": "p", "run": "pip install x"}),
    ),
    (
        "upload-in-smoke",
        "uploads",
        lambda d: d["jobs"]["smoke"]["steps"].append({"uses": "actions/upload-artifact@" + "b" * 40, "with": {"name": "x"}}),
    ),
    (
        "download-of-another-artifact",
        "downloads",
        lambda d: _named(d["jobs"]["smoke"], "Download the wheelhouse")["with"].update({"name": "persona-verdict-prepared"}),
    ),
    (
        "keyprep-runs-a-verb",
        "runs python or a run.py verb",
        lambda d: d["jobs"]["keyprep"]["steps"].append({"id": "x", "run": v.RUN_PREFIX + "key-refusal"}),
    ),
    (
        "missing-tier-role-step",
        "run.py steps are not exactly",
        _drop_step("smoke", "Smoke the warm_fetched_escape_hatch role"),
    ),
    (
        "wrong-tier-role",
        "PERSONA_SMOKE_ROLE values",
        lambda d: _named(d["jobs"]["smoke"], "Smoke the primary role")["env"].update({"PERSONA_SMOKE_ROLE": "tier_3"}),
    ),
    (
        "keyprep-checkout-with-ref",
        "checkout is not exactly",
        lambda d: d["jobs"]["keyprep"]["steps"][0]["with"].update({"ref": "x"}),
    ),
    (
        "keyprep-checkout-with-path",
        "checkout is not exactly",
        lambda d: d["jobs"]["keyprep"]["steps"][0]["with"].update({"path": "x"}),
    ),
    (
        "keyprep-checkout-persists-credentials",
        "checkout is not exactly",
        lambda d: d["jobs"]["keyprep"]["steps"][0]["with"].update({"persist-credentials": True}),
    ),
    (
        "region-from-elsewhere",
        "PERSONA_AWS_REGION is not exactly",
        lambda d: _named(d["jobs"]["smoke"], "Smoke the primary role")["env"].update(
            {"PERSONA_AWS_REGION": "${{ needs.prepare.outputs.region }}"}
        ),
    ),
    (
        "continue-on-error-on-a-keyed-step",
        "continue-on-error",
        _step_update("smoke", "Smoke the primary role", **{"continue-on-error": True}),
    ),
    (
        "continue-on-error-on-a-keyed-job",
        "sets continue-on-error",
        lambda d: d["jobs"]["refusal"].update({"continue-on-error": True}),
    ),
    ("container-in-a-keyed-job", "sets container", lambda d: d["jobs"]["smoke"].update({"container": "x"})),
    ("services-in-a-keyed-job", "sets services", lambda d: d["jobs"]["smoke"].update({"services": {}})),
    (
        "docker-command-in-a-keyed-job",
        "runs a docker command",
        lambda d: d["jobs"]["refusal"]["steps"].append({"id": "d", "run": "docker ps"}),
    ),
    (
        "aws-region-action-in-refusal",
        "uses the local action",
        lambda d: d["jobs"]["refusal"]["steps"].insert(1, {"id": "r", "uses": "./.github/actions/aws-region"}),
    ),
    (
        "other-local-action-in-keyprep",
        "uses the local action",
        _step_update("keyprep", "Resolve the project region", uses="./.github/actions/other"),
    ),
    ("run-step-without-an-id", "without an id", lambda d: d["jobs"]["keyprep"]["steps"][3].pop("id")),
    ("keyprep-needs-changed", "needs are not exactly", lambda d: d["jobs"]["keyprep"].update({"needs": ["prepare", "run"]})),
    (
        "smoke-permissions-widened",
        "permissions are not exactly",
        lambda d: d["jobs"]["smoke"]["permissions"].update({"actions": "read"}),
    ),
]


RED += [
    (
        "floor-missing",
        "run.py steps are not exactly",
        lambda d: d["jobs"]["smoke"]["steps"].remove(_named(d["jobs"]["smoke"], "Lay the egress floor")),
    ),
    (
        "final-check-missing",
        "run.py steps are not exactly",
        lambda d: d["jobs"]["refusal"]["steps"].remove(_named(d["jobs"]["refusal"], "Check the egress filter again")),
    ),
    (
        "final-check-without-always",
        "final self-check must carry",
        lambda d: _named(d["jobs"]["smoke"], "Check the egress filter again").pop("if"),
    ),
    (
        "floor-with-env",
        "floor or first self-check step has keys",
        lambda d: _named(d["jobs"]["smoke"], "Lay the egress floor").update({"env": {"X": "1"}}),
    ),
    (
        "account-id-not-the-secret",
        "AWS_ACCOUNT_ID is not exactly",
        lambda d: _named(d["jobs"]["smoke"], "Smoke the primary role")["env"].update({"AWS_ACCOUNT_ID": "x"}),
    ),
    (
        "secret-in-a-job-level-env",
        "holds a secrets reference outside its steps",
        lambda d: d["jobs"]["keyprep"].update({"env": {"X": "${{ secrets.OTHER }}"}}),
    ),
]


@pytest.mark.parametrize(("name", "expect", "edit"), RED, ids=[r[0] for r in RED])
def test_each_mutation_is_refused(name: str, expect: str, edit: Callable[[dict[str, Any]], None]) -> None:
    assert any(expect in p for p in _problems(edit)), (name, _problems(edit))


def test_the_group_exists_together_or_not_at_all() -> None:
    doc = _doc()
    for name in k.KEYED_JOBS:
        doc["jobs"].pop(name)
    assert k.keyed_problems(doc) == []
    doc = _doc()
    doc["jobs"].pop("smoke")
    assert any("together or not at all" in p for p in k.keyed_problems(doc))


def _run_named(d: dict[str, Any], name: str) -> dict[str, Any]:
    return _named(d["jobs"]["run"], name)


RUN_RED = [
    (
        "persona-extra-key",
        "persona step keys are not exactly",
        lambda d: _run_named(d, "Run the fixture persona").update({"shell": "bash"}),
    ),
    (
        "persona-wd",
        "persona step keys are not exactly",
        lambda d: _run_named(d, "Run the fixture persona").update({"working-directory": "/tmp"}),
    ),
    (
        "persona-env",
        "persona step env is not exactly",
        lambda d: _run_named(d, "Run the fixture persona")["env"].update({"X": "y"}),
    ),
    ("final-no-if", "final self-check must carry", lambda d: d["jobs"]["run"]["steps"][-1].pop("if")),
    (
        "drop-step",
        "are not exactly the 14 canonical steps",
        lambda d: d["jobs"]["run"]["steps"].remove(_run_named(d, "Set up Python 3.12")),
    ),
    (
        "uses-unpinned",
        "differs from its canonical mapping",
        lambda d: _run_named(d, "Check out main").update({"uses": "actions/checkout@v7"}),
    ),
    (
        "uses-other",
        "differs from its canonical mapping",
        lambda d: _run_named(d, "Set up Python 3.12").update({"uses": "actions/cache@" + "a" * 40}),
    ),
    (
        "mkdir-run",
        "differs from its canonical mapping",
        lambda d: _run_named(d, "Create the work directory").update({"run": "mkdir -p /tmp/x"}),
    ),
    (
        "with-extra",
        "differs from its canonical mapping",
        lambda d: _run_named(d, "Download prepared inputs")["with"].update({"run-id": "1"}),
    ),
    (
        "with-value",
        "differs from its canonical mapping",
        lambda d: _run_named(d, "Set up Python 3.12")["with"].update({"python-version": "3.11"}),
    ),
    (
        "id-changed",
        "differs from its canonical mapping",
        lambda d: _run_named(d, "Create the work directory").update({"id": "zz"}),
    ),
    (
        "floor-extra-key",
        "keys beyond name, id and run",
        lambda d: _run_named(d, "Lay the container egress floor").update({"if": "always()"}),
    ),
    (
        "reorder",
        "differs from its canonical mapping",
        lambda d: d["jobs"]["run"]["steps"].insert(5, d["jobs"]["run"]["steps"].pop(3)),
    ),
    (
        "extra-run-py-step",
        "run.py steps are not exactly",
        lambda d: d["jobs"]["run"]["steps"].insert(
            3, {"id": "s", "run": v.RUN_PREFIX + "post", "working-directory": v.WORKDIR}
        ),
    ),
    ("top-env", "a top-level env:", lambda d: d.update({"env": {"A": "b"}})),
    ("top-defaults", "a top-level defaults:", lambda d: d.update({"defaults": {"run": {"shell": "bash"}}})),
    ("job-env-run", "run sets env", lambda d: d["jobs"]["run"].update({"env": {"BASH_ENV": "x"}})),
]


@pytest.mark.parametrize(("name", "expect", "edit"), RUN_RED, ids=[r[0] for r in RUN_RED])
def test_each_run_job_mutation_is_refused(name: str, expect: str, edit: Callable[[dict[str, Any]], None]) -> None:
    assert any(expect in p for p in _problems(edit)), (name, _problems(edit))


def test_a_job_outside_the_pip_map_reports_a_problem() -> None:
    assert k._pip_rules("other", {"steps": []}) == [f"{v.VERDICT}: keyed job other has no pinned pip set"]


def _fb(edit: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    return lambda d: edit(d["jobs"]["run-fallback"])


RED_FALLBACK = [
    (
        "run-upload-name",
        "uploads",
        _fb(lambda j: _named(j, "Upload fallback verdict results")["with"].update({"name": k.RESULTS})),
    ),
    (
        "extra-step",
        "steps are not exactly",
        _fb(lambda j: j["steps"].append({"name": "x", "id": "run-fallback-x", "run": "true"})),
    ),
    (
        "step-id-prefix",
        "differs from its canonical mapping",
        _fb(lambda j: _named(j, "Create the work directory").update({"id": "run-mkdir"})),
    ),
    (
        "run-role-literal",
        "persona step env",
        lambda d: _named(d["jobs"]["run"], "Run the fixture persona")["env"].update({"PERSONA_ROLE": "x"}),
    ),
]


def test_run_fallback_step_list_is_run_s() -> None:
    assert _problems(None) == []
    assert k._canonical_run_steps("run-fallback")[2]["id"] == "run-fallback-floor"
    for _name, message, edit in RED_FALLBACK:
        assert any(message in p for p in _problems(edit)), (_name, _problems(edit))


def test_run_reads_keyed_and_smoke_and_refusal_read_fixture() -> None:
    doc = _doc()
    assert "outputs.keyed == 'true'" in str(doc["jobs"]["run"]["if"])
    assert "outputs.fixture" not in str(doc["jobs"]["run"]["if"])
    for name in ("smoke", "refusal"):
        assert "outputs.fixture == 'true'" in str(doc["jobs"][name]["if"])
        assert "outputs.keyed" not in str(doc["jobs"][name]["if"])
