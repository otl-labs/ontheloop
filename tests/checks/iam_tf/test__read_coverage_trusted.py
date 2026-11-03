"""The trusted plan role is registered as plan-capable in the refresh-read coverage gate (S3 PR 3, rec-4637)."""

from __future__ import annotations

from pathlib import Path

from scripts.checks.iam_tf._read_coverage import (
    ROLE_PLANNER,
    ROLE_TRUSTED_PLAN,
    _check_resource,
    _read_root_text,
    _resolve_role_statements,
    _scan_resources,
)

ROOT = Path(__file__).resolve().parents[3]
PERSONAL = ROOT / "terraform" / "personal"

_TRUSTED_TF = """
data "aws_iam_policy_document" "github_ci_trusted_plan" {
  statement {
    sid       = "LambdaRead"
    effect    = "Allow"
    actions   = ["lambda:Get*"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_ci_trusted_plan" {
  role   = "x"
  policy = data.aws_iam_policy_document.github_ci_trusted_plan.json
}

data "aws_iam_policy_document" "github_ci_planner" {
  statement {
    sid       = "LambdaRead"
    effect    = "Allow"
    actions   = ["lambda:Get*"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_ci_planner" {
  role   = "y"
  policy = data.aws_iam_policy_document.github_ci_planner.json
}
"""


def test_real_tree_resolves_the_trusted_role() -> None:
    result = _resolve_role_statements(_read_root_text(PERSONAL))
    assert result is not None
    assert set(result) == {ROLE_PLANNER, ROLE_TRUSTED_PLAN}
    assert result[ROLE_TRUSTED_PLAN]


def test_real_tree_resources_are_covered_for_the_trusted_role() -> None:
    statements = _resolve_role_statements(_read_root_text(PERSONAL))
    assert statements is not None
    resources, locals_map, attr_index = _scan_resources(PERSONAL)
    assert resources
    findings: list[str] = []
    for rtype, rname, fname in resources:
        found, _ = _check_resource(
            rtype, rname, fname, locals_map, attr_index, {ROLE_TRUSTED_PLAN: statements[ROLE_TRUSTED_PLAN]}, "k"
        )
        findings.extend(found)
    assert findings == []


def test_synthetic_trusted_policy_missing_a_refresh_read_is_red() -> None:
    statements = _resolve_role_statements(_TRUSTED_TF)
    assert statements is not None and set(statements) == {ROLE_PLANNER, ROLE_TRUSTED_PLAN}
    attr_index = {("aws_lambda_function", "fn"): {"function_name": '"agent-platform-fn"'}}
    found, checked = _check_resource(
        "aws_lambda_function",
        "fn",
        "x.tf",
        {},
        attr_index,
        {ROLE_TRUSTED_PLAN: [{"actions": ["s3:GetObject"], "resources_raw": '"*"'}]},
        "k",
    )
    assert checked and any(ROLE_TRUSTED_PLAN in f for f in found)
