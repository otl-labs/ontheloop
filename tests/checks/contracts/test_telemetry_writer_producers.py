"""Producer admission guard for the telemetry writer contract (rec-5153; Decisions 255 and 256).

Binds principal_classes to the terraform-declared signed identities. Text-only terraform reads (never state) of
agent_session_user.tf, agent_ducklake_api.tf, oidc_ci_roles.tf and platform_roles.tf, so --pre's mention channel selects
this module when they change. Each leg is a problem-returning predicate; test_admission_legs_fail_on_drift drives every
one on mutated real text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from src.lambdas.ducklake_telemetry_writer import handler as writer_handler
from tests.checks.iam_tf._agent_ducklake_api_predicates import policy_problems, routes_problems
from tests.checks.iam_tf._platform_security_hcl import _resource_body

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/contracts/ducklake_telemetry_writer.yaml"
SESSION_TF = ROOT / "terraform/bootstrap/agent_session_user.tf"
API_TF = ROOT / "terraform/bootstrap/agent_ducklake_api.tf"
OIDC_TF = ROOT / "terraform/personal/oidc_ci_roles.tf"
ROLES_TF = ROOT / "terraform/bootstrap/platform_roles.tf"
SESSION_CLASS = "agent-platform-session"
ROWS_BOTH_PATHS = ("describe", "provision_telemetry", "register_tenant", "register_project")
ROWS_PATH_NEUTRAL = ("append_telemetry", "begin_blobs")
CLASS_IDENTITIES = {
    "github_ci_branch": ("aws_iam_role", "github_ci_branch", "agent-platform-github-ci-branch", "oidc"),
    "PlatformDev": ("aws_iam_role", "platform_dev", "PlatformDev", "roles"),
    SESSION_CLASS: ("aws_iam_user", "agent_session", SESSION_CLASS, "session"),
}


def _ctx() -> dict[str, Any]:
    return {
        "contract": yaml.safe_load(CONTRACT.read_text(encoding="utf-8")),
        "session": SESSION_TF.read_text(encoding="utf-8"),
        "api": API_TF.read_text(encoding="utf-8"),
        "oidc": OIDC_TF.read_text(encoding="utf-8"),
        "roles": ROLES_TF.read_text(encoding="utf-8"),
    }


def _classes(ctx: dict[str, Any]) -> list[str]:
    return list(ctx["contract"]["contract"]["governance"]["principal_classes"])


def _name_attr(text: str, rtype: str, rname: str) -> str:
    body = _resource_body(text, rtype, rname) or ""
    m = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
    return "" if m is None else m.group(1)


def class_problems(ctx: dict[str, Any]) -> list[str]:
    name = _name_attr(ctx["session"], "aws_iam_user", "agent_session")
    problems = []
    if SESSION_CLASS not in _classes(ctx):
        problems.append("class: principal_classes lacks agent-platform-session")
    if name != SESSION_CLASS:
        problems.append(f"class: aws_iam_user.agent_session is named {name!r}, not the contract token")
    return problems


def grant_problems(ctx: dict[str, Any]) -> list[str]:
    problems = [f"grant: {p}" for p in routes_problems(ctx)]
    stmt = re.search(r'Sid\s*=\s*"DuckLakeApiInvoke"(.*?)\n\s*\}\]', ctx["session"], re.S)
    body = stmt.group(1) if stmt else ""
    if not re.search(r'Action\s*=\s*"execute-api:Invoke"', body) or "local.ducklake_api_routes" not in body:
        problems.append("grant: DuckLakeApiInvoke is not execute-api:Invoke over local.ducklake_api_routes")
    if not re.search(r'"telemetry/writer"\s*=\s*local\.ducklake_telemetry_writer_function', ctx["api"]):
        problems.append("grant: telemetry/writer is not mapped to the telemetry writer function")
    return problems


def fence_problems(ctx: dict[str, Any]) -> list[str]:
    problems = [f"fence: {p}" for p in policy_problems(ctx)]
    m = re.search(r'ducklake_api_session_user_arn\s*=\s*"[^"]*user/([^"]+)"', ctx["api"])
    if m is None or m.group(1) != SESSION_CLASS:
        problems.append("fence: the excepted user does not end user/agent-platform-session")
    return problems


def handler_problems(ctx: dict[str, Any]) -> list[str]:
    token = next((c for c in _classes(ctx) if c == SESSION_CLASS), "")
    event = {
        "httpMethod": "POST",
        "requestContext": {
            "apiId": "x",
            "identity": {"accountId": "acct-under-test", "userArn": f"arn:aws:iam::acct-under-test:user/{token}"},
        },
    }
    problems = []
    if writer_handler._principal(event).role_name != token or not token:
        problems.append("handler: a REST caller's role_name is not the contract token")
    try:
        writer_handler._require_caller({"httpMethod": "POST", "requestContext": {"apiId": "x"}})
        problems.append("handler: a REST event with no identity was not refused")
    except Exception as exc:  # noqa: BLE001 - the refusal type is the assertion
        if getattr(exc, "error_type", None) != "missing_caller_identity":
            problems.append("handler: the refusal is not typed missing_caller_identity")
    return problems


def rows_problems(ctx: dict[str, Any]) -> list[str]:
    verbs = ctx["contract"]["verbs"]
    problems = []
    for name, spec in verbs.items():
        row = str(spec.get("response_codes", {}).get(403, ""))
        if "Function URL authorizer context" in row:
            problems.append(f"rows: {name} 403 still says Function URL authorizer context")
        if name in ROWS_BOTH_PATHS and not ("Function URL" in row and "REST API" in row):
            problems.append(f"rows: {name} 403 does not name both Function URL and REST API")
        if name in ROWS_PATH_NEUTRAL and ("Function URL" in row or "REST API" in row):
            problems.append(f"rows: {name} 403 is not path-neutral")
    return problems


def classes_signed_problems(ctx: dict[str, Any]) -> list[str]:
    problems = []
    for cls in _classes(ctx):
        mapped = CLASS_IDENTITIES.get(cls)
        if mapped is None:
            problems.append(f"classes: {cls} maps to no terraform-declared identity")
            continue
        rtype, rname, ident, key = mapped
        if _name_attr(ctx[key], rtype, rname) != ident:
            problems.append(f"classes: {cls} is no longer declared as {rtype}.{rname} named {ident}")
    return problems


def test_session_user_admitted() -> None:
    ctx = _ctx()
    for leg in (class_problems, grant_problems, fence_problems, handler_problems, rows_problems):
        assert leg(ctx) == [], leg.__name__


def test_every_declared_class_is_a_signed_identity() -> None:
    assert classes_signed_problems(_ctx()) == []


def _mut(ctx: dict[str, Any], key: str, old: str, new: str) -> dict[str, Any]:
    assert old in ctx[key], (key, old)
    return {**ctx, key: ctx[key].replace(old, new, 1)}


def _drop_class(ctx: dict[str, Any]) -> dict[str, Any]:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract["contract"]["governance"]["principal_classes"].remove(SESSION_CLASS)
    return {**ctx, "contract": contract}


def _revert_row(ctx: dict[str, Any]) -> dict[str, Any]:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract["verbs"]["describe"]["response_codes"][403] = (
        "missing_caller_identity: the Function URL authorizer context carries no caller identity"
    )
    return {**ctx, "contract": contract}


def test_admission_legs_fail_on_drift() -> None:
    ctx = _ctx()
    cases: list[tuple[Callable[[dict[str, Any]], list[str]], dict[str, Any]]] = [
        (class_problems, _drop_class(ctx)),
        (
            class_problems,
            _mut(ctx, "session", 'name                 = "agent-platform-session"', 'name                 = "renamed"'),
        ),
        (handler_problems, _drop_class(ctx)),
        (
            grant_problems,
            _mut(ctx, "api", '"telemetry/writer" = local.ducklake_telemetry_writer_function', '"telemetry/other" = local.x'),
        ),
        (grant_problems, _mut(ctx, "session", 'Action   = "execute-api:Invoke"', 'Action   = "execute-api:*"')),
        (fence_problems, _mut(ctx, "api", "user/agent-platform-session", "user/someone-else")),
        (fence_problems, _mut(ctx, "api", "StringNotEquals", "StringEquals")),
        (rows_problems, _revert_row(ctx)),
        (classes_signed_problems, _mut(ctx, "roles", 'name                 = "PlatformDev"', 'name                 = "Gone"')),
    ]
    for leg, mutated in cases:
        assert leg(mutated), leg.__name__
