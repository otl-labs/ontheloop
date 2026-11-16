"""Decision 255 standing guard: terraform/bootstrap declares one IAM-authorised REST API in front of the four DuckLake verbs.

Credit-free text tests over agent_ducklake_api.tf and the boundary (plan agent-ducklake-rest-api). Each predicate returns a
problem list so its red case drives the real predicate on mutated real text. The module names the files it reads as
quoted literals ("agent_ducklake_api.tf", "github_ci_apply_boundary.tf", "platform_dev_policies.tf") so --pre's
data-edge channel selects it when they change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.checks.iam_tf._agent_ducklake_api_predicates import (
    _INTEGRATION_HASH,
    _METHOD_HASH,
    _PREDICATES,
    boundary_problems,
    integrations_problems,
    log_role_problems,
    methods_problems,
    permissions_problems,
    policy_problems,
    redeploy_problems,
    route_url_problems,
    routes_problems,
    stage_problems,
)
from tests.fixtures.terraform_hcl_blocks import tf_dir_file_texts

_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = _ROOT / "terraform" / "bootstrap"


def _texts() -> dict[str, str]:
    texts = tf_dir_file_texts(_BOOTSTRAP)
    return {"api": texts["agent_ducklake_api.tf"], "boundary": texts["github_ci_apply_boundary.tf"]}


def _real() -> dict[str, Any]:
    t: dict[str, Any] = dict(_texts())
    t["extra"] = tf_dir_file_texts(_ROOT / "terraform" / "personal")
    t["bootstrap"] = {n: x for n, x in tf_dir_file_texts(_BOOTSTRAP).items() if n != "agent_ducklake_api.tf"}
    return t


def test_api_routes_are_the_four_verbs() -> None:
    assert routes_problems(_real()) == []


def test_api_methods_are_iam_post() -> None:
    assert methods_problems(_real()) == []


def test_api_integrations_are_proxy_without_credentials() -> None:
    assert integrations_problems(_real()) == []


def test_api_permissions_name_exact_routes() -> None:
    assert permissions_problems(_real()) == []


def test_api_policy_admits_only_the_session_user() -> None:
    assert policy_problems(_real()) == []


def test_api_stage_logs_no_bodies() -> None:
    assert stage_problems(_real()) == []


def test_api_redeploys_on_change() -> None:
    assert redeploy_problems(_real()) == []


def test_api_log_role_is_scoped() -> None:
    assert log_role_problems(_real()) == []


def test_api_route_urls_are_parameters() -> None:
    assert route_url_problems(_real()) == []


def test_boundary_denies_api_gateway_writes() -> None:
    assert boundary_problems(_real()) == []


_PERM = 'resource "aws_lambda_permission" "agent_ducklake_route" {'
_POLICY_COND = "StringNotEquals = {"
_B = "boundary"
_PLUS_BOOTSTRAP = "+bootstrap"
_ACCOUNT = 'resource "aws_api_gateway_account" "agent_ducklake" {'
_LOG_RESOURCE = '        Resource = "*"\n'
_ROLE_DESC = '  description = "API Gateway account CloudWatch role'
_A = "api"
# (label, predicate, file key, old, new, expected problem substring)
_MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "fifth route",
        "routes",
        _A,
        '"telemetry/writer" = local',
        '"telemetry/extra" = local.x\n      "telemetry/writer" = local',
        "sandbox map",
    ),
    (
        "route mapped to another function",
        "routes",
        _A,
        '"ops/writer"       = local.ducklake_writer_function',
        '"ops/writer"       = local.ducklake_reader_function',
        "sandbox map",
    ),
    ("sit route", "routes", _A, "sit = {}", 'sit = { "ops/reader" = local.ducklake_reader_function }', "sit map"),
    ("literal count", "routes", _A, "count = local.ducklake_api_count", "count = 1", "count is not"),
    ("authorization NONE", "methods", _A, 'authorization = "AWS_IAM"', 'authorization = "NONE"', "AWS_IAM"),
    (
        "integration credentials",
        "integrations",
        _A,
        '  type                    = "AWS_PROXY"',
        '  credentials = "x"\n  type                    = "AWS_PROXY"',
        "credentials",
    ),
    (
        "timeout above quota",
        "integrations",
        _A,
        "ducklake_api_timeout_ms = 120000",
        "ducklake_api_timeout_ms = 130000",
        "differs",
    ),
    ("permission source arn wildcard", "permissions", _A, '/POST/${each.key}"', '/POST/*"', "exact stage"),
    (
        "second permission",
        "permissions",
        _A,
        _PERM,
        'resource "aws_lambda_permission" "x" {\n}\n' + _PERM,
        "2 aws_lambda_permission",
    ),
    (
        "permission qualifier",
        "permissions",
        _A,
        "  function_name = each.value\n",
        '  function_name = each.value\n  qualifier = "live"\n',
        "qualifier",
    ),
    ("policy Allow added", "policy", _A, 'Effect    = "Deny"', 'Effect    = "Allow"', "Allow"),
    ("policy equals flipped", "policy", _A, _POLICY_COND, "StringEquals = {", "StringNotEquals"),
    (
        "policy second exception",
        "policy",
        _A,
        '"aws:PrincipalArn" = local.ducklake_api_session_user_arn',
        '"aws:PrincipalArn" = [local.ducklake_api_session_user_arn, "x"]',
        "more than",
    ),
    ("policy principal narrowed", "policy", _A, 'Principal = "*"', 'Principal = { AWS = "x" }', "Principal"),
    ("policy resource narrowed", "policy", _A, 'execution_arn}/*"', 'execution_arn}/live/POST/ops/reader"', "Resource"),
    ("session user renamed", "policy", _A, "user/agent-platform-session", "user/someone", "excepted user"),
    ("data trace on", "stage", _A, "data_trace_enabled     = false", "data_trace_enabled     = true", "data_trace_enabled"),
    ("logging info", "stage", _A, 'logging_level          = "OFF"', 'logging_level          = "INFO"', "logging_level"),
    (
        "request body in format",
        "stage",
        _A,
        'responseLength     = "$context.responseLength"',
        'responseLength     = "$input.body"',
        "$input",
    ),
    ("userArn dropped from format", "stage", _A, "$context.identity.userArn", "$context.requestId", "identity.userArn"),
    ("log group skip_destroy dropped", "stage", _A, "skip_destroy      = true", "skip_destroy      = false", "skip_destroy"),
    (
        "policy dropped from trigger",
        "redeploy",
        _A,
        "      aws_api_gateway_rest_api_policy.agent_ducklake[0].policy,\n",
        "",
        "rest_api_policy",
    ),
    (
        "create_before_destroy removed",
        "redeploy",
        _A,
        "create_before_destroy = true",
        "create_before_destroy = false",
        "create_before_destroy",
    ),
    (
        "boundary on log role",
        "log role",
        _A,
        '  name        = "ducklake-api-access-logs"\n  description',
        '  permissions_boundary = "x"\n  name        = "ducklake-api-access-logs"\n  description',
        "boundary",
    ),
    (
        "log role renamed",
        "log role",
        _A,
        '  name        = "ducklake-api-access-logs"\n  description',
        '  name        = "agent-platform-ducklake-api-logs"\n  description',
        "wrong name",
    ),
    (
        "second trust principal",
        "log role",
        _A,
        'Principal = { Service = "apigateway.amazonaws.com" }',
        'Principal = { Service = "apigateway.amazonaws.com", AWS = "x" }',
        "trust",
    ),
    (
        "eighth logs action",
        "log role",
        _A,
        '"logs:FilterLogEvents"\n',
        '"logs:FilterLogEvents",\n          "logs:DeleteLogGroup"\n',
        "actions are",
    ),
    ("one of the seven dropped", "log role", _A, '          "logs:GetLogEvents",\n', "", "actions are"),
    (
        "log policy narrowed to the access-log group",
        "log role",
        _A,
        _LOG_RESOURCE,
        '        Resource = "arn:aws:logs:r:a:log-group:/aws/apigateway/agent-platform-ducklake-api"\n',
        "one unconditioned Allow",
    ),
    (
        "log policy condition",
        "log role",
        _A,
        _LOG_RESOURCE,
        _LOG_RESOURCE + '        Condition = { Bool = { "aws:SecureTransport" = "true" } }\n',
        "one unconditioned Allow",
    ),
    (
        "attachment on the log role",
        "log role",
        _A,
        _ACCOUNT,
        'resource "aws_iam_role_policy_attachment" "x" {\n  role       = aws_iam_role.agent_ducklake_api_logs[0].name\n'
        '  policy_arn = "x"\n}\n\n' + _ACCOUNT,
        "policy attachment",
    ),
    (
        "second inline policy on the log role",
        "log role",
        _A,
        _ACCOUNT,
        'resource "aws_iam_role_policy" "x" {\n  role = aws_iam_role.agent_ducklake_api_logs[0].id\n}\n\n' + _ACCOUNT,
        "2 inline policies",
    ),
    (
        "attachment in another bootstrap file",
        "log role",
        _PLUS_BOOTSTRAP,
        "",
        'resource "aws_iam_policy_attachment" "x" {\n  roles = [aws_iam_role.agent_ducklake_api_logs[0].name]\n}\n',
        "policy attachment",
    ),
    (
        "managed_policy_arns on the log role",
        "log role",
        _A,
        _ROLE_DESC,
        "  managed_policy_arns = []\n" + _ROLE_DESC,
        "managed_policy_arns",
    ),
    (
        "inline_policy block on the log role",
        "log role",
        _A,
        _ROLE_DESC,
        '  inline_policy {\n    name = "x"\n  }\n' + _ROLE_DESC,
        "inline_policy",
    ),
    (
        "description reverted",
        "log role",
        _A,
        "AWS's API Gateway logging actions, inline (Decision 255)",
        "writes the DuckLake API access log only (Decision 255)",
        "description",
    ),
    (
        "methods hashed whole",
        "redeploy",
        _A,
        _METHOD_HASH,
        "aws_api_gateway_method.agent_ducklake_route",
        "aws_api_gateway_method.*",
    ),
    (
        "integrations hashed whole",
        "redeploy",
        _A,
        _INTEGRATION_HASH,
        "aws_api_gateway_integration.agent_ducklake_route",
        "aws_api_gateway_integration.*",
    ),
    (
        "method objects yielded whole",
        "redeploy",
        _A,
        "k => [m.id, m.http_method, m.authorization]",
        "k => m",
        "aws_api_gateway_method.*",
    ),
    (
        "integration referenced twice",
        "redeploy",
        _A,
        "      local.ducklake_api_routes,\n",
        "      local.ducklake_api_routes,\n      aws_api_gateway_integration.agent_ducklake_route,\n",
        "outside its attribute hash",
    ),
    (
        "secret parameter",
        "url",
        _A,
        'name  = "/agent-platform/ducklake/api_url/${each.key}"',
        'name  = "/secret/ducklake/api_url/${each.key}"',
        "name is not",
    ),
    ("deny dropped", "boundary", _B, "DenyApiGatewayWrite", "DenyApiGatewayWriteX", "missing"),
    (
        "get added to deny",
        "boundary",
        _B,
        '["apigateway:DELETE", "apigateway:P*"]',
        '["apigateway:DELETE", "apigateway:P*", "apigateway:GET"]',
        "actions are not",
    ),
    ("post only", "boundary", _B, "apigateway:P*", "apigateway:POST", "actions are not"),
    (
        "ceiling widened",
        "boundary",
        _B,
        '"ssm:*",\n          "iam:GetRole",',
        '"ssm:*",\n          "apigateway:*",\n          "iam:GetRole",',
        "DataPlaneAllow names",
    ),
]


@pytest.mark.parametrize("case", _MUTATIONS, ids=[c[0] for c in _MUTATIONS])
def test_api_predicates_are_red_on_each_mutation(case: tuple[str, str, str, str, str, str]) -> None:
    _, predicate, key, old, new, expected = case
    real = _real()
    assert _PREDICATES[predicate](real) == [], "the real tree must be green before a mutation"
    if key == _PLUS_BOOTSTRAP:
        mutated = {**real, "bootstrap": {**real["bootstrap"], "x.tf": new}}
    else:
        assert old in real[key], f"mutation anchor {old!r} not found"
        mutated = {**real, key: real[key].replace(old, new, 1)}
    problems = _PREDICATES[predicate](mutated)
    assert any(expected in p for p in problems), problems


def test_permission_predicate_sees_an_apigateway_permission_elsewhere() -> None:
    real = _real()
    stray = 'resource "aws_lambda_permission" "x" {\n  principal = "apigateway.amazonaws.com"\n}\n'
    problems = permissions_problems({**real, "extra": {**real["extra"], "x.tf": stray}})
    assert any("another apigateway-principal" in p for p in problems), problems
