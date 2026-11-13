"""Decision 255 standing guard: terraform/bootstrap declares one IAM-authorised REST API in front of the four DuckLake verbs.

Credit-free text tests over agent_ducklake_api.tf and the boundary (plan agent-ducklake-rest-api). Each predicate returns a
problem list so its red case drives the real predicate on mutated real text. The module names the files it reads as
quoted literals ("agent_ducklake_api.tf", "github_ci_apply_boundary.tf", "platform_dev_policies.tf") so --pre's
data-edge channel selects it when they change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.checks.iam_tf._platform_security_hcl import _block_body, _resource_body, _strip_comments
from tests.fixtures.terraform_hcl_blocks import tf_dir_file_texts

_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = _ROOT / "terraform" / "bootstrap"
_ROUTES = {
    "ops/reader": "ducklake_reader_function",
    "ops/writer": "ducklake_writer_function",
    "telemetry/reader": "ducklake_telemetry_reader_function",
    "telemetry/writer": "ducklake_telemetry_writer_function",
}
_SINGLETONS = (
    ("aws_api_gateway_rest_api", "agent_ducklake"),
    ("aws_api_gateway_rest_api_policy", "agent_ducklake"),
    ("aws_cloudwatch_log_group", "agent_ducklake_api_access"),
    ("aws_iam_role", "agent_ducklake_api_logs"),
    ("aws_iam_role_policy", "agent_ducklake_api_logs"),
    ("aws_api_gateway_account", "agent_ducklake"),
    ("aws_servicequotas_service_quota", "apigateway_integration_timeout"),
    ("aws_api_gateway_deployment", "agent_ducklake"),
    ("aws_api_gateway_stage", "agent_ducklake"),
    ("aws_api_gateway_method_settings", "agent_ducklake"),
)
_PER_ROUTE = (
    ("aws_api_gateway_method", "agent_ducklake_route"),
    ("aws_api_gateway_integration", "agent_ducklake_route"),
    ("aws_lambda_permission", "agent_ducklake_route"),
    ("aws_ssm_parameter", "agent_ducklake_route_url"),
)
_LOG_ACTIONS = {"logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups"}


def _texts() -> dict[str, str]:
    texts = tf_dir_file_texts(_BOOTSTRAP)
    return {"api": texts["agent_ducklake_api.tf"], "boundary": texts["github_ci_apply_boundary.tf"]}


def _res(api: str, rtype: str, rname: str) -> str:
    body = _resource_body(api, rtype, rname)
    return "" if body is None else _strip_comments(body)


def _attr(body: str, name: str) -> str:
    m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", _mask_keep(body), re.M)
    return "" if m is None else m.group(1)


def _mask_keep(text: str) -> str:
    return _strip_comments(text)


def _local(api: str, name: str) -> str:
    m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", _strip_comments(api), re.M)
    return "" if m is None else m.group(1)


def _map_keys(api: str, name: str) -> dict[str, str]:
    m = re.search(rf"\b{name}\s*=\s*\{{\s*\n\s*sandbox\s*=\s*\{{", _strip_comments(api))
    if m is None:
        return {}
    body = _block_body(api, m.end() - 1)
    return dict(re.findall(r'"([^"]+)"\s*=\s*local\.(\w+)', body))


def routes_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    if _map_keys(api, "ducklake_api_routes") != _ROUTES:
        problems.append("routes: the sandbox map is not exactly the four verb routes")
    if not re.search(r"sit\s*=\s*\{\}", _strip_comments(api)):
        problems.append("routes: the sit map is not empty")
    for rtype, rname in _SINGLETONS:
        if _attr(_res(api, rtype, rname), "count") != "local.ducklake_api_count":
            problems.append(f"routes: {rtype}.{rname} count is not local.ducklake_api_count")
    for rtype, rname in _PER_ROUTE:
        if _attr(_res(api, rtype, rname), "for_each") != "local.ducklake_api_routes":
            problems.append(f"routes: {rtype}.{rname} for_each is not local.ducklake_api_routes")
    return problems


def methods_problems(t: dict[str, Any]) -> list[str]:
    body, problems = _res(t["api"], "aws_api_gateway_method", "agent_ducklake_route"), []
    if _attr(body, "http_method") != '"POST"':
        problems.append("method: not POST")
    if _attr(body, "authorization") != '"AWS_IAM"':
        problems.append("method: authorization is not AWS_IAM")
    problems += [f"method: carries {n}" for n in ("authorizer_id", "api_key_required") if n in body]
    return problems


def integrations_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    body = _res(api, "aws_api_gateway_integration", "agent_ducklake_route")
    if _attr(body, "type") != '"AWS_PROXY"' or _attr(body, "integration_http_method") != '"POST"':
        problems.append("integration: not an AWS_PROXY POST")
    if re.search(r"^\s*credentials\s*=", body, re.M):
        problems.append("integration: carries credentials")
    if _attr(body, "timeout_milliseconds") != "local.ducklake_api_timeout_ms":
        problems.append("integration: timeout is not local.ducklake_api_timeout_ms")
    quota = _res(api, "aws_servicequotas_service_quota", "apigateway_integration_timeout")
    if _attr(quota, "quota_code") != '"L-E5AE38E3"':
        problems.append("quota: wrong quota_code")
    if _attr(quota, "value") != _local(api, "ducklake_api_timeout_ms").split("=")[-1].strip():
        problems.append("quota: value differs from the integration timeout")
    return problems


def permissions_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    perms = re.findall(r'resource\s+"aws_lambda_permission"', _strip_comments(api))
    if len(perms) != 1:
        problems.append(f"permissions: {len(perms)} aws_lambda_permission blocks in the API file")
    body = _res(api, "aws_lambda_permission", "agent_ducklake_route")
    want = {
        "principal": '"apigateway.amazonaws.com"',
        "action": '"lambda:InvokeFunction"',
        "function_name": "each.value",
    }
    problems += [f"permissions: {k} is not {v}" for k, v in want.items() if _attr(body, k) != v]
    if re.search(r"^\s*qualifier\s*=", body, re.M):
        problems.append("permissions: carries a qualifier")
    src = _attr(body, "source_arn")
    if not src.endswith('/${local.ducklake_api_stage}/POST/${each.key}"') or re.search(r"[*?]", src):
        problems.append("permissions: source_arn is not the exact stage, method and route")
    for name, text in (t.get("extra") or {}).items():
        for m in re.finditer(r'resource\s+"aws_lambda_permission"\s+"\w+"\s*\{', _strip_comments(text)):
            if "apigateway.amazonaws.com" in _block_body(_strip_comments(text), m.end() - 1):
                problems.append(f"permissions: {name} holds another apigateway-principal permission")
    return problems


def policy_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    body = _res(api, "aws_api_gateway_rest_api_policy", "agent_ducklake")
    if len(re.findall(r"\bSid\s*=", body)) != 1:
        problems.append("policy: not exactly one statement")
    want = {"Effect": '"Deny"', "Principal": '"*"', "Action": '"execute-api:Invoke"'}
    problems += [f"policy: {k} is not {v}" for k, v in want.items() if _attr(body, k) != v]
    if '"Allow"' in body:
        problems.append("policy: carries an Allow")
    if _attr(body, "Resource") != '"${aws_api_gateway_rest_api.agent_ducklake[0].execution_arn}/*"':
        problems.append("policy: Resource is not the whole API")
    cond = re.search(r"Condition\s*=\s*\{\s*(\w+)\s*=\s*\{(.*?)\}\s*\}", body, re.S)
    if cond is None or cond.group(1) != "StringNotEquals":
        problems.append("policy: condition is not StringNotEquals")
    elif cond.group(2).split("=")[-1].strip() != "local.ducklake_api_session_user_arn":
        problems.append("policy: more than the session user is excepted")
    user = _local(api, "ducklake_api_session_user_arn").split("=", 1)[-1].strip()
    if user != '"arn:aws:iam::${var.account_id}:user/agent-platform-session"':
        problems.append("policy: the excepted user is not agent-platform-session")
    return problems


def stage_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    ms = _res(api, "aws_api_gateway_method_settings", "agent_ducklake")
    for k, v in (("logging_level", '"OFF"'), ("data_trace_enabled", "false"), ("metrics_enabled", "false")):
        if _attr(ms, k) != v:
            problems.append(f"stage: {k} is not {v}")
    problems += [f"stage: {k} unset" for k in ("throttling_rate_limit", "throttling_burst_limit") if not _attr(ms, k)]
    lg = _res(api, "aws_cloudwatch_log_group", "agent_ducklake_api_access")
    if _attr(lg, "retention_in_days") != "400" or _attr(lg, "skip_destroy") != "true":
        problems.append("stage: log group lost retention 400 or skip_destroy")
    stage = _res(api, "aws_api_gateway_stage", "agent_ducklake")
    fmt = re.search(r"format\s*=\s*jsonencode\(\{(.*?)\}\)", stage, re.S)
    text = fmt.group(1) if fmt else ""
    problems += [f"stage: format lacks {v}" for v in ("identity.userArn", "resourcePath", "context.status") if v not in text]
    problems += [f"stage: format carries {v}" for v in ("$input", "ody", "eader") if v in text]
    return problems


def redeploy_problems(t: dict[str, Any]) -> list[str]:
    body, problems = _res(t["api"], "aws_api_gateway_deployment", "agent_ducklake"), []
    trig = re.search(r"triggers\s*=\s*\{(.*?)\n  \}", body, re.S)
    text = trig.group(1) if trig else ""
    for ref in (
        "rest_api_policy.agent_ducklake",
        "api_gateway_method.agent_ducklake_route",
        "integration.agent_ducklake_route",
    ):
        if ref not in text:
            problems.append(f"redeploy: trigger omits {ref}")
    if not re.search(r"create_before_destroy\s*=\s*true", body):
        problems.append("redeploy: create_before_destroy missing")
    return problems


def log_role_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    role = _res(api, "aws_iam_role", "agent_ducklake_api_logs")
    name = _attr(role, "name").strip('"')
    if name != "ducklake-api-access-logs" or name.startswith("agent-platform-"):
        problems.append("log role: wrong name")
    if "permissions_boundary" in role:
        problems.append("log role: carries a permissions boundary")
    if re.findall(r'(\w+)\s*=\s*"([^"]+)"', _attr_block(role, "Principal")) != [("Service", "apigateway.amazonaws.com")]:
        problems.append("log role: trust is not apigateway.amazonaws.com alone")
    pol = _local_block(api, "ducklake_api_log_role_policy_json")
    allows = set(re.findall(r'"(logs:\w+)"', pol))
    if allows != _LOG_ACTIONS:
        problems.append(f"log role: actions are {sorted(allows)}")
    return problems


def _attr_block(body: str, name: str) -> str:
    m = re.search(rf"{name}\s*=\s*\{{([^{{}}]*)\}}", body)
    return "" if m is None else m.group(1)


def _local_block(api: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*jsonencode\(\{{", _strip_comments(api))
    return "" if m is None else _block_body(api, m.end() - 1)


def route_url_problems(t: dict[str, Any]) -> list[str]:
    body, problems = _res(t["api"], "aws_ssm_parameter", "agent_ducklake_route_url"), []
    if not _attr(body, "name").startswith('"/agent-platform/ducklake/api_url/${each.key}'):
        problems.append("url: name is not under /agent-platform/ducklake/api_url/")
    if _attr(body, "type") != '"String"' or "/secret/" in body:
        problems.append("url: not a plain String outside /secret/")
    return problems


def boundary_problems(t: dict[str, Any]) -> list[str]:
    text, problems = _strip_comments(t["boundary"]), []

    def stmt(sid: str) -> str:
        m = re.search(rf'\{{[^{{}}]*Sid\s*=\s*"{sid}"[^{{}}]*\}}', text)
        return "" if m is None else m.group(0)

    deny = stmt("DenyApiGatewayWrite")
    if not deny:
        return ["boundary: DenyApiGatewayWrite missing or conditioned"]
    if _attr(deny, "Effect") != '"Deny"' or _attr(deny, "Resource") != '"*"':
        problems.append("boundary: DenyApiGatewayWrite is not an unconditioned Deny on *")
    if re.findall(r'"([^"]+)"', _attr(deny, "Action")) != ["apigateway:DELETE", "apigateway:P*"]:
        problems.append("boundary: DenyApiGatewayWrite actions are not apigateway:DELETE and apigateway:P*")
    ceiling = stmt("DataPlaneAllow")
    if "iam:GetRole" not in ceiling:
        problems.append("boundary: DataPlaneAllow did not parse")
    if re.search(r'"(apigateway|execute-api):', ceiling):
        problems.append("boundary: DataPlaneAllow names an apigateway or execute-api action")
    return problems


_PREDICATES = {
    "routes": routes_problems,
    "methods": methods_problems,
    "integrations": integrations_problems,
    "permissions": permissions_problems,
    "policy": policy_problems,
    "stage": stage_problems,
    "redeploy": redeploy_problems,
    "log role": log_role_problems,
    "url": route_url_problems,
    "boundary": boundary_problems,
}


def _real() -> dict[str, Any]:
    t: dict[str, Any] = dict(_texts())
    t["extra"] = tf_dir_file_texts(_ROOT / "terraform" / "personal")
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
        "fifth logs action",
        "log role",
        _A,
        '"logs:DescribeLogStreams"\n',
        '"logs:DescribeLogStreams",\n          "logs:DeleteLogGroup"\n',
        "actions are",
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
    assert old in real[key], f"mutation anchor {old!r} not found"
    mutated = {**real, key: real[key].replace(old, new, 1)}
    problems = _PREDICATES[predicate](mutated)
    assert any(expected in p for p in problems), problems


def test_permission_predicate_sees_an_apigateway_permission_elsewhere() -> None:
    real = _real()
    stray = 'resource "aws_lambda_permission" "x" {\n  principal = "apigateway.amazonaws.com"\n}\n'
    problems = permissions_problems({**real, "extra": {**real["extra"], "x.tf": stray}})
    assert any("another apigateway-principal" in p for p in problems), problems
