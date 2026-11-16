"""rec-5026 acceptance: terraform/organization declares the Organization, its OUs and the baseline SCP.

Text tests over the root (PLAN-sit-organization): feature set ALL with SCPs enabled, OUs prod and sit, the four
baseline statements, the sit attachment, the size precondition, the name family, no literal ids and the committed
lock. Every predicate returns a problem list, so each red case drives the real predicate.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

import pytest
import yaml

from tests.checks.iam_tf._platform_security_hcl import (
    _attr,
    _block_body,
    _local_map,
    _resource_body,
    _resources,
    _strip_comments,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROOT = _REPO_ROOT / "terraform" / "organization"
_STATEMENT_KEYS = ("deny_leave_organization", "deny_root_user", "protect_cloudtrail", "deny_other_regions")
_FIXED_NAMES = {
    "org-admin-operator",
    "OrganizationAdmin",
    "AssumeOrganizationAdmin",
    "OrganizationAdminOps",
    "prod",
    "sit",
    "baseline",
}
_NAME_ATTRS = {
    "aws_iam_user": "name",
    "aws_iam_user_policy": "name",
    "aws_iam_role": "name",
    "aws_iam_role_policy": "name",
    "aws_s3_bucket": "bucket",
    "aws_cloudtrail": "name",
    "aws_cloudwatch_log_group": "name",
    "aws_sns_topic": "name",
    "aws_cloudwatch_metric_alarm": "alarm_name",
    "aws_organizations_organizational_unit": "name",
    "aws_organizations_policy": "name",
}
_ORG_RESOURCES = (
    "aws_organizations_organization",
    "aws_organizations_organizational_unit",
    "aws_organizations_policy",
)


def _texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_ROOT.glob("*.tf"))}


def _body(texts: dict[str, str], rtype: str, rname: str) -> str:
    for text in texts.values():
        body = _resource_body(text, rtype, rname)
        if body is not None:
            return body
    return ""


def _statement(texts: dict[str, str], key: str) -> str:
    text = _strip_comments(texts["scps.tf"])
    m = re.search(rf"^\s*{key}\s*=\s*\{{", text, re.M)
    return "" if m is None else _block_body(texts["scps.tf"], m.end() - 1)


def organization_problems(texts: dict[str, str]) -> list[str]:
    org = _body(texts, "aws_organizations_organization", "this")
    problems = []
    if _attr(org, "feature_set") != '"ALL"':
        problems.append("feature_set is not ALL")
    if _attr(org, "enabled_policy_types") != '["SERVICE_CONTROL_POLICY"]':
        problems.append("SERVICE_CONTROL_POLICY is not the enabled policy type")
    if _attr(org, "aws_service_access_principals") != '["iam.amazonaws.com"]':
        problems.append("aws_service_access_principals is not exactly iam.amazonaws.com")
    for ou in ("prod", "sit"):
        if f'name      = "{ou}"' not in _body(texts, "aws_organizations_organizational_unit", ou):
            problems.append(f"OU {ou} is not declared with name {ou}")
    features = _body(texts, "aws_iam_organizations_features", "this")
    if _attr(features, "enabled_features") != '["RootCredentialsManagement", "RootSessions"]':
        problems.append("both root features are not enabled")
    if "depends_on = [aws_organizations_organization.this]" not in features:
        problems.append("root features do not depend on the organization")
    for rtype in _ORG_RESOURCES[:2]:
        for _, t, n in (r for text in texts.values() for r in _resources(text)):
            if t == rtype and "prevent_destroy = true" not in _body(texts, t, n):
                problems.append(f"{t}.{n} lacks prevent_destroy")
    return problems


def baseline_problems(texts: dict[str, str]) -> list[str]:
    problems = []
    for key in _STATEMENT_KEYS:
        if not _statement(texts, key):
            problems.append(f"baseline statement {key} is missing")
    leave = _statement(texts, "deny_leave_organization")
    if '"Deny"' not in leave or "organizations:LeaveOrganization" not in leave:
        problems.append("deny_leave_organization does not Deny organizations:LeaveOrganization")
    root = _statement(texts, "deny_root_user")
    if '"aws:AssumedRoot" = "true"' not in root or "Null" not in root or '"arn:aws:iam::*:root"' not in root:
        problems.append("deny_root_user lacks the Null aws:AssumedRoot true condition")
    cloudtrail = _statement(texts, "protect_cloudtrail")
    if "Condition" in cloudtrail:
        problems.append("protect_cloudtrail carries a Condition (no exemption here)")
    if re.search(r'"(?:cloudtrail|logs):(?:Get|Describe|List|Filter|Lookup)', cloudtrail):
        problems.append("protect_cloudtrail denies a read verb")
    for action in ("sts:*", "iam:*", "organizations:*", "support:*"):
        if f'"{action}"' not in _strip_comments(texts["scps.tf"]).split("baseline_statements")[0]:
            problems.append(f"global_actions lacks {action}")
    stages = _attr(texts["scps.tf"], "prod_baseline_stages")
    if stages is None or not stages.startswith("["):
        problems.append("prod_baseline_stages is not a list")
    elif set(re.findall(r'"(\w+)"', stages)) - set(_STATEMENT_KEYS):
        problems.append("prod_baseline_stages names a key that is not a baseline statement")
    policy = _body(texts, "aws_organizations_policy", "baseline")
    if "precondition" not in policy or "<= 5120" not in policy:
        problems.append("the baseline policy has no 5,120 size precondition")
    attach = _body(texts, "aws_organizations_policy_attachment", "baseline_sit")
    if "aws_organizations_policy.baseline.id" not in attach or "organizational_unit.sit.id" not in attach:
        problems.append("baseline is not attached to the sit OU")
    return problems


def hygiene_problems(texts: dict[str, str]) -> list[str]:
    problems = []
    for name, text in texts.items():
        code = _strip_comments(text)
        if re.search(r"(?<!\d)\d{12}(?!\d)", text):
            problems.append(f"{name}: a 12-digit literal")
        if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            problems.append(f"{name}: an e-mail literal")
        if name != "variables.tf" and re.search(r"\b[a-z]{2}-[a-z]+-\d\b", code):
            problems.append(f"{name}: a region literal")
        for _, rtype, rname in _resources(text):
            attr = _NAME_ATTRS.get(rtype)
            if attr is None:
                continue
            value = _attr(_resource_body(text, rtype, rname) or "", attr)
            if value is None:
                problems.append(f"{name}: {rtype}.{rname} has no {attr}")
            elif (
                value.startswith('"')
                and value.strip('"') not in _FIXED_NAMES
                and not value.strip('"').startswith("agent-platform-")
            ):
                problems.append(f"{name}: {rtype}.{rname} name {value} is outside the agent-platform- family")
    for key in _local_map(texts["trail.tf"], "org_security_filter_patterns"):
        if not key.startswith("agent-platform-"):
            problems.append(f"filter key {key} is outside the agent-platform- family")
    backend = re.search(r'backend\s+"s3"\s*\{([^}]*)\}', _strip_comments(texts["main.tf"]), re.S)
    if backend is None or re.search(r"^\s*region\s*=", backend.group(1), re.M):
        problems.append("the s3 backend is missing or sets a region")
    return problems


def state_bucket_problems(texts: dict[str, str]) -> list[str]:
    text = texts["state_bucket.tf"]
    problems = []
    needed = {
        "aws_s3_bucket_versioning": 'status = "Enabled"',
        "aws_s3_bucket_public_access_block": "restrict_public_buckets = true",
        "aws_s3_bucket_server_side_encryption_configuration": 'sse_algorithm = "AES256"',
        "aws_s3_bucket_ownership_controls": 'object_ownership = "BucketOwnerEnforced"',
        "aws_s3_bucket_policy": '"aws:SecureTransport" = "false"',
    }
    for rtype, fragment in needed.items():
        if fragment not in re.sub(r"\s+", " ", _body(texts, rtype, "state")).replace("  ", " "):
            problems.append(f"state bucket lacks {rtype} ({fragment})")
    if "prevent_destroy = true" not in _body(texts, "aws_s3_bucket", "state") or "import {" not in text:
        problems.append("state bucket lacks prevent_destroy or its import block")
    return problems


def root_files_problems() -> list[str]:
    problems = []
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "terraform/organization/terraform.organization.tfvars"], cwd=_REPO_ROOT
    )
    if ignored.returncode != 0:
        problems.append("terraform.organization.tfvars is not gitignored")
    if "/terraform/organization/" not in (_REPO_ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8"):
        problems.append("CODEOWNERS does not name terraform/organization")
    lock = (_ROOT / ".terraform.lock.hcl").read_text(encoding="utf-8")
    blocks = re.findall(r'^provider "([^"]+)"', lock, re.M)
    if blocks != ["registry.terraform.io/hashicorp/aws"]:
        problems.append(f"lock holds {blocks}, not exactly the hashicorp/aws provider")
    if lock != (_REPO_ROOT / "terraform/bootstrap/.terraform.lock.hcl").read_text(encoding="utf-8"):
        problems.append("lock differs from terraform/bootstrap's")
    return problems


def test_baseline_scps_deny_leaving_the_organization() -> None:
    texts = _texts()
    assert baseline_problems(texts) == []
    assert "organizations:LeaveOrganization" in _statement(texts, "deny_leave_organization")


def test_organization_and_ous() -> None:
    assert organization_problems(_texts()) == []


def test_state_bucket() -> None:
    assert state_bucket_problems(_texts()) == []


def test_name_family_and_no_literal_ids() -> None:
    assert hygiene_problems(_texts()) == []


def test_real_contract_maps_both_admin_roots_as_live_roots() -> None:
    from scripts.checks.verification.validate_admin_root_premerge_gate import _derive_admin_only_roots

    roots, error = _derive_admin_only_roots(_REPO_ROOT / "docs/contracts/environment-taxonomy.yaml")
    assert error is None
    contract = yaml.safe_load((_REPO_ROOT / "docs/contracts/protected-actions.yaml").read_text(encoding="utf-8"))
    live_roots = contract["gate"]["live_roots"]
    assert {"terraform/bootstrap", "terraform/organization"} <= set(roots)
    assert {"terraform/bootstrap", "terraform/organization"} <= set(live_roots)


def test_root_files() -> None:
    assert root_files_problems() == []


def _drop(fragment: str, file: str) -> Callable[[dict[str, str]], dict[str, str]]:
    return lambda t: {**t, file: t[file].replace(fragment, "", 1)}


_REDS: dict[str, tuple[Callable[[dict[str, str]], list[str]], Callable[[dict[str, str]], dict[str, str]]]] = {
    "leave statement removed": (
        baseline_problems,
        lambda t: {**t, "scps.tf": t["scps.tf"].replace("deny_leave_organization", "renamed_statement", 1)},
    ),
    "root condition dropped": (baseline_problems, _drop('Null    = { "aws:AssumedRoot" = "true" }', "scps.tf")),
    "cloudtrail exemption": (
        baseline_problems,
        lambda t: {
            **t,
            "scps.tf": t["scps.tf"].replace(
                'Sid    = "ProtectCloudTrail"', 'Sid    = "ProtectCloudTrail"\n      Condition = {}', 1
            ),
        },
    ),
    "size precondition dropped": (baseline_problems, _drop("<= 5120", "scps.tf")),
    "attachment dropped": (
        baseline_problems,
        lambda t: {**t, "scps.tf": t["scps.tf"].replace("organizational_unit.sit.id", "organizational_unit.prod.id")},
    ),
    "feature set changed": (
        organization_problems,
        lambda t: {**t, "organization.tf": t["organization.tf"].replace('"ALL"', '"CONSOLIDATED_BILLING"')},
    ),
    "root features dependency dropped": (organization_problems, _drop("depends_on", "organization.tf")),
    "ou prevent_destroy dropped": (organization_problems, _drop("prevent_destroy = true", "organization.tf")),
    "generated name": (
        hygiene_problems,
        lambda t: {**t, "trail.tf": t["trail.tf"].replace('name = "agent-platform-org-security-alerts"', 'name = "alerts"')},
    ),
    "account id literal": (hygiene_problems, lambda t: {**t, "main.tf": t["main.tf"] + "# " + "1" * 12 + "\n"}),
    "backend region": (
        hygiene_problems,
        lambda t: {**t, "main.tf": t["main.tf"].replace("encrypt      = true", 'encrypt = true\n    region = "x"')},
    ),
    "state bucket tls policy dropped": (state_bucket_problems, _drop('"aws:SecureTransport" = "false"', "state_bucket.tf")),
}


@pytest.mark.parametrize("label", sorted(_REDS))
def test_red_case(label: str) -> None:
    predicate, mutate = _REDS[label]
    base = _texts()
    mutated = mutate(base)
    assert mutated != base, f"red case {label!r} did not mutate anything"
    assert predicate(mutated) != [], f"red case {label!r} was not detected"
