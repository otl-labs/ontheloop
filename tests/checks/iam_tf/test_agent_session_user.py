"""Decision 255: the agent-platform-session IAM user declares exactly the grants sessions use.

PLAN-agent-proxy-signed-access.

Credential-free text tests over terraform/bootstrap/agent_session_user.tf in the iam_tf convention: each check is a
predicate over Terraform text that returns a problem list, so its red case drives the real predicate on mutated input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

from tests.checks.iam_tf._platform_security_hcl import _resource_body, _resources, _strip_comments

_TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[{}\[\]()=,]|[^\s{}\[\]()=,"]+)')


def _tokens(text: str) -> list[str]:
    return [m.group(1) for m in _TOKEN.finditer(text)]


def _value(toks: list[str], i: int):
    t = toks[i]
    if t == "{":
        out: dict = {}
        i += 1
        while toks[i] != "}":
            if toks[i] == ",":
                i += 1
                continue
            key = toks[i].strip('"')
            assert toks[i + 1] == "=", f"expected = after {key}"
            out[key], i = _value(toks, i + 2)
        return out, i + 1
    if t == "[":
        items: list = []
        i += 1
        while toks[i] != "]":
            if toks[i] == ",":
                i += 1
                continue
            v, i = _value(toks, i)
            items.append(v)
        return items, i + 1
    if t == "jsonencode" and toks[i + 1] == "(":
        v, i = _value(toks, i + 2)
        assert toks[i] == ")"
        return v, i + 1
    if t.startswith('"'):
        return t[1:-1], i + 1
    return t, i + 1  # a bare reference or literal, such as local.x or true


def policy_local(text: str, name: str) -> dict:
    """The jsonencode body assigned to `name = jsonencode(...)`, as nested dicts and lists."""
    clean = _strip_comments(text)
    m = re.search(rf"\b{re.escape(name)}\s*=\s*jsonencode\(", clean)
    assert m is not None, f"local {name} not found"
    toks = _tokens(clean[m.start() :])
    return _value(toks, 2)[0]


def statements(doc: dict) -> list[dict]:
    return list(doc.get("Statement") or [])


def as_list(v) -> list:
    return v if isinstance(v, list) else [v]


_ROOT = Path(__file__).resolve().parents[3]
_TF = _ROOT / "terraform"
_BOOTSTRAP = _TF / "bootstrap"

_ALLOWED_ACTIONS = {
    "lambda:InvokeFunctionUrl",
    "lambda:InvokeFunction",
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
_IAM_READS = {a for a in _ALLOWED_ACTIONS if a.startswith("iam:")}
_INVOKE_LOCALS = {
    "ducklake_writer_function",
    "ducklake_reader_function",
    "ducklake_telemetry_writer_function",
    "ducklake_telemetry_reader_function",
}
_DAILYOPS_DENIES = {
    "DenyStateAndConvergenceWrite",
    "DenyStateRead",
    "DenyTerraformArtifactRead",
    "DenyTerraformArtifactWrite",
    "DenyReviewTranscriptRead",
    "DenyReviewTranscriptWrite",
    "DenyStateAndConvergenceVersionRead",
    "DenyStateAndConvergenceVersionList",
}
_ESCALATION = {
    "sts:AssumeRole*",
    "sts:AssumeRoot",
    "sts:GetFederationToken",
    "iam:Create*",
    "iam:Update*",
    "iam:Put*",
    "iam:Attach*",
    "iam:Detach*",
    "iam:Delete*",
    "iam:Add*",
    "iam:Remove*",
    "iam:Tag*",
    "iam:Untag*",
    "iam:Set*",
    "iam:Upload*",
    "iam:Enable*",
    "iam:Deactivate*",
    "iam:Change*",
    "iam:Reset*",
    "iam:PassRole",
}
_KEY_TYPES = {
    "aws_iam_access_key",
    "aws_iam_user_policy",
    "aws_iam_user_login_profile",
    "aws_iam_service_specific_credential",
    "aws_iam_signing_certificate",
    "aws_iam_user_ssh_key",
}
_USER_WRITE = re.compile(
    r"User|AccessKey|LoginProfile|PolicyVersion|ServiceSpecificCredential|SSHPublicKey|SigningCertificate|MFA"
)


def _agent_text() -> str:
    return (_BOOTSTRAP / "agent_session_user.tf").read_text(encoding="utf-8")


def _boundary_text() -> str:
    return (_BOOTSTRAP / "github_ci_apply_boundary.tf").read_text(encoding="utf-8")


def _dev_text() -> str:
    return (_BOOTSTRAP / "platform_dev_policies.tf").read_text(encoding="utf-8")


def _tf_texts() -> dict[str, str]:
    return {
        str(p.relative_to(_TF)): p.read_text(encoding="utf-8")
        for p in sorted(_TF.rglob("*.tf"))
        if ".terraform" not in p.parts
    }


def _stmts(text: str, which: str) -> list[dict]:
    return statements(policy_local(text, f"agent_session_{which}_policy_json"))


def _all_stmts(text: str) -> list[dict]:
    return [s for w in ("ducklake", "observe", "guard") for s in _stmts(text, w)]


def _allows(text: str) -> list[dict]:
    return [s for s in _all_stmts(text) if s.get("Effect") == "Allow"]


def _local_refs(resources) -> set[str]:
    return {m.group(1) for r in as_list(resources) for m in re.finditer(r"\$\{local\.(\w+)\}", str(r))}


def user_problems(text: str) -> list[str]:
    body = _strip_comments(_resource_body(text, "aws_iam_user", "agent_session") or "")
    problems = []
    if not re.search(r"permissions_boundary\s*=\s*aws_iam_policy\.github_ci_apply_boundary\.arn", body):
        problems.append("user lacks the permissions boundary")
    if not re.search(r"prevent_destroy\s*=\s*true", body):
        problems.append("user lacks prevent_destroy")
    return problems


def invoke_problems(text: str) -> list[str]:
    problems = []
    seen: set[str] = set()
    for s in _stmts(text, "ducklake"):
        acts = set(as_list(s.get("Action")))
        if "lambda:InvokeFunction" in acts and s.get("Condition") != {"Bool": {"lambda:InvokedViaFunctionUrl": "true"}}:
            problems.append(f"{s['Sid']}: lambda:InvokeFunction lacks Bool lambda:InvokedViaFunctionUrl = true")
        if "lambda:InvokeFunctionUrl" in acts and s.get("Condition"):
            problems.append(f"{s['Sid']}: lambda:InvokeFunctionUrl must be unconditioned")
        if acts & {"lambda:InvokeFunction", "lambda:InvokeFunctionUrl"}:
            refs = _local_refs(s.get("Resource"))
            seen |= refs
            if refs != _INVOKE_LOCALS:
                problems.append(f"{s['Sid']}: invoke targets {sorted(refs)} != the four DuckLake function locals")
    if not seen:
        problems.append("no invoke statement parsed")
    return problems


def closed_problems(text: str) -> list[str]:
    acts = {a for s in _allows(text) for a in as_list(s.get("Action"))}
    return [f"unlisted Allow action {a}" for a in sorted(acts - _ALLOWED_ACTIONS)] + [
        f"declared action {a} no longer granted" for a in sorted(_ALLOWED_ACTIONS - acts)
    ]


def reach_problems(text: str) -> list[str]:
    problems = []
    for s in _allows(text):
        for a in as_list(s.get("Action")):
            if a.startswith("s3:"):
                problems.append(f"{s['Sid']}: s3 action {a}")
            if a.startswith("iam:") and a not in _IAM_READS:
                problems.append(f"{s['Sid']}: iam verb {a} beyond the four reads")
            if a.startswith("secretsmanager:"):
                problems.append(f"{s['Sid']}: secretsmanager action {a}")
        for r in as_list(s.get("Resource")):
            if "parameter/secret/" in str(r) or "secretsmanager" in str(r):
                problems.append(f"{s['Sid']}: secret resource {r}")
    return problems


def denies_problems(agent_text: str, dev_text: str) -> list[str]:
    dev = {
        s["Sid"]: s for s in statements(policy_local(dev_text, "platform_dev_runtime_policy_json")) if s["Effect"] == "Deny"
    }
    guard = {s["Sid"]: s for s in _stmts(agent_text, "guard")}
    problems = [f"PlatformDev Deny {n} missing from the guard policy" for n in sorted(_DAILYOPS_DENIES - set(dev))]
    for sid, d in dev.items():
        g = guard.get(sid)
        if g is None:
            problems.append(f"guard policy lacks PlatformDev Deny {sid}")
        elif any(g.get(k) != d.get(k) for k in ("Effect", "Action", "Resource", "Condition")):
            problems.append(f"guard Deny {sid} differs from PlatformDev's")
    return problems


def _externalid_roles(tf_texts: dict[str, str]) -> set[str]:
    out = set()
    for text in tf_texts.values():
        for kind, rtype, rname in _resources(text):
            if (
                kind == "resource"
                and rtype == "aws_iam_role"
                and "sts:ExternalId" in (_resource_body(text, rtype, rname) or "")
            ):
                out.add(rname)
    return out


def trust_problems(agent_text: str, tf_texts: dict[str, str]) -> list[str]:
    guard = {s["Sid"]: s for s in _stmts(agent_text, "guard")}.get("DenyExternalIdTrustRead") or {}
    named = {
        m.group(1)
        for r in as_list(guard.get("Resource", []))
        for m in [re.fullmatch(r"aws_iam_role\.(\w+)\.arn", str(r))]
        if m
    }
    actual = _externalid_roles(tf_texts)
    problems = (
        []
        if guard.get("Action") == "iam:GetRole" or guard.get("Action") == ["iam:GetRole"]
        else ["trust Deny is not iam:GetRole"]
    )
    problems += [f"ExternalId-trust role {r} not denied" for r in sorted(actual - named)]
    problems += [f"Deny names {r}, which has no ExternalId trust" for r in sorted(named - actual)]
    if not {"platform_dev", "platform_admin"} <= actual:
        problems.append("ExternalId role parse is vacuous")
    return problems


def escalation_problems(text: str) -> list[str]:
    s = {x["Sid"]: x for x in _stmts(text, "guard")}.get("DenySessionEscalation") or {}
    acts = set(as_list(s.get("Action", [])))
    return [f"escalation Deny lacks {a}" for a in sorted(_ESCALATION - acts)] + [
        f"escalation Deny has extra {a}" for a in sorted(acts - _ESCALATION)
    ]


def temporary_credential_problems(text: str) -> list[str]:
    for s in _stmts(text, "guard"):
        if (
            s.get("Effect") == "Deny"
            and s.get("Action") == "*"
            and s.get("Resource") == "*"
            and s.get("Condition") == {"Null": {"aws:TokenIssueTime": "false"}}
        ):
            return []
    return ["no Deny of * on * conditioned only on Null aws:TokenIssueTime = false"]


def key_problems(tf_texts: dict[str, str]) -> list[str]:
    return [
        f"{name}: {rtype}.{rname} declared"
        for name, text in tf_texts.items()
        for kind, rtype, rname in _resources(text)
        if kind == "resource" and rtype in _KEY_TYPES
    ]


def boundary_problems(text: str) -> list[str]:
    allow = next(
        (s for s in statements(policy_local(text, "github_ci_apply_boundary_policy_json")) if s["Sid"] == "DataPlaneAllow"), {}
    )
    iam = [a for a in as_list(allow.get("Action", [])) if a.startswith("iam:")]
    problems = [f"boundary ceiling names {a}" for a in iam if _USER_WRITE.search(a) or "*" in a]
    if not {"iam:GetRole", "iam:PassRole"} <= set(iam):
        problems.append("boundary ceiling parse is vacuous")
    return problems


def _sub(text: str, old: str, new: str) -> str:
    assert old in text, f"mutation anchor {old!r} not found"
    return text.replace(old, new, 1)


def _extra_role(tf: dict[str, str]) -> dict[str, str]:
    role = 'resource "aws_iam_role" "extra" {\n  assume_role_policy = "sts:ExternalId"\n}\n'
    return {**tf, "bootstrap/extra.tf": role}


def _extra_key(tf: dict[str, str]) -> dict[str, str]:
    return {**tf, "bootstrap/extra.tf": 'resource "aws_iam_access_key" "k" {\n  user = "x"\n}\n'}


_COND = (
    '        Condition = {\n          Bool = {\n            "lambda:InvokedViaFunctionUrl" = "true"\n          }\n        }\n'
)
_ENDPOINT = '"arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/*"'
_FN = '"arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_reader_function}",\n'
_A = _agent_text
_MUTATIONS: dict[str, tuple[Callable[[], list[str]], str]] = {
    "an extra Allow verb": (
        lambda: closed_problems(
            _sub(_A(), 'Action = ["ssm:GetParameter"]', 'Action = ["ssm:GetParameter", "ssm:PutParameter"]')
        ),
        "unlisted Allow action",
    ),
    "an iam verb beyond the four reads": (
        lambda: reach_problems(_sub(_A(), '"iam:ListRolePolicies",', '"iam:ListRolePolicies", "iam:ListRoles",')),
        "beyond the four reads",
    ),
    "an invoke grant without the Bool condition": (lambda: invoke_problems(_sub(_A(), _COND, "")), "lacks Bool"),
    "a fifth function in the invoke Resources": (
        lambda: invoke_problems(
            _sub(
                _A(),
                _FN,
                _FN + '          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.smoke_fn}",\n',
            )
        ),
        "invoke targets",
    ),
    "an s3:GetObject Allow": (
        lambda: reach_problems(_sub(_A(), 'Action = ["ssm:GetParameter"]', 'Action = ["ssm:GetParameter", "s3:GetObject"]')),
        "s3 action",
    ),
    "a parameter/secret/ resource": (
        lambda: reach_problems(_sub(_A(), _ENDPOINT, '"arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/x"')),
        "secret resource",
    ),
    "a secretsmanager ARN resource": (
        lambda: reach_problems(_sub(_A(), _ENDPOINT, '"arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:x"')),
        "secret resource",
    ),
    "a dropped DailyOps Deny Sid": (
        lambda: denies_problems(_sub(_A(), 'Sid      = "DenyStateRead"', 'Sid      = "DenyStateReadX"'), _dev_text()),
        "DenyStateRead",
    ),
    "an extra ExternalId-trust role": (lambda: trust_problems(_A(), _extra_role(_tf_texts())), "not denied"),
    "PassRole dropped from the escalation Deny": (
        lambda: escalation_problems(_sub(_A(), '          "iam:PassRole",\n', "")),
        "lacks iam:PassRole",
    ),
    "the temporary-credential Deny dropped": (
        lambda: temporary_credential_problems(_sub(_A(), '"aws:TokenIssueTime" = "false"', '"aws:TokenIssueTime" = "true"')),
        "no Deny of *",
    ),
    "an access-key resource": (lambda: key_problems(_extra_key(_tf_texts())), "aws_iam_access_key"),
    "a dropped permissions boundary": (
        lambda: user_problems(_sub(_A(), "  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn\n", "")),
        "permissions boundary",
    ),
    "a dropped prevent_destroy": (
        lambda: user_problems(_sub(_A(), "prevent_destroy = true", "prevent_destroy = false")),
        "prevent_destroy",
    ),
    "iam:CreateAccessKey added to the boundary ceiling": (
        lambda: boundary_problems(
            _sub(_boundary_text(), '"iam:GetRole",', '"iam:GetRole",\n          "iam:CreateAccessKey",')
        ),
        "iam:CreateAccessKey",
    ),
}


def test_agent_session_user_carries_boundary() -> None:
    assert user_problems(_agent_text()) == []


def test_agent_session_invoke_is_url_only() -> None:
    assert invoke_problems(_agent_text()) == []


def test_agent_session_grants_are_closed() -> None:
    assert closed_problems(_agent_text()) == []


def test_agent_session_never_reaches_state_or_secrets() -> None:
    assert reach_problems(_agent_text()) == []


def test_agent_session_carries_platform_dev_denies() -> None:
    assert denies_problems(_agent_text(), _dev_text()) == []


def test_agent_session_cannot_read_externalid_trusts() -> None:
    assert trust_problems(_agent_text(), _tf_texts()) == []


def test_agent_session_escalation_denied() -> None:
    assert escalation_problems(_agent_text()) == []


def test_agent_session_temporary_credentials_denied() -> None:
    assert temporary_credential_problems(_agent_text()) == []


def test_agent_session_has_no_key_resource() -> None:
    assert key_problems(_tf_texts()) == []


def test_agent_session_boundary_holds_no_user_write() -> None:
    assert boundary_problems(_boundary_text()) == []


@pytest.mark.parametrize("label", sorted(_MUTATIONS))
def test_agent_session_predicates_are_red_on_each_mutation(label: str) -> None:
    run, expected = _MUTATIONS[label]
    problems = run()
    assert any(expected in p for p in problems), f"{label}: {problems}"
