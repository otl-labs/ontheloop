"""Tests for validate_secret_parameter_grants (Decision 237 cl.2): no pattern value grant covers /secret/."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.iam_tf.validate_secret_parameter_grants import validate_secret_parameter_grants

PREFIX = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter"


def _json_stmt(actions: str, resource: str, key: str = "Resource", sid: str = "S") -> str:
    return (
        'resource "aws_iam_role_policy" "p" {\n  policy = jsonencode({\n    Version = "2012-10-17"\n    Statement = [{\n'
        f'      Sid = "{sid}"\n      Effect = "Allow"\n      Action = {actions}\n'
        f"      {key} = {resource}\n    }}]\n  }})\n}}\n"
    )


def _hcl_stmt(actions: str, resources: str, key: str = "resources") -> str:
    return (
        'data "aws_iam_policy_document" "d" {\n  statement {\n    sid = "S"\n'
        f"    actions = {actions}\n    {key} = {resources}\n  }}\n}}\n"
    )


def _run(tmp_path: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        p = tmp_path / "terraform" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    failed: list[str] = []
    validate_secret_parameter_grants(failed, root=tmp_path)
    return failed


RED = {
    "get_star_on_parameter_star": _json_stmt('["ssm:Get*"]', f'["{PREFIX}/*"]'),
    "ssm_star_on_star": _json_stmt('["ssm:*"]', '["*"]'),
    "get_on_s_star": _json_stmt('["ssm:GetParameter"]', f'["{PREFIX}/s*"]'),
    "put_on_secret_star": _json_stmt('"ssm:PutParameter"', f'"{PREFIX}/secret/*"'),
    "by_path_on_secret": _json_stmt('["ssm:GetParametersByPath"]', f'["{PREFIX}/secret"]'),
    "by_path_on_parameter_root": _json_stmt('["ssm:GetParametersByPath"]', f'["{PREFIX}/"]'),
    "not_resource": _json_stmt('["ssm:GetParameter"]', f'["{PREFIX}/agent-platform/*"]', key="NotResource"),
    "not_action": _json_stmt('["ssm:GetParameter"]', '["*"]', key="Resource").replace("Action =", "NotAction ="),
    "bare_local_resource": _json_stmt('["ssm:GetParameter"]', "local.some_arns"),
    "hcl_style": _hcl_stmt('["ssm:GetParameter"]', f'["{PREFIX}/*"]'),
    "nonliteral_action": _json_stmt("local.actions", f'["{PREFIX}/agent-platform/*"]'),
    "hcl_not_resources": _hcl_stmt('["ssm:GetParameter"]', "[]", key="not_resources"),
}


@pytest.mark.parametrize("name", sorted(RED))
def test_red_cases(tmp_path: Path, name: str) -> None:
    failed = _run(tmp_path, {"personal/x.tf": RED[name]})
    assert len(failed) == 1, failed
    assert "Decision 237" in failed[0]


GREEN = {
    "agent_platform_star": _json_stmt('["ssm:Get*"]', f'["{PREFIX}/agent-platform/*"]'),
    "literal_secret": _json_stmt('["ssm:GetParameter"]', f'["{PREFIX}/secret/neon-api-key"]'),
    "describe_on_star": _json_stmt('["ssm:DescribeParameters"]', '["*"]'),
    "tags_on_parameter_star": _json_stmt('["ssm:ListTagsForResource"]', f'["{PREFIX}/*"]'),
    "hcl_literal_secret": _hcl_stmt('["ssm:GetParameter"]', f'["{PREFIX}/secret/ducklake-neon-catalog-dsn"]'),
}


@pytest.mark.parametrize("name", sorted(GREEN))
def test_green_controls(tmp_path: Path, name: str) -> None:
    assert _run(tmp_path, {"personal/x.tf": GREEN[name]}) == []


def test_deny_statement_is_ignored(tmp_path: Path) -> None:
    text = _json_stmt('["ssm:*"]', '["*"]').replace('Effect = "Allow"', 'Effect = "Deny"')
    assert _run(tmp_path, {"personal/x.tf": text}) == []


def test_boundary_ceiling_statement_excepted_by_sid_only(tmp_path: Path) -> None:
    ceiling = _json_stmt('["ssm:*"]', '["*"]', sid="DataPlaneAllow")
    assert _run(tmp_path, {"bootstrap/github_ci_apply_boundary.tf": ceiling}) == []
    assert len(_run(tmp_path, {"bootstrap/github_ci_apply_boundary.tf": RED["ssm_star_on_star"]})) == 1


@pytest.mark.parametrize("pattern", ["parameter/*/neon-api-key", "parameter/s*/neon-api-key", "parameter/*-api-key"])
def test_name_shaped_patterns_reaching_a_real_secret_fail(tmp_path: Path, pattern: str) -> None:
    assert (
        len(
            _run(
                tmp_path, {"personal/x.tf": _json_stmt('["ssm:GetParameter"]', f'["{PREFIX[: -len("parameter")]}{pattern}"]')}
            )
        )
        == 1
    )


def test_trailing_comment_brace_and_unbalanced_file(tmp_path: Path) -> None:
    ok = _json_stmt('["ssm:GetParameter"]', f'["{PREFIX}/agent-platform/x"]') + "# note {\n"
    ok = ok.replace('Sid = "S"', 'Sid = "S" # trailing {')
    assert _run(tmp_path, {"personal/x.tf": ok}) == []
    failed = _run(tmp_path, {"personal/y.tf": 'resource "x" "y" {\n'})
    assert len(failed) == 1 and "unbalanced" in failed[0]


def test_admin_statement_excepted_by_sid_only(tmp_path: Path) -> None:
    admin = _json_stmt('["ssm:PutParameter"]', f'["{PREFIX}/secret/*"]', sid="SecretParameterAdmin")
    assert _run(tmp_path, {"bootstrap/platform_admin_policies.tf": admin}) == []
    other = _json_stmt('["ssm:PutParameter"]', f'["{PREFIX}/secret/*"]', sid="Other")
    assert len(_run(tmp_path, {"bootstrap/platform_admin_policies.tf": other})) == 1


def test_real_tree_passes() -> None:
    failed: list[str] = []
    validate_secret_parameter_grants(failed)
    assert failed == [], failed


def test_registered_in_pre_and_full_sequence() -> None:
    name = "validate_secret_parameter_grants"
    assert name in {s.name for s in registry.pre_sequence() if s.kind == "check"}
    assert name in {s.name for s in registry.full_sequence() if s.kind == "check"}


def test_missing_roots_skip(tmp_path: Path) -> None:
    failed: list[str] = []
    validate_secret_parameter_grants(failed, root=tmp_path)
    assert failed == []
