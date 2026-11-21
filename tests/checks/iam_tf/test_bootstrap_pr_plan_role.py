"""Standing guard for PLAN-bootstrap-trusted-pr-plan (PR 1): the bootstrap-pr-plan role is read-only, trusted only by
terraform-bootstrap-plan.yml at main, boundary-free by design, and shares exactly the plan reads policy.

Credential-free text tests in the iam_tf predicate convention: the predicate returns a problem list, the real-tree node
asserts it is empty, and a parametrized red node drives one mutation per class.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

_BOOTSTRAP = Path(__file__).resolve().parents[3] / "terraform" / "bootstrap"
_NEW = "bootstrap_pr_plan.tf"
_REF = '"otl-labs/ontheloop/.github/workflows/terraform-bootstrap-plan.yml@refs/heads/main"'
_NEW_POLICIES = ("bootstrap_pr_plan_reads", "bootstrap_pr_plan_guard")
_EXPECTED_ATTACHED = {
    "aws_iam_policy.bootstrap_plan_reads.arn",
    "aws_iam_policy.bootstrap_pr_plan_reads.arn",
    "aws_iam_policy.bootstrap_pr_plan_guard.arn",
}
_ANALYZER = {
    "access-analyzer:ValidatePolicy",
    "access-analyzer:CheckAccessNotGranted",
    "access-analyzer:CheckNoNewAccess",
    "access-analyzer:CheckNoPublicAccess",
}
_NOT_ACTION = 'concat(local.bootstrap_plan_read_actions, local.bootstrap_preview_analyzer_actions, ["sts:GetCallerIdentity"])'


def _texts() -> dict[str, str]:
    return {f.name: f.read_text(encoding="utf-8") for f in sorted(_BOOTSTRAP.glob("*.tf"))}


def _nocomment(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _balanced(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_idx : i + 1]
    return text[open_idx:]


def _local(text: str, name: str) -> str:
    t = _nocomment(text)
    m = re.search(rf"^\s*{name}\s*=\s*", t, re.M)
    if not m:
        return ""
    brace, bracket = t.find("{", m.end()), t.find("[", m.end())
    idx = min(i for i in (brace, bracket) if i != -1)
    return _balanced(t, idx) if t[idx] == "{" else t[idx : t.find("]", idx) + 1]


def _blocks(text: str, kind: str) -> list[tuple[str, str]]:
    t = _nocomment(text)
    return [(m.group(1), _balanced(t, m.end() - 1)) for m in re.finditer(rf'resource\s+"{kind}"\s+"(\w+)"\s*\{{', t)]


def _statements(doc: str) -> list[str]:
    stmts: list[str] = []
    for m in re.finditer(r"\n\s*\{\n", doc):
        stmts.append(_balanced(doc, m.end() - 2))
    return [s for s in stmts if "Effect" in s]


def _role_problems(new: str) -> list[str]:
    problems: list[str] = []
    refs = _local(new, "bootstrap_pr_plan_workflow_refs")
    if re.findall(r'"([^"]+)"', refs) != [_REF.strip('"')]:
        problems.append("bootstrap_pr_plan_workflow_refs must be exactly the terraform-bootstrap-plan.yml ref at main")
    roles = _blocks(new, "aws_iam_role")
    if [r for r, _ in roles] != ["bootstrap_pr_plan"]:
        return [*problems, "the file must declare exactly the role bootstrap_pr_plan"]
    body = roles[0][1]
    if "permissions_boundary" in body:
        problems.append("the role must carry no permissions_boundary (Decision 270 pattern)")
    if not re.search(r'name\s*=\s*"bootstrap-[a-z-]+"', body):
        problems.append("the role name must start bootstrap-")
    trust = re.search(r"assume_role_policy\s*=\s*jsonencode\(", body)
    trust_text = body[trust.end() :] if trust else ""
    if len(re.findall(r"Effect\s*=", trust_text)) != 1:
        problems.append("the role must hold exactly one trust statement")
    if "local.bootstrap_pr_plan_workflow_refs" not in trust_text or "local.bootstrap_ci_preview_env" not in trust_text:
        problems.append("the trust must name the pr_plan workflow refs and the tf-trusted-plan subjects")
    if "StringEquals" not in trust_text or "StringLike" in trust_text:
        problems.append("the trust must be StringEquals only")
    if "bootstrap_ci_workflow_refs" in trust_text or "terraform-bootstrap-apply" in trust_text:
        problems.append("the trust must not name the apply workflow")
    return problems


def _attachment_problems(texts: dict[str, str]) -> list[str]:
    problems: list[str] = []
    new = texts.get(_NEW, "")
    for name, text in texts.items():
        for att, body in _blocks(text, "aws_iam_role_policy_attachment"):
            if any(p in body for p in _NEW_POLICIES) and "aws_iam_role.bootstrap_pr_plan." not in body:
                problems.append(f"{name}: {att} attaches a pr_plan policy to another role")
            if name != _NEW and "aws_iam_role.bootstrap_pr_plan." in body:
                problems.append(f"{name}: {att} attaches to bootstrap_pr_plan outside {_NEW}")
    attached = {
        m.group(1)
        for _, body in _blocks(new, "aws_iam_role_policy_attachment")
        if "aws_iam_role.bootstrap_pr_plan." in body
        for m in re.finditer(r"policy_arn\s*=\s*(\S+)", body)
    }
    if attached != _EXPECTED_ATTACHED or len(_blocks(new, "aws_iam_role_policy_attachment")) != 3:
        problems.append("exactly three attachments: the shared plan reads, pr_plan reads and pr_plan guard")
    return problems


def _reads_problems(new: str) -> list[str]:
    problems: list[str] = []
    reads = _local(new, "bootstrap_pr_plan_reads_json")
    allowed_actions: set[str] = set()
    for stmt in _statements(reads):
        if not re.search(r'Effect\s*=\s*"Allow"', stmt):
            problems.append("the reads policy must hold only Allow statements")
        action = re.search(r"Action\s*=\s*(\[[^\]]*\]|\S+)", stmt)
        allowed_actions.update(re.findall(r'"([a-z0-9-]+:[A-Za-z*]+)"', action.group(1) if action else ""))
    if allowed_actions != {*_ANALYZER, "s3:GetObject", "s3:ListBucket"}:
        problems.append("the reads policy's actions must be exactly the analyzer actions, s3:GetObject and s3:ListBucket")
    for res in re.findall(r'"([^"]*\$\{local\.data_lake_bucket_arn\}[^"]*)"', reads):
        if "bootstrap_pr_plan_mirror_prefix" not in res:
            problems.append("an S3 resource in the reads policy is not under the mirror prefix")
    if re.search(r"Resource\s*=\s*\[local\.data_lake_bucket_arn\]", reads) is None:
        problems.append("ProviderMirrorList must be on the bucket ARN")
    return problems


def _guard_problems(new: str) -> list[str]:
    problems: list[str] = []
    guard = _local(new, "bootstrap_pr_plan_guard_json")
    if f"NotAction = {_NOT_ACTION}" not in guard:
        problems.append("DenyAllButReads NotAction must be the exact concat of reads, analyzer and GetCallerIdentity")
    if re.search(r'Effect\s*=\s*"Allow"', guard):
        problems.append("the guard must hold no Allow")
    if not re.search(r'bootstrap_pr_plan_mirror_prefix\s*=\s*"tf-provider-mirror/', new):
        problems.append("the mirror prefix must be under tf-provider-mirror/")
    return problems


def pr_plan_problems(texts: dict[str, str]) -> list[str]:
    new = texts.get(_NEW, "")
    return [*_role_problems(new), *_attachment_problems(texts), *_reads_problems(new), *_guard_problems(new)]


def _mut(file: str, old: str, new: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def run(texts: dict[str, str]) -> dict[str, str]:
        assert old in texts[file], f"mutation anchor missing: {old!r}"
        return {**texts, file: texts[file].replace(old, new, 1)}

    return run


_APPLY_REF = "otl-labs/ontheloop/.github/workflows/terraform-bootstrap-apply.yml@refs/heads/main"
_ATT = 'resource "aws_iam_role_policy_attachment" '
_GUARD_ATT = _ATT + '"bootstrap_pr_plan_guard" {'
_ROLE_LINE = "\n  role       = aws_iam_role.bootstrap_"
_MIRROR_LIST = '*"]\n      },\n      {\n        Sid      = "ProviderMirrorList"'

_MUTATIONS: dict[str, Callable[[dict[str, str]], dict[str, str]]] = {
    "apply_ref_added": _mut(
        _NEW,
        'refs/heads/main"]\n  bootstrap_pr_plan_mirror',
        f'refs/heads/main", "{_APPLY_REF}"]\n  bootstrap_pr_plan_mirror',
    ),
    "second_trust_statement": _mut(
        _NEW,
        "      }\n    ]\n  })\n\n  lifecycle {\n    prevent_destroy = true",
        '      },\n      {\n        Effect = "Allow"\n      }\n    ]\n  })\n\n  lifecycle {\n    prevent_destroy = true',
    ),
    "permissions_boundary_added": _mut(
        _NEW, "  # No permissions_boundary: see the header.\n", '  permissions_boundary = "arn:aws:iam::1:policy/x"\n'
    ),
    "role_renamed_outside_bootstrap": _mut(_NEW, 'name        = "bootstrap-pr-plan"', 'name        = "pr-plan"'),
    "fourth_attachment": _mut(
        _NEW,
        _GUARD_ATT,
        _ATT + '"bootstrap_pr_plan_extra" {' + _ROLE_LINE + "pr_plan.name\n"
        "  policy_arn = aws_iam_policy.bootstrap_apply_write.arn\n}\n\n" + _GUARD_ATT,
    ),
    "put_object_in_reads": _mut(_NEW, 'Action   = ["s3:GetObject"]', 'Action   = ["s3:GetObject", "s3:PutObject"]'),
    "mirror_widened_to_bucket": _mut(
        _NEW,
        "${local.data_lake_bucket_arn}/${local.bootstrap_pr_plan_mirror_prefix}" + _MIRROR_LIST,
        "${local.data_lake_bucket_arn}/" + _MIRROR_LIST,
    ),
    "not_action_extended": _mut(_NEW, '["sts:GetCallerIdentity"])', '["sts:GetCallerIdentity", "s3:PutObject"])'),
    "guard_gains_allow": _mut(
        _NEW,
        'Sid      = "DenyAccountIAMDump"\n        Effect   = "Deny"',
        'Sid      = "DenyAccountIAMDump"\n        Effect   = "Allow"',
    ),
    "attached_to_preview": _mut(
        _NEW,
        _GUARD_ATT + _ROLE_LINE + "pr_plan.name",
        _GUARD_ATT + _ROLE_LINE + "preview.name",
    ),
}


def test_bootstrap_pr_plan_role_is_read_only_and_scoped() -> None:
    assert pr_plan_problems(_texts()) == []


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_bootstrap_pr_plan_role_mutation_is_red(name: str) -> None:
    assert pr_plan_problems(_MUTATIONS[name](_texts())) != []
