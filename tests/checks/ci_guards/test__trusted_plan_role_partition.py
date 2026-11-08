"""Predicates (j) and (k) of the tf-trusted-plan fence, called directly (PLAN-trusted-plan-github-root PR 4)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.checks.ci_guards import _trusted_plan_role_partition as part

ARN = "arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/"
KEY = "${{ secrets.TF_GITHUB_PLAN_APP_PRIVATE_KEY }}"


def _assume(role: str) -> dict[str, Any]:
    return {"uses": "aws-actions/configure-aws-credentials@" + "a" * 40, "with": {"role-to-assume": ARN + role}}


def _wf(
    *steps: dict[str, Any], env_name: str | None = None, extra: dict[str, Any] | None = None, **top: Any
) -> dict[str, Any]:
    job: dict[str, Any] = {"steps": list(steps), **(extra or {})}
    if env_name:
        job["environment"] = env_name
    return {"jobs": {"plan": job}, **top}


def _github_role_wf(role: str) -> dict[str, Any]:
    return _wf(_assume(role), env_name=part.ENV)


J = part.check_role_partition
K = part.check_key_location


@pytest.mark.parametrize(
    "check, workflows, expect",
    [
        (J, {part.GITHUB_WORKFLOW: _github_role_wf(part.SHARED_ROLE)}, "j: terraform-github-plan.yml:plan"),
        (J, {part.GITHUB_WORKFLOW: _github_role_wf("agent-platform-github-ci-planner")}, "j: terraform-github-plan.yml:plan"),
        (J, {part.PERSONAL_WORKFLOW: _github_role_wf(part.GITHUB_ROLE)}, "j: terraform-plan-trusted.yml:plan"),
        (J, {"other.yml": _github_role_wf(part.GITHUB_ROLE)}, "j: other.yml:plan"),
        (J, {"other.yml": _github_role_wf(part.SHARED_ROLE)}, "j: other.yml:plan"),
        (K, {part.GITHUB_WORKFLOW: _wf({"run": "x", "env": {"K": KEY}})}, "k: "),
        (K, {"other.yml": _wf({"run": "x", "env": {"K": KEY}})}, "k: "),
        (K, {"other.yml": _wf({"run": "x"}, extra={"env": {"K": KEY}})}, "k: "),
        (K, {"other.yml": _wf({"run": "x"}, env={"K": KEY})}, "k: "),
        (K, {"other.yml": {"jobs": {"call": {"uses": "./.github/workflows/x.yml", "secrets": {"K": KEY}}}}}, "k: "),
        (
            K,
            {
                "other.yml": _wf(
                    {"run": "x"}, extra={"container": {"image": "i", "credentials": {"username": "u", "password": KEY}}}
                )
            },
            "k: ",
        ),
        (K, {"other.yml": _wf({"run": "x", "env": {"K": "${{ secrets['TF_GITHUB_PLAN_APP_PRIVATE_KEY'] }}"}})}, "k: "),
        (K, {"other.yml": _wf({"run": "x", "env": {"K": KEY}}, env_name="other-env")}, "k: "),
    ],
)
def test_role_partition_and_key_location_red_cases(check, workflows: dict, expect: str) -> None:
    assert any(line.startswith(expect) for line in check(workflows))


def test_green_cases_and_empty_failure_paths() -> None:
    header_only = {part.PERSONAL_WORKFLOW: _github_role_wf(part.SHARED_ROLE)}
    assert part.check_role_partition(header_only) == []
    assert part.check_role_partition({part.GITHUB_WORKFLOW: _github_role_wf(part.GITHUB_ROLE)}) == []
    assert part.check_role_partition({"x.yml": {"jobs": "bad"}, "y.yml": {"jobs": {"a": "bad"}}}) == []
    assert (
        part.check_role_partition(
            {"x.yml": _wf({"uses": "other/action@v1"}, {"uses": "aws-actions/configure-aws-credentials@v6"})}
        )
        == []
    )
    assert part.check_key_location({"x.yml": _wf({"run": "echo hi"}), "y.yml": _github_role_wf(part.GITHUB_ROLE)}) == []
    ok = _wf({"run": "x", "env": {"K": KEY}}, env_name=part.ENV)
    assert part.check_key_location({part.GITHUB_WORKFLOW: ok}) == []
    dict_env = {"jobs": {"p": {"environment": {"name": part.ENV}, "steps": [{"run": "x", "env": {"K": KEY}}]}}}
    assert part.check_key_location({"x.yml": dict_env}) == []
    assert part.check_key_location({"x.yml": {"jobs": {"p": {"steps": [{"run": "x"}]}}}}) == []
