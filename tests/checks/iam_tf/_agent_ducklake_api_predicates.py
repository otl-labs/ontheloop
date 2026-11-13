"""Predicates for test_agent_ducklake_api.py (Decision 128 split; the test file keeps the nodes, the mutation table
and the quoted basename literals --pre's data-edge channel reads). Each predicate takes {"api", "boundary", "extra",
"bootstrap"} texts and returns a problem list.
"""

from __future__ import annotations

import re
from typing import Any

from tests.checks.iam_tf._platform_security_hcl import _block_body, _resource_body, _strip_comments

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
_LOG_ACTIONS = {
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:DescribeLogGroups",
    "logs:DescribeLogStreams",
    "logs:PutLogEvents",
    "logs:GetLogEvents",
    "logs:FilterLogEvents",
}
_LOG_ROLE_DESCRIPTION = "API Gateway account CloudWatch role: AWS's API Gateway logging actions, inline (Decision 255)"
_METHOD_HASH = "{ for k, m in aws_api_gateway_method.agent_ducklake_route : k => [m.id, m.http_method, m.authorization] }"
_INTEGRATION_HASH = (
    "{ for k, i in aws_api_gateway_integration.agent_ducklake_route : k => [i.id, i.type, i.uri, i.timeout_milliseconds] }"
)
_ROLE_REF = re.compile(r'aws_iam_role\.agent_ducklake_api_logs\b|"ducklake-api-access-logs"')
_ATTACH_TYPES = ("aws_iam_role_policy_attachment", "aws_iam_policy_attachment", "aws_iam_role_policy_attachments_exclusive")


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
            if re.search(r'principal\s*=\s*"apigateway\.amazonaws\.com"', _block_body(_strip_comments(text), m.end() - 1)):
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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def redeploy_problems(t: dict[str, Any]) -> list[str]:
    body, problems = _res(t["api"], "aws_api_gateway_deployment", "agent_ducklake"), []
    trig = re.search(r"triggers\s*=\s*\{(.*?)\n  \}", body, re.S)
    text = _norm(trig.group(1)) if trig else ""
    for ref in ("local.ducklake_api_routes", "aws_api_gateway_rest_api_policy.agent_ducklake[0].policy"):
        if ref not in text:
            problems.append(f"redeploy: trigger omits {ref}")
    for want, res in ((_METHOD_HASH, "aws_api_gateway_method."), (_INTEGRATION_HASH, "aws_api_gateway_integration.")):
        if want not in text:
            problems.append(f"redeploy: trigger lacks the exact {res}* attribute hash")
        if text.count(res) != 1:
            problems.append(f"redeploy: trigger references {res}* outside its attribute hash")
    if not re.search(r"create_before_destroy\s*=\s*true", body):
        problems.append("redeploy: create_before_destroy missing")
    return problems


def _blocks(text: str, rtype: str) -> list[str]:
    clean = _strip_comments(text)
    return [_block_body(clean, m.end() - 1) for m in re.finditer(rf'resource\s+"{rtype}"\s+"[\w-]+"\s*\{{', clean)]


def _names_role(body: str) -> bool:
    return any(_ROLE_REF.search(_attr(body, arg)) for arg in ("role", "role_name", "roles"))


def log_role_problems(t: dict[str, Any]) -> list[str]:
    api, problems = t["api"], []
    files = {**(t.get("bootstrap") or {}), "agent_ducklake_api.tf": api}
    role = _res(api, "aws_iam_role", "agent_ducklake_api_logs")
    name = _attr(role, "name").strip('"')
    if name != "ducklake-api-access-logs" or name.startswith("agent-platform-"):
        problems.append("log role: wrong name")
    if "permissions_boundary" in role:
        problems.append("log role: carries a permissions boundary")
    if re.search(r"\bmanaged_policy_arns\s*=|\binline_policy\s*\{", role):
        problems.append("log role: declares managed_policy_arns or an inline_policy block")
    if _attr(role, "description") != f'"{_LOG_ROLE_DESCRIPTION}"':
        problems.append("log role: description is not the amended text")
    if re.findall(r'(\w+)\s*=\s*"([^"]+)"', _attr_block(role, "Principal")) != [("Service", "apigateway.amazonaws.com")]:
        problems.append("log role: trust is not apigateway.amazonaws.com alone")
    inline = [b for text in files.values() for b in _blocks(text, "aws_iam_role_policy") if _names_role(b)]
    if len(inline) != 1:
        problems.append(f"log role: {len(inline)} inline policies name the role")
    attached = [n for n, text in files.items() for ty in _ATTACH_TYPES for b in _blocks(text, ty) if _names_role(b)]
    if attached:
        problems.append(f"log role: a policy attachment names the role in {sorted(set(attached))}")
    pol = _local_block(api, "ducklake_api_log_role_policy_json")
    stmts = re.findall(r"\bSid\s*=", pol)
    if len(stmts) != 1 or _attr(pol, "Effect") != '"Allow"' or _attr(pol, "Resource") != '"*"' or "Condition" in pol:
        problems.append("log role: policy is not one unconditioned Allow on *")
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
