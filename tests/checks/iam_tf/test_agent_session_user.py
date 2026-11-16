"""PLAN-agent-proxy-signed-access: the agent session user's grants, guard and boundary stay closed.

Credential-free text tests over terraform/bootstrap/agent_session_user.tf in the iam_tf convention: each check is a predicate
over the Terraform text that returns a problem list, so its red case drives the real predicate on mutated input.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _resources, _strip_comments
from tests.fixtures.terraform_hcl_blocks import tf_dir_file_texts

_ROOT = Path(__file__).resolve().parents[3]
_TF = _ROOT / "terraform"
_USER, _DEV, _BOUNDARY = "agent_session_user.tf", "platform_dev_policies.tf", "github_ci_apply_boundary.tf"
_INVOKE_TEMPLATE = '"${api.execution_arn}/${local.ducklake_api_stage}/POST/${route}"'
_INVOKE_FOR = f"[for route in sort(keys(local.ducklake_api_routes)) : {_INVOKE_TEMPLATE}]"
_DECLARED_ALLOW = {
    "execute-api:Invoke",
    "ssm:GetParameter",
    "cloudwatch:GetMetricStatistics",
    "cloudwatch:GetMetricData",
    "cloudwatch:ListMetrics",
    "cloudwatch:DescribeAlarms",
    "logs:DescribeLogGroups",
    "logs:DescribeLogStreams",
    "logs:GetLogEvents",
    "logs:FilterLogEvents",
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies",
}
_IAM_READS = {a for a in _DECLARED_ALLOW if a.startswith("iam:")}
_DAILY_OPS_DENIES = (
    "DenyStateAndConvergenceWrite",
    "DenyStateRead",
    "DenyTerraformArtifactRead",
    "DenyTerraformArtifactWrite",
    "DenyReviewTranscriptRead",
    "DenyReviewTranscriptWrite",
    "DenyStateAndConvergenceVersionRead",
    "DenyStateAndConvergenceVersionList",
)
_ESCALATION = {
    "sts:AssumeRole*",
    "sts:AssumeRoot",
    "sts:GetFederationToken",
    *(
        f"iam:{verb}*"
        for verb in (
            "Create Update Put Attach Detach Delete Add Remove Tag Untag Set Upload Enable Deactivate Change Reset".split()
        )
    ),
    "iam:PassRole",
}
_KEY_TYPES = (
    "aws_iam_access_key",
    "aws_iam_user_policy",
    "aws_iam_user_login_profile",
    "aws_iam_service_specific_credential",
    "aws_iam_signing_certificate",
    "aws_iam_user_ssh_key",
)
_BOUNDARY_USER_WRITE = (
    "User",
    "AccessKey",
    "LoginProfile",
    "PolicyVersion",
    "ServiceSpecificCredential",
    "SSHPublicKey",
    "SigningCertificate",
    "MFA",
)


def _ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strings(raw: str) -> list[str]:
    return re.findall(r'"([^"]*)"', raw)


def _statements(text: str) -> list[dict]:
    clean, masked, out = _strip_comments(text), _mask(text), []
    for sid in re.finditer(r'\bSid\s*=\s*"(\w+)"', clean):
        depth, i = 0, sid.start()
        while i > 0:
            i -= 1
            if masked[i] == "}":
                depth += 1
            elif masked[i] == "{":
                if depth == 0:
                    break
                depth -= 1
        body = _block_body(clean, i)
        field = lambda name: (re.search(rf"\b{name}\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|[\w.]+)", body) or [None, ""])[1]  # noqa: E731
        cond = re.search(r"\bCondition\s*=\s*\{", _mask(body))
        out.append(
            {
                "sid": sid.group(1),
                "effect": _strings(field("Effect"))[0] if field("Effect") else "",
                "actions": _strings(field("Action")),
                "resource_text": _ws(field("Resource")),
                "condition_text": _ws(_block_body(body, cond.end() - 1)) if cond else "",
            }
        )
    return out


def _allows(text: str) -> list[dict]:
    return [s for s in _statements(text) if s["effect"] == "Allow"]


def _unreadable(text: str, sid: str) -> list[str]:
    return [] if any(s["sid"] == sid for s in _statements(text)) else [f"{sid}: statement not found"]


def own_boundary_problems(text: str) -> list[str]:
    clean, problems = _strip_comments(text), []
    user = re.search(r'resource\s+"aws_iam_user"\s+"agent_session"\s*\{', clean)
    body = _block_body(clean, user.end() - 1) if user else ""
    if not user or not re.search(r'\bname\s*=\s*"agent-platform-session"', body):
        problems.append("the user's name is not exactly agent-platform-session")
    if re.search(r"^\s*path\s*=", body, re.M):
        problems.append("the user sets a path argument")
    if not re.search(r"\bpermissions_boundary\s*=\s*aws_iam_policy\.agent_session_boundary\.arn\b", body):
        problems.append("permissions_boundary is not aws_iam_policy.agent_session_boundary.arn")
    if not re.search(r"\bprevent_destroy\s*=\s*true\b", body):
        problems.append("prevent_destroy is not set on the user")
    boundary = re.search(r"agent_session_boundary_policy_json\s*=\s*jsonencode\(\{", clean)
    statement = re.search(r"\bStatement\s*=\s*([^\n]*)", _block_body(clean, boundary.end() - 1)) if boundary else None
    want = "concat(local.agent_session_ducklake_statements,local.agent_session_observe_statements)"
    if not statement or _ws(statement.group(1)) != want:
        problems.append("the boundary's Statement is not exactly the concat of the ducklake and observe statement locals")
    return problems


def invoke_problems(text: str) -> list[str]:
    problems = []
    invoke = [s for s in _allows(text) if s["sid"] == "DuckLakeApiInvoke"]
    if len(invoke) != 1 or invoke[0]["actions"] != ["execute-api:Invoke"]:
        problems.append("the only invoke Allow must be one execute-api:Invoke statement")
    elif invoke[0]["resource_text"] != _ws(_INVOKE_FOR):
        problems.append("the invoke Resource is not the exact route ARN template over keys(local.ducklake_api_routes)")
    if "*" in "".join(re.findall(r"\$\{[^}]*\}|POST/[^\"]*", _INVOKE_TEMPLATE)):
        problems.append("a wildcard in the invoke ARN template")
    if not re.search(r"for api in aws_api_gateway_rest_api\.agent_ducklake\b", _strip_comments(text)):
        problems.append("the invoke statement is not built over aws_api_gateway_rest_api.agent_ducklake")
    problems += [
        f"{s['sid']}: names a lambda or apigateway action"
        for s in _allows(text)
        if any(a.startswith(("lambda:", "apigateway:")) for a in s["actions"])
    ]
    if "*" in "".join(s["resource_text"] for s in invoke if s["resource_text"] != _ws(_INVOKE_FOR)):
        problems.append("a wildcard in the invoke Resource")
    return problems


def closed_grants_problems(text: str) -> list[str]:
    union = {a for s in _allows(text) for a in s["actions"]}
    return [f"unexpected Allow action {a}" for a in sorted(union - _DECLARED_ALLOW)] + [
        f"declared Allow action {a} is missing" for a in sorted(_DECLARED_ALLOW - union)
    ]


def reach_problems(text: str) -> list[str]:
    problems = []
    for s in _allows(text):
        label = s["sid"]
        problems += [f"{label}: s3 action {a}" for a in s["actions"] if a.startswith("s3:")]
        problems += [
            f"{label}: iam verb outside the four reads {a}"
            for a in s["actions"]
            if a.startswith("iam:") and a not in _IAM_READS
        ]
        if "parameter/secret/" in s["resource_text"]:
            problems.append(f"{label}: names parameter/secret/")
        if "secretsmanager" in s["resource_text"] or any(a.startswith("secretsmanager:") for a in s["actions"]):
            problems.append(f"{label}: names secretsmanager")
    return problems


def dev_denies_problems(text: str, dev_text: str) -> list[str]:
    guard = {s["sid"]: s for s in _statements(text) if s["effect"] == "Deny"}
    dev = {s["sid"]: s for s in _statements(dev_text) if s["effect"] == "Deny"}
    problems = [f"missing Deny Sid {sid}" for sid in _DAILY_OPS_DENIES if sid not in guard or sid not in dev]
    for sid, statement in dev.items():
        if sid in guard and any(guard[sid][k] != statement[k] for k in ("actions", "resource_text", "condition_text")):
            problems.append(f"{sid}: differs from platform_dev_policies.tf")
        elif sid not in guard:
            problems.append(f"missing Deny Sid {sid} (a PlatformDev Deny)")
    return problems


def externalid_problems(text: str, tf_texts: dict[str, str]) -> list[str]:
    roles = set()
    for tf in tf_texts.values():
        clean = _strip_comments(tf)
        for _, rtype, name in (r for r in _resources(tf) if r[1] == "aws_iam_role"):
            head = re.search(rf'resource\s+"{rtype}"\s+"{name}"\s*\{{', clean)
            if head and "sts:ExternalId" in _block_body(clean, head.end() - 1):
                roles.add(name)
    deny = next((s for s in _statements(text) if s["sid"] == "DenyExternalIdTrustRead"), None)
    named = set(re.findall(r"aws_iam_role\.(\w+)\.arn", deny["resource_text"])) if deny else set()
    problems = (
        [] if roles >= {"platform_dev", "platform_admin"} else ["the ExternalId role set lacks PlatformDev and PlatformAdmin"]
    )
    return problems + (
        [] if roles == named else [f"DenyExternalIdTrustRead names {sorted(named)}, ExternalId trusts are {sorted(roles)}"]
    )


def escalation_problems(text: str) -> list[str]:
    deny = next((s for s in _statements(text) if s["sid"] == "DenySessionEscalation"), None)
    actions = set(deny["actions"]) if deny else set()
    return [f"DenySessionEscalation lacks {a}" for a in sorted(_ESCALATION - actions)] + [
        f"DenySessionEscalation has extra {a}" for a in sorted(actions - _ESCALATION)
    ]


def temporary_credential_problems(text: str) -> list[str]:
    deny = next((s for s in _statements(text) if s["sid"] == "DenyTemporaryCredentialUse"), None)
    ok = (
        deny
        and deny["actions"] == ["*"]
        and deny["resource_text"] == '"*"'
        and deny["condition_text"] == 'Null={"aws:TokenIssueTime"="false"}'
    )
    return [] if ok else ["no Deny of Action * on Resource * whose only condition is Null aws:TokenIssueTime = false"]


def key_resource_problems(tf_texts: dict[str, str]) -> list[str]:
    return [f"{name}: {rtype}" for name, tf in tf_texts.items() for _, rtype, _ in _resources(tf) if rtype in _KEY_TYPES]


def boundary_user_write_problems(boundary_text: str) -> list[str]:
    ceiling = next((s for s in _allows(boundary_text) if s["sid"] == "DataPlaneAllow"), None)
    actions = ceiling["actions"] if ceiling else []
    problems = (
        [] if {"iam:GetRole", "iam:PassRole"} <= set(actions) else ["the parsed ceiling lacks iam:GetRole and iam:PassRole"]
    )
    problems += [f"iam wildcard {a}" for a in actions if a.startswith("iam:") and "*" in a]
    return problems + [
        f"user-write verb {a}" for a in actions if a.startswith("iam:") and any(w in a for w in _BOUNDARY_USER_WRITE)
    ]


@pytest.fixture(scope="module")
def world() -> dict:
    return {
        "user": (_TF / "bootstrap" / _USER).read_text(encoding="utf-8"),
        "dev": (_TF / "bootstrap" / _DEV).read_text(encoding="utf-8"),
        "boundary": (_TF / "bootstrap" / _BOUNDARY).read_text(encoding="utf-8"),
        "all": _all_texts(),
    }


def _all_texts() -> dict[str, str]:
    return {
        f"{d.name}/{name}": text for d in sorted(_TF.iterdir()) if d.is_dir() for name, text in tf_dir_file_texts(d).items()
    }


def test_agent_session_user_carries_own_boundary(world: dict) -> None:
    assert own_boundary_problems(world["user"]) == []


def test_agent_session_invoke_is_api_only(world: dict) -> None:
    assert invoke_problems(world["user"]) == []


def test_agent_session_grants_are_closed(world: dict) -> None:
    assert closed_grants_problems(world["user"]) == []


def test_agent_session_never_reaches_state_or_secrets(world: dict) -> None:
    assert reach_problems(world["user"]) == []


def test_agent_session_carries_platform_dev_denies(world: dict) -> None:
    assert dev_denies_problems(world["user"], world["dev"]) == []


def test_agent_session_cannot_read_externalid_trusts(world: dict) -> None:
    assert externalid_problems(world["user"], world["all"]) == []


def test_agent_session_escalation_denied(world: dict) -> None:
    assert escalation_problems(world["user"]) == []


def test_agent_session_temporary_credentials_denied(world: dict) -> None:
    assert temporary_credential_problems(world["user"]) == []


def test_agent_session_has_no_key_resource(world: dict) -> None:
    assert key_resource_problems(world["all"]) == []


def test_agent_session_boundary_holds_no_user_write(world: dict) -> None:
    assert boundary_user_write_problems(world["boundary"]) == []


def _swap(key: str, old: str, new: str) -> Callable[[dict], dict]:
    def mutate(w: dict) -> dict:
        assert old in w[key], f"mutation anchor missing: {old!r}"
        return {**w, key: w[key].replace(old, new, 1)}

    return mutate


def _add_file(text: str) -> Callable[[dict], dict]:
    return lambda w: {**w, "all": {**w["all"], "bootstrap/extra.tf": text}}


_REDS: list[tuple[str, Callable[[dict], list[str]], Callable[[dict], dict], str]] = [
    (
        "extra Allow verb",
        lambda w: closed_grants_problems(w["user"]),
        _swap("user", 'Action   = "ssm:GetParameter"', 'Action   = ["ssm:GetParameter", "ssm:PutParameter"]'),
        "unexpected Allow action ssm:PutParameter",
    ),
    (
        "iam verb beyond the four reads",
        lambda w: reach_problems(w["user"]),
        _swap("user", '"iam:ListAttachedRolePolicies",', '"iam:ListAttachedRolePolicies",\n        "iam:ListRoles",'),
        "iam verb outside the four reads iam:ListRoles",
    ),
    (
        "lambda:InvokeFunctionUrl Allow",
        lambda w: invoke_problems(w["user"]),
        _swap("user", 'Action   = "execute-api:Invoke"', 'Action   = ["execute-api:Invoke", "lambda:InvokeFunctionUrl"]'),
        "lambda or apigateway action",
    ),
    (
        "apigateway:PATCH Allow",
        lambda w: invoke_problems(w["user"]),
        _swap("user", 'Action   = "execute-api:Invoke"', 'Action   = ["execute-api:Invoke", "apigateway:PATCH"]'),
        "lambda or apigateway action",
    ),
    (
        "wildcard stage",
        lambda w: invoke_problems(w["user"]),
        _swap("user", "/${local.ducklake_api_stage}/POST/${route}", "/*/POST/${route}"),
        "invoke Resource is not the exact route ARN template",
    ),
    (
        "extra literal route ARN",
        lambda w: invoke_problems(w["user"]),
        _swap("user", '${route}"]', '${route}", "arn:aws:execute-api:eu-west-2:1:x/live/POST/extra"]'),
        "invoke Resource is not the exact route ARN template",
    ),
    (
        "s3:GetObject Allow",
        lambda w: reach_problems(w["user"]),
        _swap("user", 'Action   = "ssm:GetParameter"', 'Action   = ["ssm:GetParameter", "s3:GetObject"]'),
        "s3 action s3:GetObject",
    ),
    (
        "parameter/secret/ Resource",
        lambda w: reach_problems(w["user"]),
        _swap("user", "parameter/agent-platform/ducklake/*", "parameter/secret/*"),
        "names parameter/secret/",
    ),
    (
        "secretsmanager ARN Resource",
        lambda w: reach_problems(w["user"]),
        _swap("user", ":parameter/agent-platform/ducklake/*", ":secretsmanager:secret:x"),
        "names secretsmanager",
    ),
    (
        "dropped DailyOps Deny Sid",
        lambda w: dev_denies_problems(w["user"], w["dev"]),
        _swap("user", 'Sid      = "DenyStateRead"', 'Sid      = "DenyStateReadX"'),
        "missing Deny Sid DenyStateRead",
    ),
    (
        "extra ExternalId role",
        lambda w: externalid_problems(w["user"], w["all"]),
        _add_file('resource "aws_iam_role" "extra" {\n  assume_role_policy = "sts:ExternalId"\n}\n'),
        "DenyExternalIdTrustRead names",
    ),
    (
        "iam:PassRole dropped from escalation",
        lambda w: escalation_problems(w["user"]),
        _swap("user", '          "iam:PassRole",\n', ""),
        "DenySessionEscalation lacks iam:PassRole",
    ),
    (
        "DenyTemporaryCredentialUse dropped",
        lambda w: temporary_credential_problems(w["user"]),
        _swap("user", '"DenyTemporaryCredentialUse"', '"DenyTemporaryCredentialUseX"'),
        "no Deny of Action *",
    ),
    (
        "aws_iam_access_key added",
        lambda w: key_resource_problems(w["all"]),
        _add_file('resource "aws_iam_access_key" "k" {\n}\n'),
        "aws_iam_access_key",
    ),
    (
        "boundary pointed at the shared boundary",
        lambda w: own_boundary_problems(w["user"]),
        _swap("user", "aws_iam_policy.agent_session_boundary.arn", "aws_iam_policy.github_ci_apply_boundary.arn"),
        "permissions_boundary is not",
    ),
    (
        "statement added to the boundary",
        lambda w: own_boundary_problems(w["user"]),
        _swap(
            "user",
            "local.agent_session_observe_statements)\n  })\n\n  agent_session_guard",
            "local.agent_session_observe_statements, local.extra)\n  })\n\n  agent_session_guard",
        ),
        "not exactly the concat",
    ),
    (
        "dropped prevent_destroy",
        lambda w: own_boundary_problems(w["user"]),
        _swap("user", "prevent_destroy = true", "prevent_destroy = false"),
        "prevent_destroy is not set",
    ),
    (
        "path argument on the user",
        lambda w: own_boundary_problems(w["user"]),
        _swap(
            "user",
            'name                 = "agent-platform-session"',
            'name                 = "agent-platform-session"\n  path                 = "/x/"',
        ),
        "sets a path argument",
    ),
    (
        "another user name",
        lambda w: own_boundary_problems(w["user"]),
        _swap("user", 'name                 = "agent-platform-session"', 'name                 = "agent-platform-other"'),
        "not exactly agent-platform-session",
    ),
    (
        "iam:CreateAccessKey on the shared ceiling",
        lambda w: boundary_user_write_problems(w["boundary"]),
        _swap("boundary", '"iam:GetRole",', '"iam:GetRole",\n          "iam:CreateAccessKey",'),
        "user-write verb iam:CreateAccessKey",
    ),
]


@pytest.mark.parametrize(("label", "predicate", "mutate", "expected"), _REDS, ids=[r[0] for r in _REDS])
def test_agent_session_predicates_are_red_on_each_mutation(
    world: dict, label: str, predicate: Callable, mutate: Callable, expected: str
) -> None:
    assert any(expected in problem for problem in predicate(mutate(world))), label
