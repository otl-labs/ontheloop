"""Tests for the SecureString aws_ssm_parameter coverage overrides (Decision 237, secret parameters)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.iam_tf._read_coverage import _scan_resources
from scripts.checks.iam_tf._secure_string_coverage import (
    check_secure_string_write_coverage,
    covers_own_name,
    is_secure_string,
    secure_string_read_missing,
    type_attr,
)
from scripts.checks.iam_tf.validate_ci_refresh_read_coverage import validate_ci_refresh_read_coverage
from tests.checks.iam_tf.validate_ci_refresh_read_coverage.conftest import (  # noqa: F401 -- fixture import
    _write_ci_refresh_read_fixture,
    synthetic_parity_exempt,
)


def _arn(name: str) -> str:
    return '"arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/' + name + '"'


def _stmt(actions: list[str], resources_raw: str) -> dict:
    return {"sid": None, "actions": actions, "resources_raw": resources_raw, "effect": "Allow"}


def test_is_secure_string() -> None:
    assert is_secure_string({"type": '"SecureString"'})
    assert is_secure_string({})
    assert is_secure_string({"type": "local.kind"})
    assert not is_secure_string({"type": '"String"'})


def test_covers_own_name_red_cases() -> None:
    assert not covers_own_name("p", "/secret/a", '["arn:aws:ssm:*:*:parameter/secret/other"]')
    assert not covers_own_name("p", "/secret/a", "local.arns")
    assert not covers_own_name("p", "/secret/a", '["arn:aws:ssm:*:*:parameter/agent-platform/*"]')
    assert not covers_own_name("p", None, '["arn:aws:ssm:*:*:parameter/secret/a"]')


def test_wildcard_entry_never_covers_a_secret_parameter() -> None:
    assert not covers_own_name("p", "/secret/a", '["arn:aws:ssm:*:*:parameter/secret/*"]')
    assert covers_own_name("p", "/agent-platform/a", '["arn:aws:ssm:*:*:parameter/agent-platform/*"]')


def test_nested_tags_type_does_not_hide_a_securestring() -> None:
    body = 'name = "/secret/a"\ntags = {\n  type = "String"\n}\ntype = "SecureString"'
    assert type_attr("aws_ssm_parameter", body) == {"type": '"SecureString"'}
    assert type_attr("aws_lambda_function", body) == {}
    assert type_attr("aws_ssm_parameter", 'name = "/secret/a"') == {}


def test_covers_own_name_green_cases() -> None:
    assert covers_own_name("p", "/secret/a", "[" + _arn("a") + "]")
    assert covers_own_name("p", "/secret/a", "[aws_ssm_parameter.p.arn]")


def test_read_side_red_and_green() -> None:
    own = "[" + _arn("a") + "]"
    assert secure_string_read_missing("p", "/secret/a", [_stmt(["ssm:Describe*"], own)]) == [
        "ssm:GetParameter",
        "ssm:ListTagsForResource",
    ]
    assert secure_string_read_missing("p", "/secret/a", [_stmt(["ssm:GetParameter"], "[" + _arn("b") + "]")])
    both = [_stmt(["ssm:GetParameter", "ssm:ListTagsForResource"], own)]
    assert secure_string_read_missing("p", "/secret/a", both) == []
    assert secure_string_read_missing("p", "/secret/a", [_stmt(["ssm:Get*", "ssm:List*"], own)]) == []


def _scan(tmp_path: Path, body: str):
    d = tmp_path / "personal"
    d.mkdir()
    (d / "p.tf").write_text(body, encoding="utf-8")
    return _scan_resources(d)


def test_write_side_red_and_green(tmp_path: Path) -> None:
    resources, locals_map, attr_index = _scan(
        tmp_path, 'resource "aws_ssm_parameter" "dsn" {\n  name = "/secret/a"\n  type = "SecureString"\n}\n'
    )
    own = "[" + _arn("a") + "]"
    marker_only = [_stmt(["ssm:PutParameter", "ssm:DeleteParameter"], '["arn:aws:ssm:*:*:parameter/agent-platform/*"]')]
    failed: list[str] = []
    assert check_secure_string_write_coverage(marker_only, resources, locals_map, attr_index, failed, "k:") == 1
    assert len(failed) == 1 and "ssm:PutParameter" in failed[0]

    no_tags = [_stmt(["ssm:PutParameter", "ssm:DeleteParameter"], own)]
    failed = []
    check_secure_string_write_coverage(no_tags, resources, locals_map, attr_index, failed, "k:")
    assert len(failed) == 1 and "ssm:AddTagsToResource" in failed[0] and "ssm:PutParameter" not in failed[0].split("lacks")[1]

    full = [_stmt(["ssm:PutParameter", "ssm:DeleteParameter", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource"], own)]
    failed = []
    check_secure_string_write_coverage(full, resources, locals_map, attr_index, failed, "k:")
    assert failed == []


def test_plain_string_parameter_is_not_asserted(tmp_path: Path) -> None:
    resources, locals_map, attr_index = _scan(
        tmp_path, 'resource "aws_ssm_parameter" "u" {\n  name = "/agent-platform/u"\n  type = "String"\n}\n'
    )
    failed: list[str] = []
    assert check_secure_string_write_coverage([], resources, locals_map, attr_index, failed, "k:") == 0
    assert failed == []


_SECURE_RESOURCE = 'resource "aws_ssm_parameter" "sec" {\n  name = "/secret/a"\n  type = "SecureString"\n  value = "x"\n}\n'
_PLANNER_DESCRIBE = """
data "aws_iam_policy_document" "ci_full_refresh_read" {
  statement {
    sid       = "LambdaRead"
    effect    = "Allow"
    actions   = ["lambda:Get*", "lambda:List*"]
    resources = ["arn:aws:lambda:eu-west-2:1234567890:function:agent-platform-known-fn"]
  }
  statement {
    sid       = "SsmDescribe"
    effect    = "Allow"
    actions   = ["ssm:Describe*"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_ci_planner" {
  name   = "test-planner"
  role   = "test-planner-role"
  policy = data.aws_iam_policy_document.github_ci_planner.json
}

data "aws_iam_policy_document" "github_ci_planner" {
  source_policy_documents = [data.aws_iam_policy_document.ci_full_refresh_read.json]
}
"""


@pytest.mark.usefixtures("synthetic_parity_exempt")
def test_end_to_end_secure_string_red_on_both_sides(tmp_path: Path) -> None:
    resources = (
        'resource "aws_lambda_function" "known_fn" {\n  function_name = "agent-platform-known-fn"\n}\n' + _SECURE_RESOURCE
    )
    _write_ci_refresh_read_fixture(tmp_path, oidc_body=_PLANNER_DESCRIBE, resources_body=resources)
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path):
        validate_ci_refresh_read_coverage(failed)
    joined = "\n".join(failed)
    assert "SecureString aws_ssm_parameter 'sec'" in joined and "ssm:PutParameter" in joined
    assert "planner role policy" in joined and "ssm:GetParameter" in joined
