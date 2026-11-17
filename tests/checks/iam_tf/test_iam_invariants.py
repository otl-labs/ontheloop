"""Static, credential-free layer over config/iam-invariants.json (PLAN-bootstrap-iam-analyzer-checks, Decision 235).

The never-list is pinned field for field, drift-checked against the classifier's protected names and the session guard, and
checked offline against the session user's own Allow statements. Each predicate returns a problem list; the real-tree nodes
assert it is empty and the red nodes drive one mutation per class.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import terraform_bootstrap_apply_classify as classify

ROOT = Path(__file__).resolve().parents[3]
INVARIANTS = ROOT / "config" / "iam-invariants.json"
SESSION_HCL = ROOT / "terraform" / "bootstrap" / "agent_session_user.tf"
EXPECTED: dict[str, Any] = json.loads(
    """
{
    "schema_version": 1,
    "validate_policy_block": {
        "session": [
            "ERROR",
            "SECURITY_WARNING"
        ],
        "ci_writable": [
            "ERROR",
            "SECURITY_WARNING"
        ],
        "other": [
            "ERROR"
        ],
        "boundary": [
            "ERROR"
        ]
    },
    "ceilings": [
        "agent-platform-github-ci-apply-boundary"
    ],
    "classes": [
        {
            "id": "session",
            "role_names": [],
            "policy_names": [
                "agent-platform-session-*"
            ],
            "user_names": [
                "agent-platform-session"
            ],
            "never": [
                {
                    "id": "iam_write",
                    "actions": [
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
                        "iam:PassRole"
                    ]
                },
                {
                    "id": "credential_minting",
                    "actions": [
                        "sts:AssumeRole*",
                        "sts:AssumeRoot",
                        "sts:GetFederationToken"
                    ]
                },
                {
                    "id": "secret_values_secretsmanager",
                    "actions": [
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:BatchGetSecretValue",
                        "secretsmanager:PutSecretValue",
                        "secretsmanager:UpdateSecret"
                    ]
                },
                {
                    "id": "secret_values_ssm",
                    "actions": [
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                        "ssm:GetParametersByPath",
                        "ssm:GetParameterHistory",
                        "ssm:PutParameter"
                    ],
                    "resources": [
                        "arn:aws:ssm:{region}:{account}:parameter/secret/*"
                    ]
                },
                {
                    "id": "s3_objects",
                    "actions": [
                        "s3:GetObject*",
                        "s3:PutObject*",
                        "s3:DeleteObject*",
                        "s3:ListBucket"
                    ]
                },
                {
                    "id": "lambda_direct",
                    "actions": [
                        "lambda:InvokeFunction",
                        "lambda:InvokeFunctionUrl",
                        "lambda:GetFunction",
                        "lambda:GetFunctionConfiguration"
                    ]
                },
                {
                    "id": "kms_decrypt",
                    "actions": [
                        "kms:Decrypt"
                    ]
                }
            ]
        },
        {
            "id": "ci_writable",
            "role_names": [
                "agent-platform-*"
            ],
            "policy_names": [
                "agent-platform-*"
            ],
            "user_names": [],
            "never": [
                {
                    "id": "protected_role_write",
                    "actions": [
                        "iam:PutRolePolicy",
                        "iam:AttachRolePolicy",
                        "iam:DeleteRolePolicy",
                        "iam:DetachRolePolicy",
                        "iam:UpdateAssumeRolePolicy",
                        "iam:DeleteRole",
                        "iam:PutRolePermissionsBoundary",
                        "iam:DeleteRolePermissionsBoundary",
                        "iam:PassRole"
                    ],
                    "resources": [
                        "arn:aws:iam::{account}:role/PlatformAdmin",
                        "arn:aws:iam::{account}:role/PlatformDev",
                        "arn:aws:iam::{account}:role/bootstrap-*",
                        "arn:aws:iam::{account}:role/github-settings-*",
                        "arn:aws:iam::{account}:role/platform-security-*",
                        "arn:aws:iam::{account}:role/ducklake-api-*",
                        "arn:aws:iam::{account}:role/telemetry-content-*"
                    ]
                },
                {
                    "id": "protected_policy_write",
                    "actions": [
                        "iam:CreatePolicyVersion",
                        "iam:DeletePolicyVersion",
                        "iam:SetDefaultPolicyVersion",
                        "iam:DeletePolicy"
                    ],
                    "resources": [
                        "arn:aws:iam::{account}:policy/agent-platform-github-ci-apply-boundary",
                        "arn:aws:iam::{account}:policy/agent-platform-session-*",
                        "arn:aws:iam::{account}:policy/bootstrap-*",
                        "arn:aws:iam::{account}:policy/platform-security-*"
                    ]
                },
                {
                    "id": "user_write",
                    "actions": [
                        "iam:CreateUser",
                        "iam:CreateAccessKey",
                        "iam:PutUserPolicy",
                        "iam:AttachUserPolicy",
                        "iam:PutUserPermissionsBoundary",
                        "iam:DeleteUserPermissionsBoundary",
                        "iam:AddUserToGroup",
                        "iam:CreateLoginProfile"
                    ]
                },
                {
                    "id": "credential_minting",
                    "actions": [
                        "sts:AssumeRoot",
                        "sts:GetFederationToken"
                    ]
                },
                {
                    "id": "github_settings_key",
                    "actions": [
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:PutSecretValue"
                    ],
                    "resources": [
                        "arn:aws:secretsmanager:{region}:{account}:secret:agent-platform-terraform-github-apply-app*"
                    ]
                },
                {
                    "id": "bootstrap_state_write",
                    "actions": [
                        "s3:PutObject",
                        "s3:DeleteObject"
                    ],
                    "resources": [
                        "arn:aws:s3:::agent-platform-bootstrap-tfstate/*"
                    ]
                }
            ]
        }
    ]
}
"""
)
PLACEHOLDERS = {"account", "region"}


def pin_problems(parsed: dict[str, Any]) -> list[str]:
    problems = [] if parsed == EXPECTED else ["the invariants file differs from the pinned mapping"]
    if parsed.get("ceilings") != [classify.BOUNDARY_NAME]:
        problems.append("ceilings must be exactly the classifier's boundary name")
    return problems


def _classes(parsed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in parsed["classes"]}


def drift_problems(parsed: dict[str, Any]) -> list[str]:
    resources = {r for row in _classes(parsed)["ci_writable"]["never"] for r in row.get("resources", [])}
    out = []
    for pattern in sorted(classify.PROTECTED_NAME_PATTERNS):
        if f"arn:aws:iam::{{account}}:{pattern}" not in resources:
            out.append(f"{pattern} is a protected name the ci_writable never rows do not name")
    return out


def _spans(text: str) -> list[str]:
    stack: list[int] = []
    spans = []
    for i, ch in enumerate(text):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            spans.append(text[stack.pop() : i + 1])
    return spans


def statements(text: str) -> list[dict[str, Any]]:
    """Every innermost {Effect, Action, Resource} object of an HCL file, with ${..} read as a wildcard."""
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    found = [s for s in _spans(text) if re.search(r'Effect\s*=\s*"(Allow|Deny)"', s) and re.search(r"\bAction\s*=", s)]
    out = []
    for span in found:
        if any(other != span and other in span for other in found):
            continue
        effect = re.search(r'Effect\s*=\s*"(\w+)"', span).group(1)  # type: ignore[union-attr]
        parts: dict[str, list[str]] = {}
        for key in ("Action", "Resource"):
            m = re.search(rf"\b{key}\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", span, re.S)
            parts[key] = re.findall(r'"([^"]*)"', m.group(1)) if m else []
        out.append(
            {
                "effect": effect,
                "actions": parts["Action"],
                "resources": [re.sub(r"\$\{[^}]*\}", "*", r) for r in parts["Resource"]],
            }
        )
    return out


def _hit(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a)


def session_grant_problems(parsed: dict[str, Any], hcl: str) -> list[str]:
    out = []
    for stmt in (s for s in statements(hcl) if s["effect"] == "Allow"):
        for row in _classes(parsed)["session"]["never"]:
            rows = [re.sub(r"\{\w+\}", "*", r) for r in row.get("resources", [])] or ["*"]
            for action in stmt["actions"]:
                if any(_hit(action, w) for w in row["actions"]) and any(_hit(r, w) for r in stmt["resources"] for w in rows):
                    out.append(f"an Allow grants {action} on a {row['id']} resource")
    return out


def hygiene_problems(parsed: dict[str, Any]) -> list[str]:
    text = json.dumps(parsed)
    out = [f"placeholder {{{p}}}" for p in set(re.findall(r"\{(\w+)\}", text)) - PLACEHOLDERS]
    if re.search(r"[0-9]{12}", text):
        out.append("a twelve-digit run")
    for c in parsed["classes"]:
        out += [
            f"{row['id']}: {a} has no service prefix"
            for row in c["never"]
            for a in row["actions"]
            if not re.match(r"^[a-z0-9-]+:\S+$", a)
        ]
    return out


def escalation_problems(parsed: dict[str, Any], hcl: str) -> list[str]:
    guard = next((s for s in statements(hcl) if s["effect"] == "Deny" and "iam:PassRole" in s["actions"]), None)
    if guard is None:
        return ["the session guard's escalation statement was not found"]
    session = _classes(parsed)["session"]["never"]
    covered = {a for row in session if row["id"] in ("iam_write", "credential_minting") for a in row["actions"]}
    return [f"{a} is denied by the guard and absent from the session rows" for a in guard["actions"] if a not in covered]


def _load() -> dict[str, Any]:
    return json.loads(INVARIANTS.read_text(encoding="utf-8"))


def _mutated(mutate: Any) -> dict[str, Any]:
    parsed = copy.deepcopy(_load())
    mutate(parsed)
    return parsed


def _drop_secret(p: dict[str, Any]) -> None:
    row = next(r for r in _classes(p)["session"]["never"] if r["id"] == "secret_values_secretsmanager")
    row["actions"].remove("secretsmanager:GetSecretValue")


MUTATIONS = {
    "narrowed_session_glob": lambda p: _classes(p)["session"].update(user_names=["agent-platform-session-x"]),
    "dropped_secret_action": _drop_secret,
    "added_ceiling": lambda p: p["ceilings"].append("agent-platform-session-guard"),
    "emptied_ci_policy_names": lambda p: _classes(p)["ci_writable"].update(policy_names=[]),
}


def test_the_invariants_file_is_pinned_field_for_field() -> None:
    assert pin_problems(_load()) == []


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_the_pin_is_red_on_each_mutation(name: str) -> None:
    assert pin_problems(_mutated(MUTATIONS[name]))


def test_two_classes_and_thirteen_never_rows_are_named() -> None:
    parsed = _load()
    assert sorted(_classes(parsed)) == ["ci_writable", "session"]
    assert sum(len(c["never"]) for c in parsed["classes"]) == 13  # count-coupling-ok: the plan's pinned row count


def test_every_protected_name_is_a_ci_writable_never_resource() -> None:
    assert drift_problems(_load()) == []
    assert drift_problems(_mutated(lambda p: _classes(p)["ci_writable"]["never"][0]["resources"].pop(0)))


def test_no_session_allow_grants_a_never_action_on_a_never_resource() -> None:
    hcl = SESSION_HCL.read_text(encoding="utf-8")
    assert len(statements(hcl)) >= 5 and session_grant_problems(_load(), hcl) == []


@pytest.mark.parametrize(
    "extra",
    [
        ('"ssm:GetParameter"', '"arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/x"'),
        ('"s3:GetObject"', '"*"'),
        ('["iam:CreateUser"]', '"*"'),
    ],
)
def test_an_added_session_allow_on_a_never_row_is_red(extra: tuple[str, str]) -> None:
    hcl = (
        SESSION_HCL.read_text(encoding="utf-8")
        + f'\nlocals {{\n  x = {{\n    Effect = "Allow"\n    Action = {extra[0]}\n    Resource = {extra[1]}\n  }}\n}}\n'
    )
    assert session_grant_problems(_load(), hcl)


def test_the_endpoint_discovery_read_passes_because_its_resource_is_disjoint() -> None:
    hcl = SESSION_HCL.read_text(encoding="utf-8")
    assert any(s["resources"] == ["arn:aws:ssm:*:*:parameter/agent-platform/ducklake/*"] for s in statements(hcl))


def test_hygiene_holds_and_a_stray_placeholder_or_digit_run_is_red() -> None:
    assert hygiene_problems(_load()) == []
    assert hygiene_problems(_mutated(lambda p: _classes(p)["session"]["never"][3]["resources"].append("arn:{other}")))
    assert hygiene_problems(_mutated(lambda p: _classes(p)["session"]["never"][3]["resources"].append("a" + "1" * 12)))
    assert hygiene_problems(_mutated(lambda p: _classes(p)["session"]["never"][0]["actions"].append("Create*")))


def test_every_session_guard_action_is_in_a_session_row() -> None:
    hcl = SESSION_HCL.read_text(encoding="utf-8")
    assert escalation_problems(_load(), hcl) == []
    widened = hcl.replace('"iam:Reset*",', '"iam:Reset*",\n          "iam:Simulate*",', 1)
    assert escalation_problems(_load(), widened) == ["iam:Simulate* is denied by the guard and absent from the session rows"]
    assert escalation_problems(_load(), "") == ["the session guard's escalation statement was not found"]
