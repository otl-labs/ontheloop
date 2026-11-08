"""Predicate n of validate_github_settings_apply: the provider-mirror wiring (rec-5005)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from scripts.checks.ci_guards import _github_settings_mirror as m
from scripts.verify_ci_workflow import _load

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github" / "workflows" / "terraform-github-apply.yml"
_SCRIPT = (_ROOT / m.APPLY_SCRIPT_PATH).read_text(encoding="utf-8")


@pytest.fixture
def jobs() -> dict[str, Any]:
    return copy.deepcopy(_load(str(_WORKFLOW))["jobs"])


def _steps(jobs: dict[str, Any], jid: str) -> list[dict[str, Any]]:
    return jobs[jid]["steps"]


def _mirror(jobs: dict[str, Any], jid: str) -> dict[str, Any]:
    return next(s for s in _steps(jobs, jid) if s.get("id") == "mirror")


def test_real_workflow_and_script_are_green(jobs) -> None:
    assert m.check_mirror_wiring(jobs, _SCRIPT) == []
    assert m.check_mirror_wiring(jobs) == []


def _drop(jobs, jid) -> None:
    _steps(jobs, jid).remove(_mirror(jobs, jid))


def _before_credentials(jobs, jid) -> None:
    steps = _steps(jobs, jid)
    mirror = _mirror(jobs, jid)
    steps.remove(mirror)
    steps.insert(0, mirror)


def _after_terraform(jobs, jid) -> None:
    steps = _steps(jobs, jid)
    mirror = _mirror(jobs, jid)
    steps.remove(mirror)
    steps.append(mirror)


def _terraform_ahead(jobs, jid) -> None:
    steps = _steps(jobs, jid)
    steps.insert(steps.index(_mirror(jobs, jid)), {"name": "x", "run": "terraform version"})


def _other_run(jobs, jid) -> None:
    _mirror(jobs, jid)["run"] = "bash scripts/ci/terraform_github_apply_mirror.sh fetch --extra"


def _other_prefix(jobs, jid) -> None:
    _mirror(jobs, jid)["env"] = {"MIRROR_PREFIX": "s3://agent-platform-data-lake/tf-provider-mirror/kislerdm/"}


@pytest.mark.parametrize("jid", ["preview", "apply"])
@pytest.mark.parametrize(
    "mutate",
    [_drop, _before_credentials, _after_terraform, _terraform_ahead, _other_run, _other_prefix],
    ids=lambda f: f.__name__.strip("_"),
)
def test_n_each_job_fetches_the_mirror_before_init(jobs, jid, mutate) -> None:
    mutate(jobs, jid)
    assert [x for x in m.check_mirror_wiring(jobs, _SCRIPT) if x.startswith(f"n: {jid}:")] != []
    assert len(m.check_mirror_wiring(jobs, _SCRIPT)) == 1


def test_n_preview_mirror_after_the_app_token(jobs) -> None:
    steps = _steps(jobs, "preview")
    mirror = _mirror(jobs, "preview")
    steps.remove(mirror)
    steps.insert(next(i for i, s in enumerate(steps) if s.get("id") == "app_token") + 1, mirror)
    assert len(m.check_mirror_wiring(jobs, _SCRIPT)) == 1


def test_n_preview_plan_step_drops_the_classifier(jobs) -> None:
    plan = next(s for s in _steps(jobs, "preview") if s.get("id") == "plan")
    plan["run"] = plan["run"].replace(m.CLASSIFY, "true")
    assert len(m.check_mirror_wiring(jobs, _SCRIPT)) == 1


def test_n_classifier_after_the_failure_line(jobs) -> None:
    plan = next(s for s in _steps(jobs, "preview") if s.get("id") == "plan")
    plan["run"] = plan["run"].replace("TF_GITHUB_PREVIEW_FAILED init", "TF_GITHUB_PREVIEW_FAILED init; " + m.CLASSIFY)
    plan["run"] = plan["run"].replace('bash "$GITHUB_WORKSPACE/scripts/ci/' + m.CLASSIFY, "true #", 1)
    assert len(m.check_mirror_wiring(jobs, _SCRIPT)) == 1


def test_n_apply_script_drops_the_classifier(jobs) -> None:
    assert len(m.check_mirror_wiring(jobs, _SCRIPT.replace(m.CLASSIFY, "true"))) == 1
    assert len(m.check_mirror_wiring(jobs, "")) == 1
