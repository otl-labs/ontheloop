"""rec-5026: the management account's admin lane (user org-admin-operator, role OrganizationAdmin) is keyless and bounded.

Text tests over terraform/organization's management_admin.tf and its three policy templates (PLAN-sit-organization,
Decision 256 cl.3, Decision 237). Every predicate returns a problem list, so each red case drives the real predicate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import pytest

from tests.checks.iam_tf._platform_security_hcl import _attr, _resource_body, _resources, _strip_comments

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROOT = _REPO_ROOT / "terraform" / "organization"
_TEMPLATES = ("organization_admin", "organization_admin_trust", "assume_organization_admin")
_CLOUDSHELL = {
    "cloudshell:CreateEnvironment",
    "cloudshell:CreateSession",
    "cloudshell:GetEnvironmentStatus",
    "cloudshell:StartEnvironment",
    "cloudshell:StopEnvironment",
    "cloudshell:PutCredentials",
    "cloudshell:DescribeEnvironments",
}
_OPERATOR_ONLY = (
    "organizations:DeleteOrganization",
    "organizations:RemoveAccountFromOrganization",
    "organizations:CloseAccount",
    "organizations:LeaveOrganization",
    "organizations:DeletePolicy",
    "organizations:DeleteOrganizationalUnit",
    "iam:UpdateAssumeRolePolicy",
    "iam:CreateAccessKey",
    "cloudtrail:StopLogging",
    "cloudtrail:DeleteTrail",
    "cloudtrail:*",
)
_STAND_IN = "1" * 12
_TOTAL_LIMIT = 10_240
_OPS_LIMIT = 7_000


def _read() -> dict[str, str]:
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(_ROOT.glob("*.tf"))}
    files.update({f"{n}.json.tftpl": (_ROOT / "policies" / f"{n}.json.tftpl").read_text(encoding="utf-8") for n in _TEMPLATES})
    return files


def _rendered(text: str) -> str:
    return text.replace("${account_id}", _STAND_IN).replace("${region}", "xx-xxxx-0")


def _statements(files: dict[str, str], name: str = "organization_admin") -> list[dict]:
    return json.loads(_rendered(files[f"{name}.json.tftpl"]))["Statement"]


def _actions(stmt: dict) -> list[str]:
    value = stmt.get("Action", [])
    return [value] if isinstance(value, str) else list(value)


def _compact(text: str) -> str:
    return json.dumps(json.loads(_rendered(text)), separators=(",", ":"))


def lane_problems(files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    tf = {n: t for n, t in files.items() if n.endswith(".tf")}
    resources = [(t, n, name) for n, text in tf.items() for _, t, name in _resources(text)]
    for rtype, _, name in resources:
        if rtype in ("aws_iam_access_key", "aws_iam_user_login_profile", "aws_iam_virtual_mfa_device"):
            problems.append(f"forbidden resource {rtype}.{name}")
    for name, text in files.items():
        if name.endswith(".json.tftpl") and re.search(r"CreateAccessKey|CreateLoginProfile|CreateVirtualMFADevice", text):
            problems.append(f"{name}: an access-key or login verb")
        if name.endswith(".json.tftpl") and re.search(r'"iam:\*"', text):
            problems.append(f"{name}: an iam:* grant")
    admin = _strip_comments(files["management_admin.tf"])
    calls = re.findall(r"templatefile\(", admin)
    guarded = re.findall(
        r"try\(templatefile\(\"\$\{path\.module\}/policies/\w+\.json\.tftpl\", [^)]*\), local\.(\w+)\)", admin
    )
    if len(calls) != 3 or sorted(guarded) != ["deny_all_policy_json", "deny_all_policy_json", "deny_assume_trust_json"]:
        problems.append("every templatefile call must sit in try() with its fail-closed fallback")
    for local, needle in (("deny_all_policy_json", '"Deny"'), ("deny_assume_trust_json", "sts:AssumeRole")):
        m = re.search(rf"{local}\s*=\s*jsonencode\(.*?\n  \}}\)", admin, re.S)
        if m is None or needle not in m.group(0):
            problems.append(f"fallback {local} is not a Deny")
    if '"*"' not in re.search(r"deny_assume_trust_json\s*=.*?\n  \}\)", admin, re.S).group(0):  # type: ignore[union-attr]
        problems.append("the trust fallback does not name every principal")
    for rtype, rname in (("aws_iam_user", "org_admin_operator"), ("aws_iam_role", "organization_admin")):
        body = _resource_body(admin, rtype, rname) or ""
        for banned in ("description", "path", "permissions_boundary"):
            if _attr(body, banned) is not None:
                problems.append(f"{rtype}.{rname} sets {banned}")
    return problems


def trust_problems(files: dict[str, str]) -> list[str]:
    trust = json.loads(_rendered(files["organization_admin_trust.json.tftpl"]))["Statement"]
    problems = []
    if len(trust) != 1 or trust[0].get("Effect") != "Allow":
        return ["the trust holds other than one Allow statement"]
    if trust[0]["Principal"] != {"AWS": f"arn:aws:iam::{_STAND_IN}:user/org-admin-operator"}:
        problems.append("the trust admits other than the org-admin-operator user")
    if trust[0].get("Condition") != {"Bool": {"aws:MultiFactorAuthPresent": "true"}}:
        problems.append("the trust does not require aws:MultiFactorAuthPresent true")
    user = json.loads(_rendered(files["assume_organization_admin.json.tftpl"]))["Statement"]
    if user != [
        {"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": f"arn:aws:iam::{_STAND_IN}:role/OrganizationAdmin"}
    ]:
        problems.append("the user's policy is not only sts:AssumeRole on OrganizationAdmin")
    return problems


def grant_problems(files: dict[str, str]) -> list[str]:
    stmts = _statements(files)
    by_sid = {s["Sid"]: s for s in stmts}
    problems = []
    if set(_actions(by_sid.get("CloudShellUse", {}))) != _CLOUDSHELL or len(_actions(by_sid["CloudShellUse"])) != 7:
        problems.append("CloudShellUse is not exactly its seven verbs")
    every = [a for s in stmts if s["Effect"] == "Allow" for a in _actions(s)]
    for verb in _OPERATOR_ONLY:
        if verb in every:
            problems.append(f"OrganizationAdminOps grants operator-only {verb}")
    for action in every:
        if re.search(r":Delete", action) and action != "s3:DeleteObject":
            problems.append(f"a Delete verb outside the state lock object: {action}")
    deletes = [s for s in stmts if "s3:DeleteObject" in _actions(s)]
    if [s["Sid"] for s in deletes] != ["StateObjects"] or any("tflock" not in "".join(s["Resource"]) for s in deletes):
        problems.append("s3:DeleteObject is not confined to StateObjects and the .tflock object")
    enable = by_sid.get("OrgTrustedAccessIam", {})
    if _actions(enable) != ["organizations:EnableAWSServiceAccess"] or "organizations:ServicePrincipal" not in json.dumps(
        enable.get("Condition")
    ):
        problems.append("EnableAWSServiceAccess does not sit alone under its iam.amazonaws.com condition")
    if any("organizations:EnableAWSServiceAccess" in _actions(s) for s in stmts if s["Sid"] != "OrgTrustedAccessIam"):
        problems.append("EnableAWSServiceAccess sits outside its own Sid")
    ssm = by_sid.get("TfvarsRead", {})
    if _actions(ssm) != ["ssm:GetParameter"] or not re.fullmatch(
        rf"arn:aws:ssm:xx-xxxx-0:{_STAND_IN}:parameter/secret/agent-platform-terraform-organization-tfvars",
        ssm.get("Resource", ""),
    ):
        problems.append("the SSM grant is not ssm:GetParameter alone on the one literal parameter ARN")
    if any(a.startswith("iam:") and a.endswith("AssumeRolePolicy") for a in every):
        problems.append("a trust change is granted")
    ops = len(_compact(files["organization_admin.json.tftpl"]))
    if ops >= _OPS_LIMIT:
        problems.append(f"OrganizationAdminOps is {ops} characters, over {_OPS_LIMIT}")
    total = ops + sum(
        len(m.group(0))
        for m in re.finditer(
            r"aws_iam_role_policy\"\s+\"(?!organization_admin_ops)\w+\"[^}]*role\s*=\s*aws_iam_role\.organization_admin",
            "".join(files.values()),
        )
    )
    if total >= _TOTAL_LIMIT:
        problems.append(f"the role's inline policies total {total} characters")
    return problems


def sid_set_problems(files: dict[str, str]) -> list[str]:
    """Each Sid's actions equal its declared write or refresh-read set, so a missing refresh read is red."""
    expected = {
        "OrgRead": {"organizations:Describe*", "organizations:List*"},
        "RootFeatures": {
            "iam:EnableOrganizationsRootCredentialsManagement",
            "iam:EnableOrganizationsRootSessions",
            "iam:ListOrganizationsFeatures",
            "iam:GetAccountSummary",
        },
        "OrgSecurityTrailRead": {
            "cloudtrail:GetTrail",
            "cloudtrail:GetTrailStatus",
            "cloudtrail:DescribeTrails",
            "cloudtrail:GetEventSelectors",
            "cloudtrail:GetInsightSelectors",
            "cloudtrail:ListTags",
        },
        "LogsRead": {"logs:DescribeLogGroups", "logs:DescribeMetricFilters", "logs:TestMetricFilter"},
        "AlarmRead": {"cloudwatch:DescribeAlarms", "cloudwatch:GetMetricStatistics"},
        "TopicSubscriptionRead": {"sns:ListTopics", "sns:GetSubscriptionAttributes"},
        "StateObjects": {"s3:GetObject", "s3:PutObject", "s3:DeleteObject"},
        "DeliveryRolePass": {"iam:PassRole"},
        "SelfUser": {
            "iam:GetUser",
            "iam:TagUser",
            "iam:ListUserTags",
            "iam:GetUserPolicy",
            "iam:ListUserPolicies",
            "iam:ListAttachedUserPolicies",
            "iam:PutUserPolicy",
        },
        "SelfRole": {
            "iam:GetRole",
            "iam:TagRole",
            "iam:ListRoleTags",
            "iam:GetRolePolicy",
            "iam:ListRolePolicies",
            "iam:ListAttachedRolePolicies",
            "iam:PutRolePolicy",
        },
    }
    by_sid = {s["Sid"]: s for s in _statements(files)}
    problems = []
    for sid, actions in expected.items():
        got = _actions(by_sid.get(sid, {}))
        if set(got) != actions or len(got) != len(actions):
            problems.append(f"{sid} actions differ from its declared set")
    bucket = set(_actions(by_sid.get("BucketManage", {})))
    for refresh in ("s3:GetBucketAcl", "s3:GetBucketPolicy", "s3:GetBucketOwnershipControls", "s3:GetBucketTagging"):
        if refresh not in bucket:
            problems.append(f"BucketManage lacks the refresh read {refresh}")
    return problems


def template_syntax_problems(files: dict[str, str]) -> list[str]:
    problems = []
    for name in _TEMPLATES:
        text = files[f"{name}.json.tftpl"]
        if set(re.findall(r"\$\{(\w+)\}", text)) - {"account_id", "region"}:
            problems.append(f"{name}: a placeholder other than account_id and region")
        if "$${" in text or "%{" in text:
            problems.append(f"{name}: a template escape or directive")
    return problems


_PREDICATES = (lane_problems, trust_problems, grant_problems, sid_set_problems, template_syntax_problems)


@pytest.mark.parametrize("predicate", _PREDICATES, ids=lambda p: p.__name__)
def test_real_tree(predicate: Callable[[dict[str, str]], list[str]]) -> None:
    assert predicate(_read()) == []


def _edit(file: str, old: str, new: str) -> Callable[[dict[str, str]], dict[str, str]]:
    return lambda f: {**f, file: f[file].replace(old, new, 1)}


_ADMIN = "organization_admin.json.tftpl"
_REDS: dict[str, tuple[Callable[[dict[str, str]], list[str]], Callable[[dict[str, str]], dict[str, str]]]] = {
    "access key resource": (
        lane_problems,
        lambda f: {
            **f,
            "management_admin.tf": f["management_admin.tf"] + 'resource "aws_iam_access_key" "x" {\n  user = "u"\n}\n',
        },
    ),
    "templatefile outside try": (
        lane_problems,
        _edit("management_admin.tf", "try(templatefile(", "(templatefile("),
    ),
    "user description": (
        lane_problems,
        _edit("management_admin.tf", '  name = "org-admin-operator"', '  name = "org-admin-operator"\n  path = "/x/"'),
    ),
    "trust without mfa": (
        trust_problems,
        _edit("organization_admin_trust.json.tftpl", "aws:MultiFactorAuthPresent", "aws:Other"),
    ),
    "trust other principal": (
        trust_problems,
        _edit("organization_admin_trust.json.tftpl", "user/org-admin-operator", "root"),
    ),
    "user policy widened": (trust_problems, _edit("assume_organization_admin.json.tftpl", "role/OrganizationAdmin", "role/*")),
    "cloudshell verb added": (
        grant_problems,
        _edit(_ADMIN, '"cloudshell:PutCredentials"', '"cloudshell:PutCredentials", "cloudshell:UploadFile"'),
    ),
    "operator-only verb": (
        grant_problems,
        _edit(_ADMIN, '"organizations:TagResource"', '"organizations:TagResource", "organizations:CloseAccount"'),
    ),
    "trust change granted": (
        grant_problems,
        _edit(_ADMIN, '"iam:PutRolePolicy"]', '"iam:PutRolePolicy", "iam:UpdateAssumeRolePolicy"]'),
    ),
    "delete verb": (
        grant_problems,
        _edit(_ADMIN, '"logs:PutMetricFilter"', '"logs:PutMetricFilter", "logs:DeleteMetricFilter"'),
    ),
    "service access shares a sid": (
        grant_problems,
        _edit(
            _ADMIN, '"organizations:UntagResource"', '"organizations:UntagResource", "organizations:EnableAWSServiceAccess"'
        ),
    ),
    "tfvars grant widened": (grant_problems, _edit(_ADMIN, '"ssm:GetParameter"', '["ssm:GetParameter", "ssm:PutParameter"]')),
    "oversize": (
        grant_problems,
        _edit(_ADMIN, '"Version": "2012-10-17"', '"Version": "2012-10-17", "Pad": "' + "x" * 7000 + '"'),
    ),
    "refresh read dropped": (sid_set_problems, _edit(_ADMIN, '"s3:GetBucketAcl", ', "")),
    "log read dropped": (sid_set_problems, _edit(_ADMIN, '"logs:TestMetricFilter"', '"logs:Other"')),
    "extra placeholder": (template_syntax_problems, _edit(_ADMIN, "${region}", "${other}")),
    "template escape": (template_syntax_problems, _edit(_ADMIN, "${region}", "$${region}")),
}


@pytest.mark.parametrize("label", sorted(_REDS))
def test_red_case(label: str) -> None:
    predicate, mutate = _REDS[label]
    base = _read()
    mutated = mutate(base)
    assert mutated != base, f"red case {label!r} did not mutate anything"
    assert predicate(mutated) != [], f"red case {label!r} was not detected"
