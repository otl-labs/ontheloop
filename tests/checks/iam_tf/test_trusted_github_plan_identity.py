"""Standing guard for PLAN-trusted-plan-github-root (rec-4638): the terraform/github PR-plan role.

PR 2 pins the bootstrap pre-add: the apply role's IAMRolesRead names the new role ahead of its creation, so the
gated apply's iam:GetRole after the create and every later refresh succeed. PR 3 adds the role's own cases.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.checks.iam_tf._ci_platform_role_iam import _attr_raw, _hcl_statement_blocks, _jsonencode_statement_blocks
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READS_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply_reads.tf"
_ROLE_NAME = "agent-platform-github-ci-trusted-github-plan"
_ROLE_ARN = f"arn:aws:iam::${{var.account_id}}:role/{_ROLE_NAME}"
_ROLE_FILE = _REPO_ROOT / "terraform" / "personal" / "oidc_trusted_github_plan_role.tf"
_OIDC_FILE = _REPO_ROOT / "terraform" / "personal" / "oidc.tf"
_BOUNDARY = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
_BUCKET = "${aws_s3_bucket.data_lake.arn}"
_WORKFLOW_REF = "otl-labs/ontheloop/.github/workflows/terraform-github-plan.yml@refs/heads/main"
_EXPECTED_SIDS = {
    "TfstateGithubRead": (["s3:GetObject"], [f"{_BUCKET}/tfstate/github/terraform.tfstate"]),
    "ProviderMirrorRead": (["s3:GetObject"], [f"{_BUCKET}/tf-provider-mirror/*"]),
    "BucketListScoped": (["s3:ListBucket"], [_BUCKET]),
}
_FORBIDDEN_TEXT = ("secretsmanager", "ssm:", "tfplan", "tfstate/personal", "s3:Put", "s3:Delete")


def _iam_roles_read_resources(text: str, sid: str = "IAMRolesRead") -> list[str]:
    match = re.search(rf'[Ss]id\s*=\s*"{sid}".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, f"{sid} statement not found"
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return re.findall(r'"([^"]+)"', body)


def test_bootstrap_iam_roles_read_lists_the_github_plan_role() -> None:
    resources = _iam_roles_read_resources(_READS_FILE.read_text(encoding="utf-8"))
    assert resources.count(_ROLE_ARN) == 1


def test_bootstrap_assertion_reddens_without_the_arn() -> None:
    text = _READS_FILE.read_text(encoding="utf-8").replace(_ROLE_ARN, "arn:aws:iam::${var.account_id}:role/other")
    assert _ROLE_ARN not in _iam_roles_read_resources(text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(text: str, kind: str, rtype: str, name: str) -> str:
    m = re.search(rf'^{kind}\s+"{rtype}"\s+"{name}"\s*\{{', _strip_comments(text), re.M)
    assert m, f"{kind} {rtype}.{name} not found"
    return _block_body(text, m.end() - 1)


def trust_problems(text: str) -> list[str]:
    role = _block(text, "resource", "aws_iam_role", "github_ci_trusted_github_plan")
    problems: list[str] = []
    if _attr_raw(role, "permissions_boundary") != f'"{_BOUNDARY}"':
        problems.append("permissions_boundary is not the apply boundary")
    statements = _jsonencode_statement_blocks(role)
    if len(statements) != 1:
        return problems + [f"expected exactly one trust statement, found {len(statements)}"]
    stmt = _strip_comments(statements[0])
    cond = re.search(r"Condition\s*=\s*\{", _mask(stmt))
    if cond is None:
        return problems + ["trust has no Condition"]
    body = _block_body(stmt, cond.end() - 1)
    operators = re.findall(r"^\s*(\w+)\s*=\s*\{", _mask(body), re.M)
    if operators != ["StringEquals"]:
        return problems + [f"condition operators are {operators}, expected only StringEquals"]
    se = _block_body(body, re.search(r"StringEquals\s*=\s*\{", _mask(body)).end() - 1)
    keys = re.findall(r'"token\.actions\.githubusercontent\.com:(\w+)"\s*=', se)
    if sorted(keys) != ["aud", "job_workflow_ref", "sub"]:
        problems.append(f"StringEquals keys are {sorted(keys)}")
    sub = re.search(r':sub"\s*=\s*flatten\(\[(.*?)\]\)\s*\n', se, re.S)
    if sub is None or "for repo in local.github_repos" not in sub.group(1):
        problems.append("sub does not iterate local.github_repos")
    elif re.findall(r'"([^"]+)"', sub.group(1)) != ["repo:${repo}:environment:${local.trusted_plan_environment}"]:
        problems.append("sub is not the tf-trusted-plan environment")
    if not re.search(rf':job_workflow_ref"\s*=\s*"{re.escape(_WORKFLOW_REF)}"\s*$', se, re.M):
        problems.append("job_workflow_ref is not the one exact github-plan workflow ref")
    return problems


def _items(raw: str) -> list[str]:
    return [m[0] or m[1] for m in re.findall(r'"([^"]+)"|([A-Za-z_][\w.]*)', raw)]


def policy_problems(text: str) -> list[str]:
    doc = _block(text, "data", "aws_iam_policy_document", "github_ci_trusted_github_plan")
    problems: list[str] = []
    if _attr_raw(doc, "source_policy_documents") is not None:
        problems.append("document sources another document")
    seen = {}
    for block in _hcl_statement_blocks(doc):
        stmt_text = _strip_comments(block)
        sid = re.search(r'sid\s*=\s*"([^"]+)"', stmt_text)
        acts = re.search(r"actions\s*=\s*\[([^\]]*)\]", stmt_text)
        ress = re.search(r"resources\s*=\s*\[([^\]]*)\]", stmt_text)
        seen[sid.group(1) if sid else None] = (
            re.findall(r'"([^"]+)"', acts.group(1)) if acts else [],
            [_BUCKET if t == "aws_s3_bucket.data_lake.arn" else t for t in _items(ress.group(1))] if ress else [],
            "Deny" not in stmt_text,
            block,
        )
    if set(seen) != set(_EXPECTED_SIDS):
        problems.append(f"Sids are {sorted(map(str, seen))}")
    for sid, (actions, resources) in _EXPECTED_SIDS.items():
        if sid in seen and (seen[sid][0], seen[sid][1]) != (actions, resources):
            problems.append(f"{sid} grants {seen[sid][:2]}")
    listing = seen.get("BucketListScoped")
    if listing is not None:
        cond = _mask(listing[3])
        if (
            not re.search(r'test\s*=\s*"StringLike"', listing[3])
            or "tf-provider-mirror/*" not in listing[3]
            or "condition" not in cond
        ):
            problems.append("BucketListScoped lacks the s3:prefix StringLike condition")
    for token in _FORBIDDEN_TEXT:
        if token in _strip_comments(doc):
            problems.append(f"document names {token}")
    return problems


def test_trust_is_one_environment_and_workflow_bound_statement() -> None:
    assert trust_problems(_read(_ROLE_FILE)) == []


def test_policy_is_exactly_the_three_statements() -> None:
    assert policy_problems(_read(_ROLE_FILE)) == []


def test_role_has_one_inline_policy_naming_the_role() -> None:
    text = _strip_comments(_read(_ROLE_FILE))
    assert text.count('resource "aws_iam_role_policy"') == 1
    assert "role   = aws_iam_role.github_ci_trusted_github_plan.id" in text
    assert "aws_iam_role_policy_attachment" not in text


def test_personal_iam_ci_roles_read_lists_the_github_plan_role() -> None:
    resources = _iam_roles_read_resources(_read(_OIDC_FILE), "IAMCIRolesRead")
    assert resources.count(_ROLE_ARN) == 1


def test_red_pr_sub_or_string_like_in_trust() -> None:
    base = _read(_ROLE_FILE)
    pr_sub = base.replace(
        '"repo:${repo}:environment:${local.trusted_plan_environment}"',
        '"repo:${repo}:environment:${local.trusted_plan_environment}",\n "repo:${repo}:pull_request"',
    )
    assert trust_problems(pr_sub)
    assert trust_problems(base.replace("StringEquals = {", "StringLike = {", 1))
    assert trust_problems(base.replace("terraform-github-plan.yml@refs/heads/main", "*@refs/heads/main"))
    assert trust_problems(base.replace("github-ci-apply-boundary", "other-boundary"))


def test_red_added_secret_state_write_or_wide_list() -> None:
    base = _read(_ROLE_FILE)
    extra = (
        '  statement {\n    sid       = "Extra"\n    effect    = "Allow"\n'
        '    actions   = ["secretsmanager:GetSecretValue"]\n    resources = ["*"]\n  }\n\n'
    )
    assert any("Sids" in p for p in policy_problems(base.replace("  statement {\n", extra + "  statement {\n", 1)))
    assert policy_problems(base.replace('actions   = ["s3:GetObject"]', 'actions   = ["s3:GetObject", "s3:PutObject"]', 1))
    no_prefix = re.sub(r"\n    condition \{.*?\n    \}\n", "\n", base, count=1, flags=re.S)
    assert any("prefix" in p for p in policy_problems(no_prefix))
