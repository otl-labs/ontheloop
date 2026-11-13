"""Standing guard for PLAN-bootstrap-ci-gated-apply (first PR): the bootstrap CI roles, their four policies, the boundary's
protected-role Deny, the provider lock and the tf-bootstrap-apply Environment.

Credential-free text tests in the iam_tf predicate convention: each predicate returns a problem list, the real-tree nodes
assert it is empty, and the red node drives the same predicate on one mutation per class.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.checks.iam_tf._ci_platform_role_iam import _jsonencode_statement_blocks, _parse_jsonencode_statement

_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = _ROOT / "terraform" / "bootstrap"
_FILE = "bootstrap_ci_apply.tf"
_ACCT = "123456789012"
_BOUNDARY = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
_REF = "otl-labs/ontheloop/.github/workflows/terraform-bootstrap-apply.yml@refs/heads/main"
_ROLES = {"bootstrap_apply": "tf-bootstrap-apply", "bootstrap_preview": "tf-trusted-plan"}
_READ_ACTIONS = {
    "iam:Get*", "iam:List*", "cloudtrail:DescribeTrails", "cloudtrail:GetTrail", "cloudtrail:GetTrailStatus",
    "cloudtrail:GetEventSelectors", "cloudtrail:GetInsightSelectors", "cloudtrail:ListTags", "logs:Describe*",
    "logs:ListTagsLogGroup", "logs:ListTagsForResource", "cloudwatch:DescribeAlarms", "cloudwatch:ListTagsForResource",
    "scheduler:GetSchedule", "scheduler:GetScheduleGroup", "scheduler:ListTagsForResource", "states:Describe*",
    "states:List*", "states:ValidateStateMachineDefinition", "secretsmanager:DescribeSecret",
    "secretsmanager:GetResourcePolicy", "secretsmanager:ListSecrets", "s3:GetBucket*", "s3:GetAccelerateConfiguration",
    "s3:GetLifecycleConfiguration", "s3:GetReplicationConfiguration", "s3:GetEncryptionConfiguration", "s3:ListBucket",
    "s3:GetObject", "ssm:GetParameter", "ssm:GetParameters", "ssm:ListTagsForResource", "ssm:DescribeParameters",
    "apigateway:GET", "lambda:GetPolicy", "servicequotas:Get*", "servicequotas:List*",
}  # fmt: skip
_WRITE_ACTIONS = {
    "s3:PutObject", "s3:GetObject", "s3:DeleteObject", "iam:CreateRole", "iam:PutRolePolicy",
    "iam:PutRolePermissionsBoundary", "iam:AttachRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:UpdateRole",
    "iam:UpdateRoleDescription", "iam:UpdateAssumeRolePolicy", "iam:CreatePolicy", "iam:CreatePolicyVersion",
    "iam:DeletePolicyVersion", "iam:TagPolicy", "iam:UntagPolicy",
}  # fmt: skip
_PROTECTED_DENIES = {
    "DenyProtectedRoleWrite", "DenyUserAndGroupWrite", "DenyProtectedPolicyWrite", "DenyBoundaryRemoval",
    "DenyProviderAndAccountWrite", "DenyDetectorWrite", "DenyGithubSettingsKey", "DenySecretValues",
    "DenyObjectIOOutsideState", "DenyCredentialMinting", "DenyAccountIAMDump",
}  # fmt: skip
_BOUNDARY_DENY_RESOURCES = {
    "arn:aws:iam::*:role/Platform*", "arn:aws:iam::*:role/bootstrap-*", "arn:aws:iam::*:role/github-settings-*",
    "arn:aws:iam::*:role/platform-security-*", "arn:aws:iam::*:role/ducklake-api-*",
}  # fmt: skip
_DETECTOR_PATTERNS = {
    "aws_iam_role": ("role/platform-security-*",),
    "aws_iam_policy": ("policy/platform-security-*",),
    "aws_cloudwatch_log_group": ("log-group:platform-security-*", "log-group:platform-security-*:*"),
    "aws_cloudwatch_metric_alarm": ("alarm:platform-security-*",),
    "aws_cloudtrail": ("trail/platform-security-*",),
    "aws_s3_bucket": ("arn:aws:s3:::platform-security-trail-*", "arn:aws:s3:::platform-security-trail-*/*"),
    "aws_sfn_state_machine": ("stateMachine:platform-security-*",),
    "aws_scheduler_schedule_group": ("schedule-group/platform-security-*",),
    "aws_scheduler_schedule": ("schedule/platform-security-*/*",),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bootstrap_texts() -> dict[str, str]:
    return {f.name: _read(f) for f in sorted(_BOOTSTRAP.glob("*.tf"))}


def _balanced(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_idx : i + 1]
    return text[open_idx:]


def _nocomment(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _resource(text: str, rtype: str, name: str) -> str:
    m = re.search(rf'resource\s+"{rtype}"\s+"{name}"\s*\{{', _nocomment(text))
    return _balanced(_nocomment(text), m.end() - 1) if m else ""


def _local(text: str, name: str) -> str:
    t = _nocomment(text)
    m = re.search(rf"^\s*{name}\s*=\s*", t, re.M)
    if not m:
        return ""
    start = m.end()
    brace = t.find("{", start)
    bracket = t.find("[", start)
    open_idx = min(i for i in (brace, bracket) if i != -1)
    return _balanced(t, open_idx) if t[open_idx] == "{" else t[open_idx : t.find("]", open_idx) + 1]


def _statements(text: str, local: str) -> dict[str, tuple[str, object]]:
    out = {}
    for block in _jsonencode_statement_blocks(_local(text, local)):
        stmt = _parse_jsonencode_statement(block)
        out[stmt.sid or ""] = (block, stmt)
    return out


def _list(raw: str) -> list[str]:
    return re.findall(r'"([^"]+)"', raw)


def _local_list(text: str, name: str) -> list[str]:
    return _list(_local(text, name))


def trust_problems(text: str) -> list[str]:
    problems: list[str] = []
    for role, env in _ROLES.items():
        block = _resource(text, "aws_iam_role", role)
        if not block:
            problems.append(f"{role}: role not declared")
            continue
        if block.count("Principal") != 1 or block.count("Effect") != 1:
            problems.append(f"{role}: trust must be exactly one statement")
        if "StringEquals" not in block or re.search(r"StringLike|ForAnyValue|ForAllValues|:ref\b|refs/", block):
            problems.append(f"{role}: trust must be StringEquals only with no ref subject")
        if "token.actions.githubusercontent.com:aud" not in block or "local.github_repos" not in block:
            problems.append(f"{role}: trust must carry aud and the github_repos subject")
        want_env = "bootstrap_ci_apply_env" if role == "bootstrap_apply" else "bootstrap_ci_preview_env"
        if f"environment:${{local.{want_env}}}" not in block:
            problems.append(f"{role}: subject must name the {env} Environment local")
        if "job_workflow_ref" not in block or "local.bootstrap_ci_workflow_refs" not in block:
            problems.append(f"{role}: job_workflow_ref must be local.bootstrap_ci_workflow_refs")
    for name, env in (("bootstrap_ci_apply_env", "tf-bootstrap-apply"), ("bootstrap_ci_preview_env", "tf-trusted-plan")):
        if re.search(rf'^\s*{name}\s*=\s*"{env}"', _nocomment(text), re.M) is None:
            problems.append(f"{name} must be {env}")
    if _local_list(text, "bootstrap_ci_workflow_refs") != [_REF]:
        problems.append("workflow refs must be exactly the bootstrap-apply workflow at refs/heads/main")
    return problems


def boundary_free_problems(text: str) -> list[str]:
    problems = []
    for role, _ in _ROLES.items():
        block = _resource(text, "aws_iam_role", role)
        if re.search(r"^\s*permissions_boundary\s*=", block, re.M):
            problems.append(f"{role}: carries a permissions_boundary")
        name = re.search(r'^\s*name\s*=\s*"([^"]+)"', block, re.M)
        if not name or name.group(1).startswith("agent-platform-"):
            problems.append(f"{role}: name must exist and sit outside agent-platform-*")
        if "prevent_destroy = true" not in block:
            problems.append(f"{role}: prevent_destroy missing")
    return problems


def _allows(text: str, local: str) -> list[object]:
    return [s for _, s in _statements(text, local).values() if s.effect == "Allow"]  # type: ignore[attr-defined]


def grant_problems(text: str) -> list[str]:
    problems = []
    reads = {a for s in _allows(text, "bootstrap_plan_reads_json") for a in s.actions}  # type: ignore[attr-defined]
    write = {a for s in _allows(text, "bootstrap_apply_write_json") for a in s.actions}  # type: ignore[attr-defined]
    if reads != _READ_ACTIONS:
        problems.append(f"reads policy actions differ: {sorted(reads ^ _READ_ACTIONS)}")
    if write != _WRITE_ACTIONS:
        problems.append(f"write policy actions differ: {sorted(write ^ _WRITE_ACTIONS)}")
    for s in _allows(text, "bootstrap_apply_write_json"):
        if any(a.startswith("iam:") for a in s.actions):  # type: ignore[attr-defined]
            for r in s.resources:  # type: ignore[attr-defined]
                if not re.search(r":(role|policy)/agent-platform-", r):
                    problems.append(f"{s.sid}: IAM write on non-prefixed resource {r}")  # type: ignore[attr-defined]
    return problems


def condition_problems(text: str) -> list[str]:
    problems = []
    stmts = _statements(text, "bootstrap_apply_write_json")
    for sid in ("IAMRoleCreateBounded", "IAMRoleWriteBounded", "IAMRoleAttachBounded"):
        block = stmts.get(sid, ("", None))[0]
        if f'"iam:PermissionsBoundary" = "{_BOUNDARY}"' not in block:
            problems.append(f"{sid}: missing the boundary condition")
    if '"iam:PolicyARN" = "arn:aws:iam::*:policy/agent-platform-*"' not in stmts.get("IAMRoleAttachBounded", ("", None))[0]:
        problems.append("IAMRoleAttachBounded: missing the agent-platform-* policy ARN condition")
    return problems


def trust_write_problems(texts: dict[str, str]) -> list[str]:
    stmt = _statements(texts[_FILE], "bootstrap_apply_write_json").get("IAMRoleTrustWrite", ("", None))[1]
    if stmt is None:
        return ["IAMRoleTrustWrite missing"]
    declared = {
        m.group(1)
        for t in texts.values()
        for b in re.findall(r'resource\s+"aws_iam_role"\s+"\w+"\s*\{[^}]*?name\s*=\s*"([^"]+)"', _nocomment(t), re.S)
        for m in [re.match(r"(.+)", b)]
    }
    problems = []
    for r in stmt.resources:  # type: ignore[attr-defined]
        name = r.rsplit("/", 1)[-1]
        if "*" in r or name not in declared:
            problems.append(f"IAMRoleTrustWrite resource {r} must be a declared, non-wildcard role")
    if not stmt.resources:  # type: ignore[attr-defined]
        problems.append("IAMRoleTrustWrite has no resources")
    return problems


def preview_problems(text: str) -> list[str]:
    problems = []
    reads = {a for s in _allows(text, "bootstrap_plan_reads_json") for a in s.actions}  # type: ignore[attr-defined]
    if not all(re.match(r"^(\w+:(Get|List|Describe)|apigateway:GET|states:Validate)", a) for a in reads):
        problems.append("a plan-read action is not a read family")
    guard = _local(text, "bootstrap_preview_guard_json")
    if 'concat(local.bootstrap_plan_read_actions, ["sts:GetCallerIdentity"])' not in guard:
        problems.append("DenyAllButReads NotAction must be the reads list plus sts:GetCallerIdentity")
    if re.search(r'BoolIfExists\s*=\s*\{\s*"aws:ViaAWSService"\s*=\s*"false"\s*\}', guard) is None:
        problems.append("DenyAllButReads must carry BoolIfExists aws:ViaAWSService false")
    if set(_local_list(text, "bootstrap_plan_read_actions")) != reads:
        problems.append("bootstrap_plan_read_actions differs from the reads policy's actions")
    if _allows(text, "bootstrap_preview_guard_json"):
        problems.append("the preview guard must hold Denies only")
    return problems


def guard_problems(texts: dict[str, str]) -> list[str]:
    text = texts[_FILE]
    stmts = _statements(text, "bootstrap_apply_guard_json")
    problems = [f"missing Deny {sid}" for sid in sorted(_PROTECTED_DENIES - set(stmts))]
    if any(s.effect != "Deny" for _, s in stmts.values()):  # type: ignore[attr-defined]
        problems.append("the guard must hold Denies only")
    patterns = [r for _, s in stmts.values() for r in s.resources]  # type: ignore[attr-defined]
    concrete = {
        k: [p.replace("${var.account_id}", _ACCT) for p in patterns if f":{k}/" in p] for k in ("role", "policy", "user")
    }
    parsed = 0
    for t in texts.values():
        t = _nocomment(t)
        for kind, rtype, prefix in (
            ("role", "aws_iam_role", "role"),
            ("policy", "aws_iam_policy", "policy"),
            ("user", "aws_iam_user", "user"),
        ):
            for body in re.findall(rf'resource\s+"{rtype}"\s+"\w+"\s*\{{(.*?)\n\}}', t, re.S):
                m = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
                if not m or ("${" in m.group(1)):
                    continue
                n = m.group(1)
                protected = not n.startswith("agent-platform-") or n.endswith("github-ci-apply-boundary") or "session-" in n
                if not protected:
                    continue
                parsed += 1
                arn = f"arn:aws:iam::{_ACCT}:{prefix}/{n}"
                if not any(fnmatch.fnmatch(arn, c) for c in concrete[prefix]):
                    problems.append(f"protected {kind} {n} matched by no Deny resource")
    if parsed < 5:
        problems.append(
            f"parsed only {parsed} protected IAM names (expected the platform roles, bootstrap-apply and the boundary)"
        )
    return problems


def detector_problems(texts: dict[str, str]) -> list[str]:
    block = _statements(texts[_FILE], "bootstrap_apply_guard_json").get("DenyDetectorWrite", ("", None))[0]
    problems = []
    for name, t in texts.items():
        if not name.startswith("platform_security_"):
            continue
        for rtype in sorted(set(re.findall(r'resource\s+"(aws_\w+)"', _nocomment(t)))):
            key = "aws_s3_bucket" if rtype.startswith("aws_s3_bucket") else rtype
            key = {"aws_iam_role_policy": "aws_iam_role", "aws_iam_role_policy_attachment": "aws_iam_role"}.get(key, key)
            key = "aws_cloudwatch_log_group" if rtype == "aws_cloudwatch_log_metric_filter" else key
            patterns = _DETECTOR_PATTERNS.get(key)
            if patterns is None:
                problems.append(f"{rtype}: no detector pattern is mapped, so DenyDetectorWrite cannot be checked for it")
            problems += [f"{rtype}: DenyDetectorWrite lacks {p}" for p in patterns or () if f'{p}"' not in block]
    return problems


def boundary_deny_problems(texts: dict[str, str]) -> list[str]:
    text = texts["github_ci_apply_boundary.tf"]
    i = text.find('"DenyBoundedWriteOnProtectedRoles"')
    if i < 0:
        return ["DenyBoundedWriteOnProtectedRoles missing"]
    start = text.rfind("{", 0, i)
    stmt = _parse_jsonencode_statement(_balanced(text, start))
    problems = []
    if stmt.effect != "Deny" or set(stmt.actions) != {f"iam:{c}*" for c in "ACDPTU"}:
        problems.append("DenyBoundedWriteOnProtectedRoles must Deny iam:A*, C*, D*, P*, T*, U*")
    if set(stmt.resources) != _BOUNDARY_DENY_RESOURCES:
        problems.append("DenyBoundedWriteOnProtectedRoles resources differ from the five protected role patterns")
    for t in texts.values():
        for body in re.findall(r'resource\s+"aws_iam_role"\s+"\w+"\s*\{(.*?)\n\}', _nocomment(t), re.S):
            m = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
            if m and not m.group(1).startswith("agent-platform-"):
                arn = f"arn:aws:iam::{_ACCT}:role/{m.group(1)}"
                if not any(fnmatch.fnmatch(arn, r.replace("*:", "*:")) for r in stmt.resources):
                    problems.append(f"role {m.group(1)} matched by no boundary Deny resource")
    return problems


def lock_problems(text: str, tracked: list[str]) -> list[str]:
    problems = []
    if "terraform/bootstrap/.terraform.lock.hcl" not in tracked:
        problems.append("the lock is not tracked by git")
    providers = re.findall(r'^provider\s+"([^"]+)"', text, re.M)
    if providers != ["registry.terraform.io/hashicorp/aws"]:
        problems.append(f"the lock must hold exactly hashicorp/aws, found {providers}")
    version = re.search(r'version\s*=\s*"(\d+)\.', text)
    if 'constraints = "~> 5.0"' not in text or not version or version.group(1) != "5":
        problems.append("the lock constraint must be ~> 5.0 with a 5.x version")
    if '"h1:' not in text or '"zh:' not in text:
        problems.append("the lock must carry h1: and zh: hashes")
    return problems


def environment_problems(text: str) -> list[str]:
    block = _resource(text, "github_repository_environment", "tf_bootstrap_apply")
    problems = []
    if not block or '"tf-bootstrap-apply"' not in block:
        return ["tf-bootstrap-apply Environment not declared"]
    for want in ("can_admins_bypass   = false", "custom_branch_policies = true", "prevent_destroy = true", "reviewers {"):
        if want not in block:
            problems.append(f"Environment lacks {want}")
    policy = _resource(text, "github_repository_environment_deployment_policy", "tf_bootstrap_apply_main")
    if 'branch_pattern = "main"' not in policy:
        problems.append("Environment lacks a main deployment policy")
    return problems


def _tracked_locks() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "terraform"], cwd=_ROOT, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout.split()


def test_bootstrap_roles_trust_one_gated_subject() -> None:
    assert trust_problems(_read(_BOOTSTRAP / _FILE)) == []


def test_bootstrap_roles_are_boundary_free_by_name() -> None:
    assert boundary_free_problems(_read(_BOOTSTRAP / _FILE)) == []


def test_bootstrap_apply_grants_are_closed() -> None:
    assert grant_problems(_read(_BOOTSTRAP / _FILE)) == []


def test_bootstrap_apply_create_and_write_carry_boundary_condition() -> None:
    assert condition_problems(_read(_BOOTSTRAP / _FILE)) == []


def test_bootstrap_apply_trust_write_is_enumerated() -> None:
    texts = _bootstrap_texts()
    assert trust_write_problems(texts) == []
    stmt = _statements(texts[_FILE], "bootstrap_apply_write_json")["IAMRoleTrustWrite"][1]
    assert stmt.resources == ["arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-apply"]  # type: ignore[attr-defined]


def test_bootstrap_preview_has_no_write() -> None:
    assert preview_problems(_read(_BOOTSTRAP / _FILE)) == []


def test_bootstrap_apply_guard_covers_protected_set() -> None:
    assert guard_problems(_bootstrap_texts()) == []


def test_bootstrap_detector_resources_denied() -> None:
    assert detector_problems(_bootstrap_texts()) == []


def test_boundary_denies_protected_role_writes() -> None:
    assert boundary_deny_problems(_bootstrap_texts()) == []


def test_bootstrap_lock_pins_aws() -> None:
    assert lock_problems(_read(_BOOTSTRAP / ".terraform.lock.hcl"), _tracked_locks()) == []


def test_bootstrap_apply_environment_declared() -> None:
    assert environment_problems(_read(_ROOT / "terraform" / "github" / "environments.tf")) == []


def _sub(old: str, new: str) -> Callable[[str], str]:
    def mutate(text: str) -> str:
        assert old in text, old
        return text.replace(old, new, 1)

    return mutate


_SECOND_STATEMENT = (
    '    Statement = [\n      {\n        Effect = "Allow"\n        Principal = "*"\n      },\n'
    '      {\n        Effect = "Allow"\n'
)

_MUTATIONS: dict[str, tuple[str, Callable[[str], str]]] = {
    "extra_allow_verb": ("grant", _sub('"iam:UntagPolicy"]', '"iam:UntagPolicy", "iam:DeleteRole"]')),
    "iam_write_on_non_prefixed_resource": (
        "grant",
        _sub('Resource = ["arn:aws:iam::${var.account_id}:policy/agent-platform-*"]', 'Resource = ["*"]'),
    ),
    "missing_boundary_condition": (
        "condition",
        _sub(
            '"iam:PermissionsBoundary" = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"',
            '"iam:Other" = "x"',
        ),
    ),
    "ref_subject": ("trust", _sub("environment:${local.bootstrap_ci_apply_env}", "ref:refs/heads/main")),
    "string_like_trust": ("trust", _sub('StringEquals = {\n            "token', 'StringLike = {\n            "token')),
    "second_trust_statement": ("trust", _sub('    Statement = [\n      {\n        Effect = "Allow"\n', _SECOND_STATEMENT)),
    "boundary_added_to_role": (
        "boundary_free",
        _sub("  # No permissions_boundary: see the header. The explicit", '  permissions_boundary = "x"\n  # The explicit'),
    ),
    "write_allow_on_preview": (
        "preview",
        _sub(
            'Sid       = "DenyAllButReads"\n        Effect    = "Deny"',
            'Sid       = "DenyAllButReads"\n        Effect    = "Allow"',
        ),
    ),
    "dropped_protected_deny_resource": (
        "guard",
        _sub('"arn:aws:iam::${var.account_id}:role/PlatformAdmin",\n', ""),
    ),
    "missing_detector_pattern": ("detector", _sub('"arn:aws:s3:::platform-security-trail-*/*",\n', "")),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_bootstrap_ci_predicates_are_red_on_each_mutation(name: str) -> None:
    kind, mutate = _MUTATIONS[name]
    texts = _bootstrap_texts()
    text = mutate(texts[_FILE])
    mutated = {**texts, _FILE: text}
    problems = {
        "grant": lambda: grant_problems(text),
        "condition": lambda: condition_problems(text),
        "trust": lambda: trust_problems(text),
        "boundary_free": lambda: boundary_free_problems(text),
        "preview": lambda: preview_problems(text),
        "guard": lambda: guard_problems(mutated),
        "detector": lambda: detector_problems(mutated),
    }[kind]()
    assert problems, f"{name}: the {kind} predicate stayed green on its mutation"


def test_unprotected_new_role_without_deny_is_red() -> None:
    texts = _bootstrap_texts()
    texts["sneaky.tf"] = 'resource "aws_iam_role" "sneaky" {\n  name = "sneaky-role"\n}\n'
    assert guard_problems(texts) and boundary_deny_problems(texts)


def test_dropped_boundary_protected_role_resource_is_red() -> None:
    texts = _bootstrap_texts()
    texts["github_ci_apply_boundary.tf"] = texts["github_ci_apply_boundary.tf"].replace(
        '"arn:aws:iam::*:role/ducklake-api-*"', '"arn:aws:iam::*:role/other-*"'
    )
    assert boundary_deny_problems(texts)


def test_lock_with_second_provider_or_untracked_is_red() -> None:
    text = _read(_BOOTSTRAP / ".terraform.lock.hcl")
    assert lock_problems(text + '\nprovider "registry.terraform.io/x/y" {\n}\n', _tracked_locks())
    assert lock_problems(text, [])


def test_environment_with_admin_bypass_is_red() -> None:
    text = _read(_ROOT / "terraform" / "github" / "environments.tf").replace(
        'environment         = "tf-bootstrap-apply"\n  prevent_self_review = false\n  can_admins_bypass   = false',
        'environment         = "tf-bootstrap-apply"\n  prevent_self_review = false\n  can_admins_bypass   = true',
    )
    assert environment_problems(text)
