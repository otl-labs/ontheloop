"""REST-route producer guard for the telemetry writer contract (rec-5153, rec-5198; Decisions 255 and 256).

Binds each rest_route class of docs/contracts/telemetry-writer-principals.yaml to its IAM user, its route grant, the
API's principal fence, the handler's caller shape and the 403 rows. Which identities may write at all, and with what
grant, is validate_telemetry_writer_principals's subject (rec-4420). Text-only terraform reads (never state) of
agent_session_user.tf and agent_ducklake_api.tf, so --pre's mention channel selects this module when they change. Each
leg is a problem-returning predicate; test_admission_legs_fail_on_drift drives every one on mutated real text and
test_drift_survives_realigned_terraform proves a formatting-only change keeps every leg green.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir
from src.lambdas.ducklake_telemetry_writer import handler as writer_handler
from tests.checks.iam_tf._agent_ducklake_api_predicates import policy_problems, routes_problems
from tests.checks.iam_tf._platform_security_hcl import _resource_body

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/contracts/ducklake_telemetry_writer.yaml"
REGISTRY = ROOT / "docs/contracts/telemetry-writer-principals.yaml"
SESSION_TF = ROOT / "terraform/bootstrap/agent_session_user.tf"
API_TF = ROOT / "terraform/bootstrap/agent_ducklake_api.tf"
SESSION_CLASS = "agent-platform-session"
ROWS_BOTH_PATHS = ("describe", "provision_telemetry", "register_tenant", "register_project")
ROWS_PATH_NEUTRAL = ("append_telemetry", "begin_blobs")


def _ctx() -> dict[str, Any]:
    return {
        "contract": yaml.safe_load(CONTRACT.read_text(encoding="utf-8")),
        "registry": yaml.safe_load(REGISTRY.read_text(encoding="utf-8")),
        "session": SESSION_TF.read_text(encoding="utf-8"),
        "api": API_TF.read_text(encoding="utf-8"),
        "files": {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in _identity_files()},
    }


def _identity_files() -> list[str]:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    return sorted({row["identity"]["file"] for row in registry["classes"].values()})


def _classes(ctx: dict[str, Any]) -> list[str]:
    return list(ctx["contract"]["contract"]["governance"]["principal_classes"])


def _rest_route_classes(ctx: dict[str, Any]) -> list[str]:
    return [c for c, spec in ((ctx["registry"] or {}).get("classes") or {}).items() if "rest_route" in spec.get("paths", [])]


def _name_attr(text: str, rtype: str, rname: str) -> str | None:
    """The literal name attribute, "" when the resource is absent, None when the name is not a string literal."""
    body = _resource_body(text, rtype, rname)
    if not body:
        return ""
    m = re.search(r"^\s*name\s*=\s*(.+?)\s*$", body, re.M)
    if m is None:
        return ""
    lit = re.fullmatch(r'"([^"$]+)"', m.group(1))
    return None if lit is None else lit.group(1)


def class_problems(ctx: dict[str, Any]) -> list[str]:
    name = _name_attr(ctx["session"], "aws_iam_user", "agent_session")
    problems = []
    if _rest_route_classes(ctx) != [SESSION_CLASS] or SESSION_CLASS not in _classes(ctx):
        problems.append("class: agent-platform-session is not the one declared rest_route class in principal_classes")
    if name is None:
        problems.append("class: aws_iam_user.agent_session's name is not a string literal")
    elif name != SESSION_CLASS:
        problems.append(f"class: aws_iam_user.agent_session is named {name!r}, not the contract token")
    return problems


def _statement_object(text: str, sid: str) -> str:
    m = re.search(rf'Sid\s*=\s*"{sid}"', text)
    if m is None:
        return ""
    start = text.rfind("{", 0, m.start())
    return _vir._extract_block(text, start) if start >= 0 else ""


def grant_problems(ctx: dict[str, Any]) -> list[str]:
    problems = [f"grant: {p}" for p in routes_problems(ctx)]
    body = _statement_object(ctx["session"], "DuckLakeApiInvoke")
    if not re.search(r'Action\s*=\s*"execute-api:Invoke"', body) or "local.ducklake_api_routes" not in body:
        problems.append("grant: DuckLakeApiInvoke is not execute-api:Invoke over local.ducklake_api_routes")
    if not re.search(r'"telemetry/writer"\s*=\s*local\.ducklake_telemetry_writer_function', ctx["api"]):
        problems.append("grant: telemetry/writer is not mapped to the telemetry writer function")
    return problems


def fence_problems(ctx: dict[str, Any]) -> list[str]:
    problems = [f"fence: {p}" for p in policy_problems(ctx)]
    m = re.search(r'ducklake_api_session_user_arn\s*=\s*"[^"]*user/([^"]+)"', ctx["api"])
    if m is None or [m.group(1)] != _rest_route_classes(ctx):
        problems.append("fence: the excepted user is not exactly the registry's rest_route class")
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
    """Every principal_classes token is a registry row whose identity Terraform still declares under that name."""
    rows = (ctx["registry"] or {}).get("classes") or {}
    problems = []
    for cls in _classes(ctx):
        ident = (rows.get(cls) or {}).get("identity")
        if not ident:
            problems.append(f"classes: {cls} has no registry identity")
            continue
        rtype, rname = ident["resource"].split(".", 1)
        text = ctx["files"].get(ident["file"]) or ""
        if _name_attr(text, rtype, rname) != ident["name"]:
            problems.append(f"classes: {cls} is no longer declared as {ident['resource']} named {ident['name']}")
    return problems


def test_session_user_admitted() -> None:
    ctx = _ctx()
    for leg in (class_problems, grant_problems, fence_problems, handler_problems, rows_problems):
        assert leg(ctx) == [], leg.__name__


def test_every_declared_class_is_a_signed_identity() -> None:
    assert classes_signed_problems(_ctx()) == []


def _mut(ctx: dict[str, Any], key: str, pattern: str, new: str) -> dict[str, Any]:
    text, n = re.subn(pattern, new, ctx[key], count=1)
    assert n == 1, (key, pattern)
    return {**ctx, key: text}


def _realign(text: str) -> str:
    return re.sub(r"(\S)[ \t]+=[ \t]+", r"\1 = ", text)


def _drop_class(ctx: dict[str, Any]) -> dict[str, Any]:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    contract["contract"]["governance"]["principal_classes"].remove(SESSION_CLASS)
    del registry["classes"][SESSION_CLASS]
    return {**ctx, "contract": contract, "registry": registry}


def _revert_row(ctx: dict[str, Any]) -> dict[str, Any]:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract["verbs"]["describe"]["response_codes"][403] = (
        "missing_caller_identity: the Function URL authorizer context carries no caller identity"
    )
    return {**ctx, "contract": contract}


def _drop_row_identity(ctx: dict[str, Any]) -> dict[str, Any]:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    del registry["classes"]["github_ci_branch"]["identity"]
    return {**ctx, "registry": registry}


def _rename_in_file(ctx: dict[str, Any], rel: str, old: str, new: str) -> dict[str, Any]:
    text, n = re.subn(rf'(name\s*=\s*)"{re.escape(old)}"', rf'\1"{new}"', ctx["files"][rel], count=1)
    assert n == 1, (rel, old)
    return {**ctx, "files": {**ctx["files"], rel: text}}


def test_admission_legs_fail_on_drift() -> None:
    ctx = _ctx()
    cases: list[tuple[Callable[[dict[str, Any]], list[str]], dict[str, Any]]] = [
        (class_problems, _drop_class(ctx)),
        (class_problems, _mut(ctx, "session", r'name\s*=\s*"agent-platform-session"', 'name = "renamed"')),
        (class_problems, _mut(ctx, "session", r'name\s*=\s*"agent-platform-session"', "name = local.user_name")),
        (handler_problems, _drop_class(ctx)),
        (
            grant_problems,
            _mut(
                ctx,
                "api",
                r'"telemetry/writer"\s*=\s*local\.ducklake_telemetry_writer_function',
                '"telemetry/other" = local.x',
            ),
        ),
        (grant_problems, _mut(ctx, "session", r'Action\s*=\s*"execute-api:Invoke"', 'Action = "execute-api:*"')),
        (fence_problems, _mut(ctx, "api", r"user/agent-platform-session", "user/someone-else")),
        (fence_problems, _mut(ctx, "api", r"StringNotEquals", "StringEquals")),
        (rows_problems, _revert_row(ctx)),
        (classes_signed_problems, _drop_row_identity(ctx)),
        (classes_signed_problems, _rename_in_file(ctx, "terraform/bootstrap/platform_roles.tf", "PlatformDev", "Gone")),
    ]
    for leg, mutated in cases:
        assert leg(mutated), leg.__name__


def test_drift_survives_realigned_terraform() -> None:
    ctx = _ctx()
    realigned = {**ctx, "session": _realign(ctx["session"]), "api": _realign(ctx["api"])}
    assert realigned["session"] != ctx["session"]
    files = {rel: _realign(text) for rel, text in ctx["files"].items()}
    realigned["files"] = files
    for leg in (class_problems, grant_problems, fence_problems, classes_signed_problems):
        assert leg(realigned) == [], leg.__name__
