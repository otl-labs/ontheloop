"""Standing guard for PLAN-trusted-plan-identity (rec-4636).

The trusted speculative-plan role: one environment- and job_workflow_ref-bound trust statement, exactly
its pinned Sids over the shared refresh-read surface, no state write or saved-plan read, and both apply-side
read lists naming it. The planner grants nothing on tfplan/*.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from scripts.checks.ci_guards.validate_trusted_plan_environment import ENV, TRUSTED_WORKFLOWS
from tests.checks.iam_tf._ci_platform_role_iam import (
    _attr_raw,
    _hcl_statement_blocks,
    _jsonencode_statement_blocks,
    _list_or_scalar,
    _parse_hcl_statement,
)
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READS_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply_reads.tf"
_PERSONAL = _REPO_ROOT / "terraform" / "personal"
_ROLE_FILE = _PERSONAL / "oidc_trusted_plan_role.tf"
_ROLE_NAME = "agent-platform-github-ci-trusted-plan"
_ROLE_ARN = f"arn:aws:iam::${{var.account_id}}:role/{_ROLE_NAME}"
_BUCKET = "${aws_s3_bucket.data_lake.arn}"
_BOUNDARY = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
_SM_ARN = "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:"
_SSM_ARN = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/"
_EXPECTED_SIDS = {
    "TfplanPersonalWrite": (["s3:PutObject"], [f"{_BUCKET}/tfplan/personal/*"]),
    "ProviderMirrorRead": (["s3:GetObject"], [f"{_BUCKET}/tf-provider-mirror/*"]),
    "PlanSecretValueRead": (
        ["secretsmanager:GetSecretValue"],
        [f"{_SM_ARN}neon-api-key-*", f"{_SM_ARN}ducklake-neon-catalog-dsn-*"],
    ),
    "PlanSecretParameterRead": (
        ["ssm:GetParameter"],
        [f"{_SSM_ARN}neon-api-key", f"{_SSM_ARN}ducklake-neon-catalog-dsn"],
    ),
    "TfstateGithubRead": (["s3:GetObject"], [f"{_BUCKET}/tfstate/github/terraform.tfstate"]),
}
_FORBIDDEN_TRUST_TEXT = ("pull_request", "refs/pull", "agent/", "ref:refs/heads")
_WRITE_PROBES = ("s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectAcl")


def _block(text: str, kind: str, rtype: str, name: str) -> str:
    m = re.search(rf'^{kind}\s+"{rtype}"\s+"{name}"\s*\{{', _strip_comments(text), re.M)
    assert m, f"{kind} {rtype}.{name} not found"
    return _block_body(text, m.end() - 1)


def _iam_roles_read_resources(text: str, sid: str = "IAMRolesRead") -> list[str]:
    match = re.search(rf'[Ss]id\s*=\s*"{sid}".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, f"{sid} statement not found"
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return re.findall(r'"([^"]+)"', body)


def _local_list(text: str, name: str) -> list[str]:
    raw = _attr_raw(text, name)
    assert raw is not None, f"local.{name} not found"
    return _list_or_scalar(raw)


def trust_problems(text: str) -> list[str]:
    role = _block(text, "resource", "aws_iam_role", "github_ci_trusted_plan")
    problems: list[str] = []
    if _attr_raw(role, "permissions_boundary") != f'"{_BOUNDARY}"':
        problems.append("permissions_boundary is not the apply boundary")
    statements = _jsonencode_statement_blocks(role)
    if len(statements) != 1:
        return problems + [f"expected exactly one trust statement, found {len(statements)}"]
    stmt = _strip_comments(statements[0])
    if any(t in stmt for t in _FORBIDDEN_TRUST_TEXT):
        problems.append("trust names a PR, branch or agent subject")
    cond = re.search(r"Condition\s*=\s*\{", _mask(stmt))
    if cond is None:
        return problems + ["trust has no Condition"]
    body = _block_body(stmt, cond.end() - 1)
    operators = re.findall(r"^\s*(\w+)\s*=\s*\{", _mask(body), re.M)
    if operators != ["StringEquals"]:
        problems.append(f"condition operators are {operators}, expected only StringEquals")
        return problems
    se = _block_body(body, re.search(r"StringEquals\s*=\s*\{", _mask(body)).end() - 1)
    keys = re.findall(r'"token\.actions\.githubusercontent\.com:(\w+)"\s*=', se)
    if sorted(keys) != ["aud", "job_workflow_ref", "sub"]:
        problems.append(f"StringEquals keys are {sorted(keys)}")
    if not re.search(r':aud"\s*=\s*"sts\.amazonaws\.com"', se):
        problems.append("aud is not sts.amazonaws.com")
    sub = re.search(r':sub"\s*=\s*flatten\(\[(.*?)\]\)\s*\n', se, re.S)
    if sub is None or "for repo in local.github_repos" not in sub.group(1):
        problems.append("sub does not iterate local.github_repos")
    else:
        forms = re.findall(r'"([^"]+)"', sub.group(1))
        if forms != ["repo:${repo}:environment:${local.trusted_plan_environment}"]:
            problems.append(f"sub forms are {forms}")
    if not re.search(r':job_workflow_ref"\s*=\s*local\.trusted_plan_workflow_refs\s*$', se, re.M):
        problems.append("job_workflow_ref is not local.trusted_plan_workflow_refs")
    env = re.search(r'^\s*trusted_plan_environment\s*=\s*"([^"]*)"', _strip_comments(text), re.M)
    if env is None or env.group(1) != ENV:
        problems.append("local.trusted_plan_environment is not the fenced Environment")
    refs = _local_list(text, "trusted_plan_workflow_refs")
    files = set()
    for ref in refs:
        m = re.fullmatch(r"[^*?/]+/[^*?/]+/\.github/workflows/([^*?/@]+)@refs/heads/main", ref)
        if m is None:
            problems.append(f"job_workflow_ref value {ref!r} is not an exact refs/heads/main workflow ref")
        else:
            files.add(m.group(1))
    if files != set(TRUSTED_WORKFLOWS):
        problems.append(f"job_workflow_ref files {sorted(files)} != TRUSTED_WORKFLOWS")
    return problems


def own_sid_problems(text: str) -> list[str]:
    doc = _block(text, "data", "aws_iam_policy_document", "github_ci_trusted_plan")
    problems: list[str] = []
    if _attr_raw(doc, "source_policy_documents") != "[data.aws_iam_policy_document.ci_full_refresh_read.json]":
        problems.append("source_policy_documents is not exactly ci_full_refresh_read")
    seen = {}
    for block in _hcl_statement_blocks(doc):
        stmt = _parse_hcl_statement(block)
        seen[stmt.sid] = (stmt.effect, stmt.actions, stmt.resources, bool(stmt.not_actions or stmt.not_resources))
    if set(seen) != set(_EXPECTED_SIDS):
        problems.append(f"Sids are {sorted(map(str, seen))}")
    for sid, (actions, resources) in _EXPECTED_SIDS.items():
        if sid in seen and seen[sid] != ("Allow", actions, resources, False):
            problems.append(f"{sid} grants {seen[sid]}")
    if re.search(r"^\s*condition\s*\{", _mask(doc), re.M):
        problems.append("a statement carries a condition")
    return problems


def _covers(pattern: str, concrete: str) -> bool:
    return fnmatch.fnmatchcase(concrete.lower(), pattern.lower())


def _resource_covers(resource: str, prefix: str) -> bool:
    if resource == "*":
        return True
    if not resource.startswith(f"{_BUCKET}/"):
        return False
    tail = resource[len(_BUCKET) + 1 :]
    return tail.startswith(prefix) or _covers(tail, f"{prefix}probe")


def resolved_policy_problems(texts: list[str]) -> list[str]:
    """No Allow in the role's own policy plus every document it sources reads tfplan/* or writes
    tfstate/*, lambda-packages/* or convergence/*."""
    problems: list[str] = []
    for text in texts:
        for block in _hcl_statement_blocks(text):
            stmt = _parse_hcl_statement(block)
            if stmt.effect != "Allow":
                continue
            reads = any(_covers(a, "s3:GetObject") for a in stmt.actions)
            writes = any(_covers(a, probe) for a in stmt.actions for probe in _WRITE_PROBES)
            for resource in stmt.resources:
                if reads and _resource_covers(resource, "tfplan/"):
                    problems.append(f"{stmt.sid} reads tfplan/*")
                for prefix in ("tfstate/", "lambda-packages/", "convergence/"):
                    if writes and _resource_covers(resource, prefix):
                        problems.append(f"{stmt.sid} writes {prefix}*")
    return problems


def _policy_docs(texts: list[str], root: str = "github_ci_trusted_plan") -> list[str]:
    """The root document and every document it sources, followed recursively across texts."""
    docs: dict[str, str] = {}
    pending = [root]
    while pending:
        name = pending.pop()
        if name in docs:
            continue
        body = next(
            (
                _block(t, "data", "aws_iam_policy_document", name)
                for t in texts
                if re.search(rf'^data\s+"aws_iam_policy_document"\s+"{name}"\s*\{{', _strip_comments(t), re.M)
            ),
            None,
        )
        assert body is not None, f"policy document {name} not found"
        docs[name] = body
        pending += re.findall(r"data\.aws_iam_policy_document\.(\w+)\.json", _strip_comments(body))
    return list(docs.values())


def _personal_texts(role_text: str | None = None) -> list[str]:
    return [role_text if role_text is not None and p == _ROLE_FILE else _read(p) for p in sorted(_PERSONAL.glob("*.tf"))]


def attachment_counts(texts: list[str]) -> tuple[int, int]:
    target = re.compile(r'^\s*role\s*=\s*(aws_iam_role\.github_ci_trusted_plan\.(id|name)|"' + _ROLE_NAME + r'")\s*$', re.M)
    inline = attached = 0
    for text in texts:
        for kind in ("aws_iam_role_policy", "aws_iam_role_policy_attachment"):
            for m in re.finditer(rf'^resource\s+"{kind}"\s+"\w+"\s*\{{', _strip_comments(text), re.M):
                if target.search(_strip_comments(_block_body(text, m.end() - 1))):
                    if kind == "aws_iam_role_policy":
                        inline += 1
                    else:
                        attached += 1
    return inline, attached


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _planner_tfplan_grants(text: str) -> list[str]:
    grants: list[str] = []
    for block in _hcl_statement_blocks(text):
        stmt = _parse_hcl_statement(block)
        if stmt.effect == "Allow" and any(_resource_covers(r, "tfplan/") for r in stmt.resources):
            grants.append(stmt.sid)
    return grants


def test_bootstrap_iam_roles_read_lists_trusted_plan_role() -> None:
    resources = _iam_roles_read_resources(_read(_READS_FILE))
    assert resources.count(_ROLE_ARN) == 1


def test_bootstrap_assertion_reddens_without_the_arn() -> None:
    text = _read(_READS_FILE).replace(_ROLE_ARN, "arn:aws:iam::${var.account_id}:role/other")
    assert _ROLE_ARN not in _iam_roles_read_resources(text)


def test_personal_iam_ci_roles_read_lists_trusted_plan_role() -> None:
    resources = _iam_roles_read_resources(_read(_PERSONAL / "oidc.tf"), "IAMCIRolesRead")
    assert resources.count(_ROLE_ARN) == 1


def test_trust_is_one_environment_and_workflow_bound_statement() -> None:
    assert trust_problems(_read(_ROLE_FILE)) == []


def test_policy_sids_are_exactly_the_four() -> None:
    assert own_sid_problems(_read(_ROLE_FILE)) == []


def test_resolved_policy_reads_no_saved_plan_and_writes_no_state() -> None:
    assert resolved_policy_problems(_policy_docs(_personal_texts())) == []


def test_role_has_one_inline_policy_and_no_attachments() -> None:
    assert attachment_counts([_read(p) for p in sorted(_PERSONAL.glob("*.tf"))]) == (1, 0)


def test_planner_grants_nothing_on_tfplan() -> None:
    assert _planner_tfplan_grants(_read(_PERSONAL / "oidc_pipeline_roles.tf")) == []


def test_red_pr_sub_in_trust() -> None:
    text = _read(_ROLE_FILE).replace(
        '"repo:${repo}:environment:${local.trusted_plan_environment}"',
        '"repo:${repo}:environment:${local.trusted_plan_environment}",\n "repo:${repo}:pull_request"',
    )
    assert trust_problems(text)


def test_red_string_like_job_workflow_ref() -> None:
    ref_line = '"token.actions.githubusercontent.com:job_workflow_ref" = local.trusted_plan_workflow_refs\n'
    text = _read(_ROLE_FILE).replace(ref_line, "")
    string_like = f"          }}\n          StringLike = {{\n            {ref_line}          }}\n        }}\n"
    text = text.replace("          }\n        }\n", string_like, 1)
    assert any("operators" in p for p in trust_problems(text))


def test_red_wildcard_or_unknown_workflow_ref() -> None:
    base = _read(_ROLE_FILE)
    assert trust_problems(base.replace("terraform-plan-trusted.yml@refs/heads/main", "*@refs/heads/main"))
    assert trust_problems(base.replace("terraform-plan-trusted.yml@refs/heads/main", "other.yml@refs/heads/main"))
    dev = base.replace('terraform-github-plan.yml@refs/heads/main"', 'terraform-github-plan.yml@refs/heads/dev"')
    assert trust_problems(dev)


def test_red_added_ducklake_build_inputs_read() -> None:
    extra = (
        '  statement {\n    sid       = "DucklakeBuildInputsRead"\n    effect    = "Allow"\n'
        f'    actions   = ["s3:GetObject"]\n    resources = ["{_BUCKET}/ducklake-build-inputs/*"]\n  }}\n\n'
    )
    text = _read(_ROLE_FILE).replace("  statement {\n", extra + "  statement {\n", 1)
    assert any("Sids" in p for p in own_sid_problems(text))


def test_red_get_object_on_tfplan() -> None:
    text = _read(_ROLE_FILE).replace('actions   = ["s3:PutObject"]', 'actions   = ["s3:GetObject", "s3:PutObject"]')
    assert own_sid_problems(text)
    assert resolved_policy_problems(_policy_docs(_personal_texts(text))) == ["TfplanPersonalWrite reads tfplan/*"]


def test_red_state_write_and_wildcards_in_sourced_document() -> None:
    doc = (
        'statement {\n sid = "Wide"\n effect = "Allow"\n actions = ["s3:*"]\n'
        f' resources = ["{_BUCKET}/*"]\n}}\nstatement {{\n sid = "Star"\n actions = ["s3:Put*"]\n resources = ["*"]\n}}\n'
        'statement {\n sid = "Denied"\n effect = "Deny"\n actions = ["s3:*"]\n resources = ["*"]\n}\n'
        'statement {\n sid = "Other"\n actions = ["s3:PutObject"]\n resources = ["arn:aws:s3:::x/*"]\n}\n'
    )
    problems = resolved_policy_problems([doc])
    assert "Wide reads tfplan/*" in problems
    assert {"Wide writes tfstate/*", "Star writes lambda-packages/*", "Star writes convergence/*"} <= set(problems)
    assert not any(p.startswith(("Denied", "Other")) for p in problems)


def test_red_attachment_or_second_inline_policy() -> None:
    extra = (
        'resource "aws_iam_role_policy_attachment" "x" {\n  role       = aws_iam_role.github_ci_trusted_plan.name\n'
        '  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"\n}\n'
        f'resource "aws_iam_role_policy" "y" {{\n  role = "{_ROLE_NAME}"\n  policy = "{{}}"\n}}\n'
    )
    assert attachment_counts([_read(_ROLE_FILE), extra]) == (2, 1)


def test_red_boundary_and_planner_get_object() -> None:
    assert trust_problems(_read(_ROLE_FILE).replace("github-ci-apply-boundary", "other-boundary"))
    planner = _read(_PERSONAL / "oidc_pipeline_roles.tf").replace(
        'data "aws_iam_policy_document" "github_ci_planner" {',
        'data "aws_iam_policy_document" "github_ci_planner" {\n  statement {\n    sid       = "TfplanWrite"\n'
        '    effect    = "Allow"\n    actions   = ["s3:PutObject"]\n'
        '    resources = ["${aws_s3_bucket.data_lake.arn}/tfplan/personal/*"]\n  }\n',
    )
    assert _planner_tfplan_grants(planner) == ["TfplanWrite"]


def test_resolved_policy_follows_nested_sources() -> None:
    texts = _personal_texts()
    assert len(_policy_docs(texts)) == 3
    nested = (
        'data "aws_iam_policy_document" "nested_extra" {\n  statement {\n    sid       = "Nested"\n'
        f'    actions   = ["s3:GetObject"]\n    resources = ["{_BUCKET}/tfplan/*"]\n  }}\n}}\n'
    )
    ssm = "data.aws_iam_policy_document.ci_ssm_refresh_read.json"
    oidc = _read(_PERSONAL / "oidc.tf").replace(f"[{ssm}]", f"[{ssm}, data.aws_iam_policy_document.nested_extra.json]", 1)
    assert oidc != _read(_PERSONAL / "oidc.tf")
    assert resolved_policy_problems(_policy_docs([_read(_ROLE_FILE), oidc, nested])) == ["Nested reads tfplan/*"]
