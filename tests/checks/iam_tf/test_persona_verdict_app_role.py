"""Standing guard for PLAN-persona-verdict-app-grant (T4.32 egress E4): the verdict App key's role and Environment.

Three PRs share this file: PR 1 (bootstrap pre-stage and boundary Deny), PR 2 (the persona-verdict-app Environment and
the workflow fence) and PR 3 (the role in terraform/personal). Each case is a pure function of HCL or workflow text.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from scripts.checks.ci_guards.validate_trusted_plan_environment import _arg, _blocks
from scripts.checks.iam_tf.validate_secret_parameter_grants import covers_value_verb, normalise, parse_statements
from scripts.verify_ci_workflow import _load
from tests.checks.iam_tf._ci_platform_role_iam import _attr_raw, _hcl_statement_blocks, _jsonencode_statement_blocks
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TF_GITHUB_DIR = _REPO_ROOT / "terraform" / "github"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_ENV = "persona-verdict-app"
_ENV_WORKFLOW = "persona-verdict.yml"
_ENV_JOB = "post"
_ROLE_NAME = "agent-platform-github-ci-persona-verdict-app"
_BOOTSTRAP_DIR = _REPO_ROOT / "terraform" / "bootstrap"
_READS_FILE = _BOOTSTRAP_DIR / "github_ci_apply_reads.tf"
_BOUNDARY_FILE = _BOOTSTRAP_DIR / "github_ci_apply_boundary.tf"
_ROLE_ARN = f"arn:aws:iam::${{var.account_id}}:role/{_ROLE_NAME}"
_DENY_SID = "DenyVerdictAppKeyRead"
_KEY = "arn:aws:ssm:${var.aws_region}:*:parameter/secret/agent-platform-persona-verdict-app-key"
_DENY_RESOURCES = {
    _KEY,
    "arn:aws:ssm:${var.aws_region}:*:parameter",
    "arn:aws:ssm:${var.aws_region}:*:parameter/",
    "arn:aws:ssm:${var.aws_region}:*:parameter/secret",
    "arn:aws:ssm:${var.aws_region}:*:parameter/secret/",
}
_SECRET_TYPES = ("github_actions_environment_secret", "github_actions_environment_variable", "github_actions_secret")


def _tf_github_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_TF_GITHUB_DIR.glob("*.tf")))


def environment_problems(text: str) -> list[str]:
    envs = {n: b for n, b in _blocks(text, "github_repository_environment").items() if _arg(b, "environment") == f'"{_ENV}"'}
    if len(envs) != 1:
        return [f"expected exactly one {_ENV} environment, found {len(envs)}"]
    name, block = next(iter(envs.items()))
    problems = [
        f"{arg} must be {want}"
        for arg, want in (
            ("can_admins_bypass", "false"),
            ("protected_branches", "false"),
            ("custom_branch_policies", "true"),
            ("prevent_destroy", "true"),
        )
        if _arg(block, arg) != want
    ]
    if re.search(r"^\s*(reviewers|wait_timer)\b", block, re.M):
        problems.append("environment declares reviewers or a wait timer")
    refs = {f"github_repository_environment.{name}.environment", f'"{_ENV}"'}
    policies = [
        b for b in _blocks(text, "github_repository_environment_deployment_policy").values() if _arg(b, "environment") in refs
    ]
    if len(policies) != 1:
        problems.append(f"expected exactly one deployment policy, found {len(policies)}")
    for b in policies:
        if _arg(b, "branch_pattern") != '"main"' or _arg(b, "tag_pattern") is not None:
            problems.append('deployment policy must be branch_pattern = "main" with no tag_pattern')
    return problems


def secret_problems(text: str) -> list[str]:
    return [
        f"{rtype}.{name} names {token}"
        for rtype in _SECRET_TYPES
        for name, block in _blocks(text, rtype).items()
        for token in (_ENV, "persona_verdict_app")
        if token in block or token in name
    ]


def _workflow_env(job: dict[str, Any]) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return None if env is None else str(env)


def _jobs(wf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jobs = wf.get("jobs")
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def _assumes_role(job: dict[str, Any]) -> bool:
    steps = job.get("steps") if isinstance(job.get("steps"), list) else []
    return any(
        isinstance(s, dict)
        and "configure-aws-credentials" in str(s.get("uses", ""))
        and str((s.get("with") or {}).get("role-to-assume", "")).endswith(f"/{_ROLE_NAME}")
        for s in steps
    )


def workflow_problems(workflows: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for fname, wf in workflows.items():
        for jname, job in _jobs(wf).items():
            env = _workflow_env(job)
            allowed = fname == _ENV_WORKFLOW and jname == _ENV_JOB
            if env is not None and "${{" in env:
                problems.append(f"{fname}:{jname} names an environment by expression")
            if env == _ENV and not allowed:
                problems.append(f"{fname}:{jname} names the {_ENV} environment")
            if _assumes_role(job) and not allowed:
                problems.append(f"{fname}:{jname} assumes the verdict App key role")
    return problems


def _real_workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _load(str(p)) for p in sorted(_WORKFLOWS_DIR.glob("*.yml"))}


def test_persona_verdict_app_environment_shape() -> None:
    assert environment_problems(_tf_github_text()) == []


def test_no_secret_or_variable_names_the_app_environment() -> None:
    assert secret_problems(_tf_github_text()) == []


def test_workflow_fence() -> None:
    assert workflow_problems(_real_workflows()) == []


def test_red_environment_shape() -> None:
    base = _tf_github_text()
    env = _blocks(base, "github_repository_environment")["persona_verdict_app"]
    policy = _blocks(base, "github_repository_environment_deployment_policy")["persona_verdict_app_main"]
    for broken in (
        env.replace("can_admins_bypass = false", "can_admins_bypass = true"),
        env.replace("  deployment_branch_policy {", "  reviewers {\n    users = [1]\n  }\n\n  deployment_branch_policy {"),
        env.replace("  deployment_branch_policy {", "  wait_timer = 5\n\n  deployment_branch_policy {"),
        env.replace("protected_branches     = false", "protected_branches     = true"),
        env.replace("custom_branch_policies = true", "custom_branch_policies = false"),
        env.replace("prevent_destroy = true", "prevent_destroy = false"),
    ):
        assert environment_problems(base.replace(env, broken)), broken
    assert environment_problems(base.replace(policy, policy.replace('branch_pattern = "main"', 'branch_pattern = "*"')))
    second = (
        '\nresource "github_repository_environment_deployment_policy" "persona_verdict_app_extra" {\n'
        "  environment    = github_repository_environment.persona_verdict_app.environment\n"
        '  branch_pattern = "main"\n}\n'
    )
    assert any("exactly one deployment policy" in p for p in environment_problems(base + second))
    assert environment_problems("")


def test_red_environment_secret() -> None:
    extra = (
        f'\nresource "github_actions_environment_secret" "x" {{\n  environment     = "{_ENV}"\n  secret_name     = "X"\n}}\n'
    )
    assert secret_problems(_tf_github_text() + extra)


def test_red_workflow_fence() -> None:
    job = {"runs-on": "ubuntu-latest", "environment": _ENV, "steps": []}
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": job}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": "push", "jobs": {"other": job}}})
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": {"environment": {"name": _ENV}}}}})
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": {"environment": "${{ inputs.env }}"}}}})
    assume = {
        "steps": [
            {
                "uses": "aws-actions/configure-aws-credentials@v4",
                "with": {"role-to-assume": f"arn:aws:iam::123:role/{_ROLE_NAME}"},  # pragma: allowlist secret
            }
        ]
    }
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": assume}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": "push", "jobs": {"other": assume}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": "push", "jobs": {_ENV_JOB: {**job, **assume}}}}) == []


def _iam_roles_read_resources(text: str) -> list[str]:
    match = re.search(r'[Ss]id\s*=\s*"IAMRolesRead".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, "IAMRolesRead statement not found"
    return re.findall(r'"([^"]+)"', re.sub(r"#[^\n]*", "", match.group(1)))


def reads_problems(text: str) -> list[str]:
    count = _iam_roles_read_resources(text).count(_ROLE_ARN)
    return [] if count == 1 else [f"IAMRolesRead names the role {count} times, expected once"]


def boundary_deny_problems(text: str) -> list[str]:
    blocks = [b for b in _jsonencode_statement_blocks(text) if f'"{_DENY_SID}"' in b]
    if len(blocks) != 1:
        return [f"expected exactly one {_DENY_SID}, found {len(blocks)}"]
    block = blocks[0]
    problems: list[str] = []
    if _attr_raw(block, "Effect") != '"Deny"':
        problems.append("Effect must be Deny")
    if _attr_raw(block, "Action") != '"ssm:GetParameter*"':
        problems.append('Action must be exactly the scalar "ssm:GetParameter*"')
    resources = set(re.findall(r'"([^"]+)"', _attr_raw(block, "Resource") or ""))
    if resources != _DENY_RESOURCES:
        problems.append(f"Resource set differs: {sorted(resources ^ _DENY_RESOURCES)}")
    cond = re.search(r"Condition\s*=\s*\{(.*)\}", _strip_comments(block), re.DOTALL)
    condition = cond.group(1) if cond else ""
    if not re.search(r"\bArnNotLike\b", condition) or "StringNotLike" in condition:
        problems.append("Condition must be ArnNotLike")
    if re.findall(r'"([^"]+)"', condition) != ["aws:PrincipalArn", f"arn:aws:iam::*:role/{_ROLE_NAME}"]:
        problems.append("Condition must name exactly the role")
    return problems


def test_bootstrap_pre_stage_names_the_role() -> None:
    assert reads_problems(_READS_FILE.read_text(encoding="utf-8")) == []


def test_boundary_deny_shape() -> None:
    assert boundary_deny_problems(_BOUNDARY_FILE.read_text(encoding="utf-8")) == []


def test_red_pre_stage() -> None:
    text = _READS_FILE.read_text(encoding="utf-8")
    assert reads_problems(text.replace(_ROLE_ARN, "arn:aws:iam::${var.account_id}:role/other"))


def test_red_boundary_deny() -> None:
    text = _BOUNDARY_FILE.read_text(encoding="utf-8")
    broken = (
        text.replace('"arn:aws:ssm:${var.aws_region}:*:parameter/secret/",', "", 1),
        text.replace(
            f'ArnNotLike = {{ "aws:PrincipalArn" = "arn:aws:iam::*:role/{_ROLE_NAME}"',
            f'StringNotLike = {{ "aws:PrincipalArn" = "arn:aws:iam::*:role/{_ROLE_NAME}"',
            1,
        ),
        text.replace(f'role/{_ROLE_NAME}"', 'role/x"', 1),
        text.replace('Action = "ssm:GetParameter*"', 'Action = ["ssm:GetParameter*"]', 1),
        text.replace(f'"{_DENY_SID}"', '"Other"', 1),
        text.replace(
            'Effect = "Deny"\n        Action = "ssm:GetParameter*"',
            'Effect = "Allow"\n        Action = "ssm:GetParameter*"',
            1,
        ),
    )
    for variant in broken:
        assert variant != text
        assert boundary_deny_problems(variant), variant


_PERSONAL_DIR = _REPO_ROOT / "terraform" / "personal"
_ROLE_FILE = _PERSONAL_DIR / "oidc_persona_verdict_app_role.tf"
_OIDC_FILE = _PERSONAL_DIR / "oidc.tf"
_BOUNDARY_ARN = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
_JOB_WORKFLOW_REF = "otl-labs/ontheloop/.github/workflows/persona-verdict.yml@refs/heads/main"
_KEY_LITERAL = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-persona-verdict-app-key"
_KEY_PATH = "parameter/secret/agent-platform-persona-verdict-app-key"
_LITERAL_READERS = {("oidc_persona_verdict_app_role.tf", "VerdictAppKeyParameterRead")}
_PATTERN_READERS = {("github_ci_apply_boundary.tf", "DataPlaneAllow"), ("platform_admin_policies.tf", "SecretParameterAdmin")}
_WRITE_ALLOWED_IN = {"platform_admin_policies.tf": "SecretParameterAdmin", "github_ci_apply_boundary.tf": "DataPlaneAllow"}
_WRITE_VERBS = ("ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(text: str, kind: str, rtype: str, name: str) -> str:
    m = re.search(rf'^{kind}\s+"{rtype}"\s+"{name}"\s*\{{', _strip_comments(text), re.M)
    assert m, f"{kind} {rtype}.{name} not found"
    return _block_body(_strip_comments(text), m.end() - 1)


def trust_problems(text: str) -> list[str]:
    role = _block(text, "resource", "aws_iam_role", "github_ci_persona_verdict_app")
    problems: list[str] = []
    if _attr_raw(role, "permissions_boundary") != f'"{_BOUNDARY_ARN}"':
        problems.append("permissions_boundary is not the apply boundary")
    env_local = re.search(r'^\s*persona_verdict_app_environment\s*=\s*"([^"]*)"', _strip_comments(text), re.M)
    if env_local is None or env_local.group(1) != _ENV:
        problems.append(f'local.persona_verdict_app_environment is not "{_ENV}"')
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
    elif re.findall(r'"([^"]+)"', sub.group(1)) != ["repo:${repo}:environment:${local.persona_verdict_app_environment}"]:
        problems.append("sub is not the persona-verdict-app environment")
    if not re.search(rf':job_workflow_ref"\s*=\s*"{re.escape(_JOB_WORKFLOW_REF)}"\s*$', se, re.M):
        problems.append("job_workflow_ref is not the one exact persona-verdict.yml ref")
    return problems


def policy_problems(text: str) -> list[str]:
    doc = _block(text, "data", "aws_iam_policy_document", "github_ci_persona_verdict_app")
    problems: list[str] = []
    if _attr_raw(doc, "source_policy_documents") is not None:
        problems.append("document sources another document")
    statements = [_strip_comments(b) for b in _hcl_statement_blocks(doc)]
    if len(statements) != 1:
        return problems + [f"expected exactly one statement, found {len(statements)}"]
    stmt = statements[0]
    for attr, want in (
        ("sid", '"VerdictAppKeyParameterRead"'),
        ("effect", '"Allow"'),
        ("actions", '["ssm:GetParameter"]'),
        ("resources", f'["{_KEY_LITERAL}"]'),
    ):
        if _attr_raw(stmt, attr) != want:
            problems.append(f"{attr} is not {want}")
    if re.search(r"^\s*(condition|not_actions|not_resources)\b", stmt, re.M):
        problems.append("statement carries a condition or a not_* form")
    return problems


def _tf_files() -> dict[str, str]:
    return {p.name: _read(p) for d in (_PERSONAL_DIR, _BOOTSTRAP_DIR) for p in sorted(d.glob("*.tf"))}


def _readers(files: dict[str, str]) -> tuple[set[tuple[str, str | None]], set[tuple[str, str | None]]]:
    literal: set[tuple[str, str | None]] = set()
    pattern: set[tuple[str, str | None]] = set()
    for fname, text in files.items():
        for stmt in parse_statements(text):
            kind, resources = stmt["resource"]
            if stmt["effect"] != "Allow" or kind != "literal" or not resources or not covers_value_verb(stmt["actions"]):
                continue
            for res in resources:
                if res.endswith(_KEY_PATH):
                    literal.add((fname, stmt["sid"]))
                elif fnmatch.fnmatchcase(f"arn:aws:ssm:region:account:{_KEY_PATH}", normalise(res)):
                    pattern.add((fname, stmt["sid"]))
    return literal, pattern


def write_problems(files: dict[str, str]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for fname, text in files.items():
        for stmt in parse_statements(text):
            kind, resources = stmt["resource"]
            if stmt["effect"] != "Allow" or kind != "literal" or _WRITE_ALLOWED_IN.get(fname) == stmt["sid"]:
                continue
            covers_key = any(
                fnmatch.fnmatchcase(f"arn:aws:ssm:region:account:{_KEY_PATH}", normalise(r)) for r in (resources or [])
            )
            if covers_key and any(
                fnmatch.fnmatchcase(v, a) or fnmatch.fnmatchcase(v.lower(), a.lower())
                for v in _WRITE_VERBS
                for a in stmt["actions"]
            ):
                out.append((fname, stmt["sid"]))
    return out


def test_trust_is_one_environment_and_workflow_bound_statement() -> None:
    assert trust_problems(_read(_ROLE_FILE)) == []


def test_policy_is_exactly_the_one_key_read() -> None:
    assert policy_problems(_read(_ROLE_FILE)) == []


def test_role_has_one_inline_policy_and_no_attachment() -> None:
    text = "\n".join(_strip_comments(t) for t in _tf_files().values())
    assert len(re.findall(r"role\s*=\s*aws_iam_role\.github_ci_persona_verdict_app\.(?:id|name)\b", text)) == 1
    assert _strip_comments(_read(_ROLE_FILE)).count('resource "aws_iam_role_policy"') == 1
    assert "aws_iam_role_policy_attachment" not in _strip_comments(_read(_ROLE_FILE))


def test_personal_iam_ci_roles_read_lists_the_role() -> None:
    assert _iam_roles_read_resources_in(_read(_OIDC_FILE), "IAMCIRolesRead").count(_ROLE_ARN) == 1


def _iam_roles_read_resources_in(text: str, sid: str) -> list[str]:
    match = re.search(rf'[Ss]id\s*=\s*"{sid}".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, f"{sid} statement not found"
    return re.findall(r'"([^"]+)"', re.sub(r"#[^\n]*", "", match.group(1)))


def test_key_readers_are_exactly_the_declared_ones() -> None:
    literal, pattern = _readers(_tf_files())
    assert literal == _LITERAL_READERS
    assert pattern == _PATTERN_READERS


def test_no_other_grant_writes_or_deletes_the_key() -> None:
    assert write_problems(_tf_files()) == []


def test_red_trust() -> None:
    base = _read(_ROLE_FILE)
    sub = '"repo:${repo}:environment:${local.persona_verdict_app_environment}"'
    assert trust_problems(base.replace(sub, sub + ',\n "repo:${repo}:pull_request"'))
    assert trust_problems(base.replace("StringEquals = {", "StringLike = {", 1))
    assert trust_problems(base.replace("persona-verdict.yml@refs/heads/main", "*@refs/heads/main"))
    assert trust_problems(base.replace('"persona-verdict-app"', '"persona-verdict"', 1))
    assert trust_problems(base.replace(f'"{_BOUNDARY_ARN}"', '"arn:aws:iam::${var.account_id}:policy/other"'))


def test_red_policy() -> None:
    base = _read(_ROLE_FILE)
    extra = (
        '  statement {\n    sid       = "Extra"\n    effect    = "Allow"\n'
        '    actions   = ["s3:GetObject"]\n    resources = ["*"]\n  }\n\n'
    )
    assert policy_problems(base.replace("  statement {\n", extra + "  statement {\n", 1))
    assert policy_problems(base.replace('actions   = ["ssm:GetParameter"]', 'actions   = ["ssm:GetParameter*"]', 1))
    assert policy_problems(base.replace("agent-platform-persona-verdict-app-key", "agent-platform-deepseek-api-key", 1))


def test_red_readers_and_writers() -> None:
    files = _tf_files()
    key = "arn:aws:ssm:${var.aws_region}:${var.account_id}:" + _KEY_PATH
    third = (
        f'statement {{\n  sid = "Third"\n  effect = "Allow"\n  actions = ["ssm:GetParameter"]\n  resources = ["{key}"]\n}}\n'
    )
    assert _readers({**files, "extra.tf": third})[0] != _LITERAL_READERS
    wide = third.replace(key, "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/*").replace(
        '"Third"', '"Wide"'
    )
    assert _readers({**files, "extra.tf": wide})[1] != _PATTERN_READERS
    for actions in ('["ssm:PutParameter"]', '["ssm:Delete*"]', '["ssm:*"]'):
        put = third.replace('["ssm:GetParameter"]', actions)
        assert write_problems({**files, "extra.tf": put}), actions
