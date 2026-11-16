"""Standing guard for PLAN-persona-verdict-credential (T4.32 c1 credential leg): the persona-verdict role.

PR 1 of 3 pins the bootstrap pre-stage: the apply role's IAMRolesRead names the role ahead of its creation, so the
gated apply's iam:GetRole after the create and every later refresh succeed. PR 2 adds the persona-verdict
Environment's cases (terraform/github) and PR 3 the role's own cases (terraform/personal).
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
_READS_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply_reads.tf"
_ROLE_NAME = "agent-platform-github-ci-persona-verdict"
_ROLE_ARN = f"arn:aws:iam::${{var.account_id}}:role/{_ROLE_NAME}"


def _iam_roles_read_resources(text: str, sid: str = "IAMRolesRead") -> list[str]:
    match = re.search(rf'[Ss]id\s*=\s*"{sid}".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, f"{sid} statement not found"
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return re.findall(r'"([^"]+)"', body)


def test_bootstrap_iam_roles_read_lists_the_persona_verdict_role() -> None:
    resources = _iam_roles_read_resources(_READS_FILE.read_text(encoding="utf-8"))
    assert resources.count(_ROLE_ARN) == 1


def test_bootstrap_assertion_reddens_without_the_arn() -> None:
    text = _READS_FILE.read_text(encoding="utf-8").replace(_ROLE_ARN, "arn:aws:iam::${var.account_id}:role/other")
    assert _ROLE_ARN not in _iam_roles_read_resources(text)


_TF_GITHUB_DIR = _REPO_ROOT / "terraform" / "github"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_ENV = "persona-verdict"
_ENV_WORKFLOW = "persona-verdict.yml"
_SECRET_TYPES = ("github_actions_environment_secret", "github_actions_environment_variable", "github_actions_secret")
_KEY_NAMES = ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")
_REFUSED_TRIGGERS = ("pull_request", "pull_request_target", "workflow_call")


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
    tokens = (_ENV, "persona_verdict", *_KEY_NAMES)
    return [
        f"{rtype}.{name} names {token}"
        for rtype in _SECRET_TYPES
        for name, block in _blocks(text, rtype).items()
        for token in tokens
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


def _triggers(wf: dict[str, Any]) -> set[str]:
    on = wf.get("on")
    if isinstance(on, str):
        return {on}
    if isinstance(on, (list, dict)):
        return {str(x) for x in on}
    return set()


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
            if env is not None and "${{" in env:
                problems.append(f"{fname}:{jname} names an environment by expression")
            if fname != _ENV_WORKFLOW and env == _ENV:
                problems.append(f"{fname}:{jname} names the {_ENV} environment")
            if fname != _ENV_WORKFLOW and _assumes_role(job):
                problems.append(f"{fname}:{jname} assumes the persona-verdict role")
    if _ENV_WORKFLOW in workflows:
        refused = sorted(_triggers(workflows[_ENV_WORKFLOW]) & set(_REFUSED_TRIGGERS))
        if refused:
            problems.append(f"{_ENV_WORKFLOW} has refused triggers {refused}")
    return problems


def _real_workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _load(str(p)) for p in sorted(_WORKFLOWS_DIR.glob("*.yml"))}


def test_persona_verdict_environment_shape() -> None:
    assert environment_problems(_tf_github_text()) == []


def test_no_secret_or_variable_names_persona_verdict_or_a_key() -> None:
    assert secret_problems(_tf_github_text()) == []


def test_workflow_fence() -> None:
    """Holds vacuously for the persona-verdict.yml cases until the verdict-jobs plan creates that file."""
    assert workflow_problems(_real_workflows()) == []


def test_red_environment_shape() -> None:
    base = _tf_github_text()
    env = _blocks(base, "github_repository_environment")["persona_verdict"]
    policy = _blocks(base, "github_repository_environment_deployment_policy")["persona_verdict_main"]
    for broken in (
        env.replace("can_admins_bypass = false", "can_admins_bypass = true"),
        env.replace("  deployment_branch_policy {", "  reviewers {\n    users = [1]\n  }\n\n  deployment_branch_policy {"),
        env.replace("protected_branches     = false", "protected_branches     = true"),
        env.replace("prevent_destroy = true", "prevent_destroy = false"),
    ):
        assert environment_problems(base.replace(env, broken)), broken
    assert environment_problems(base.replace(policy, policy.replace('branch_pattern = "main"', 'branch_pattern = "*"')))
    second = (
        '\nresource "github_repository_environment_deployment_policy" "persona_verdict_extra" {\n'
        "  environment    = github_repository_environment.persona_verdict.environment\n"
        '  branch_pattern = "main"\n}\n'
    )
    assert any("exactly one deployment policy" in p for p in environment_problems(base + second))


def test_red_environment_secret() -> None:
    extra = (
        '\nresource "github_actions_environment_secret" "x" {\n'
        '  environment     = "persona-verdict"\n  secret_name     = "ANTHROPIC_API_KEY"\n}\n'  # pragma: allowlist secret
    )
    assert secret_problems(_tf_github_text() + extra)


def test_red_workflow_fence() -> None:
    job = {"runs-on": "ubuntu-latest", "environment": _ENV, "steps": []}
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": job}}})
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
    for trigger in ("pull_request", "workflow_call"):
        assert workflow_problems({_ENV_WORKFLOW: {"on": {trigger: {}, "workflow_run": {}}, "jobs": {"j": job}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": {"workflow_run": {}}, "jobs": {"j": job}}}) == []


_PERSONAL_DIR = _REPO_ROOT / "terraform" / "personal"
_BOOTSTRAP_DIR = _REPO_ROOT / "terraform" / "bootstrap"
_ROLE_FILE = _PERSONAL_DIR / "oidc_persona_verdict_role.tf"
_OIDC_FILE = _PERSONAL_DIR / "oidc.tf"
_SECRET_PARAMETERS_FILE = _PERSONAL_DIR / "secret_parameters.tf"
_BOUNDARY = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
_JOB_WORKFLOW_REF = "otl-labs/ontheloop/.github/workflows/persona-verdict.yml@refs/heads/main"
_SSM = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter"
_WRITER = "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}"
_KEY_PATHS = ("parameter/secret/agent-platform-deepseek-api-key", "parameter/secret/agent-platform-anthropic-api-key")
_EXPECTED_SIDS = {
    "InferenceKeyParameterRead": (
        ["ssm:GetParameter"],
        [f"{_SSM}/secret/agent-platform-deepseek-api-key", f"{_SSM}/secret/agent-platform-anthropic-api-key"],
    ),
    "TelemetryWriterUrlParameterRead": (["ssm:GetParameter"], [f"{_SSM}/agent-platform/ducklake/telemetry_writer_url"]),
    "TelemetryWriterInvoke": (["lambda:InvokeFunctionUrl"], [_WRITER, f"{_WRITER}:*"]),
    "TelemetryWriterInvokeViaUrlOnly": (["lambda:InvokeFunction"], [_WRITER, f"{_WRITER}:*"]),
}
_FORBIDDEN_POLICY_TEXT = (
    "secretsmanager",
    "s3:",
    "telemetry-writer-smoke",
    "smoke_function",
    "telemetry-reader",
    "telemetry_reader",
    "GetFunctionUrlConfig",
)
_FORBIDDEN_TRUST_TEXT = ("pull_request", "refs/pull", "agent/", "ref:refs/heads")
_LITERAL_READERS = {
    ("platform_dev_policies.tf", "InferenceCredentialsParameterRead"),
    ("oidc_persona_verdict_role.tf", "InferenceKeyParameterRead"),
}
_WILDCARD_READERS = {("github_ci_apply_boundary.tf", "DataPlaneAllow"), ("platform_admin_policies.tf", "SecretParameterAdmin")}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(text: str, kind: str, rtype: str, name: str) -> str:
    m = re.search(rf'^{kind}\s+"{rtype}"\s+"{name}"\s*\{{', _strip_comments(text), re.M)
    assert m, f"{kind} {rtype}.{name} not found"
    return _block_body(_strip_comments(text), m.end() - 1)


def trust_problems(text: str) -> list[str]:
    role = _block(text, "resource", "aws_iam_role", "github_ci_persona_verdict")
    problems: list[str] = []
    if _attr_raw(role, "permissions_boundary") != f'"{_BOUNDARY}"':
        problems.append("permissions_boundary is not the apply boundary")
    env_local = re.search(r'^\s*persona_verdict_environment\s*=\s*"([^"]*)"', _strip_comments(text), re.M)
    if env_local is None or env_local.group(1) != "persona-verdict":
        problems.append('local.persona_verdict_environment is not "persona-verdict"')
    statements = _jsonencode_statement_blocks(role)
    if len(statements) != 1:
        return problems + [f"expected exactly one trust statement, found {len(statements)}"]
    stmt = _strip_comments(statements[0])
    problems += [f"trust names {t}" for t in _FORBIDDEN_TRUST_TEXT if t in stmt]
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
    elif re.findall(r'"([^"]+)"', sub.group(1)) != ["repo:${repo}:environment:${local.persona_verdict_environment}"]:
        problems.append("sub is not the persona-verdict environment")
    if not re.search(rf':job_workflow_ref"\s*=\s*"{re.escape(_JOB_WORKFLOW_REF)}"\s*$', se, re.M):
        problems.append("job_workflow_ref is not the one exact persona-verdict.yml ref")
    return problems


def policy_problems(text: str) -> list[str]:
    doc = _block(text, "data", "aws_iam_policy_document", "github_ci_persona_verdict")
    problems: list[str] = []
    if _attr_raw(doc, "source_policy_documents") is not None:
        problems.append("document sources another document")
    seen: dict[str | None, tuple[list[str], list[str], str]] = {}
    for block in _hcl_statement_blocks(doc):
        stmt_text = _strip_comments(block)
        sid = re.search(r'sid\s*=\s*"([^"]+)"', stmt_text)
        acts = re.search(r"actions\s*=\s*\[([^\]]*)\]", stmt_text)
        ress = re.search(r"resources\s*=\s*\[([^\]]*)\]", stmt_text)
        seen[sid.group(1) if sid else None] = (
            re.findall(r'"([^"]+)"', acts.group(1)) if acts else [],
            re.findall(r'"([^"]+)"', ress.group(1)) if ress else [],
            stmt_text,
        )
    if set(seen) != set(_EXPECTED_SIDS):
        problems.append(f"Sids are {sorted(map(str, seen))}")
    for sid, (actions, resources) in _EXPECTED_SIDS.items():
        if sid in seen and (seen[sid][0], seen[sid][1]) != (actions, resources):
            problems.append(f"{sid} grants {seen[sid][:2]}")
    via = seen.get("TelemetryWriterInvokeViaUrlOnly")
    if via is not None and not (
        re.search(r'test\s*=\s*"Bool"', via[2])
        and re.search(r'variable\s*=\s*"lambda:InvokedViaFunctionUrl"', via[2])
        and re.search(r'values\s*=\s*\["true"\]', via[2])
    ):
        problems.append("TelemetryWriterInvokeViaUrlOnly lacks the Bool lambda:InvokedViaFunctionUrl condition")
    problems += [f"document names {t}" for t in _FORBIDDEN_POLICY_TEXT if t in _strip_comments(doc)]
    return problems


def _key_readers(files: dict[str, str]) -> tuple[set[tuple[str, str | None]], set[tuple[str, str | None]]]:
    literal: set[tuple[str, str | None]] = set()
    wildcard: set[tuple[str, str | None]] = set()
    names = [p.rsplit("/", 1)[1] for p in _KEY_PATHS]
    for fname, text in files.items():
        for stmt in parse_statements(text):
            kind, resources = stmt["resource"]
            if stmt["effect"] != "Allow" or kind != "literal" or not resources or not covers_value_verb(stmt["actions"]):
                continue
            for res in resources:
                if res.endswith(_KEY_PATHS):
                    literal.add((fname, stmt["sid"]))
                elif any(
                    fnmatch.fnmatchcase(f"arn:aws:ssm:region:account:parameter/secret/{n}", normalise(res)) for n in names
                ):
                    wildcard.add((fname, stmt["sid"]))
    return literal, wildcard


def _tf_files() -> dict[str, str]:
    return {p.name: _read(p) for d in (_PERSONAL_DIR, _BOOTSTRAP_DIR) for p in sorted(d.glob("*.tf"))}


def header_problems(text: str) -> list[str]:
    header = "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#"))
    problems = [
        f"header does not name {t}"
        for t in (_ROLE_NAME, "PlatformDev", "PlatformAdmin", "SecretParameterAdmin")
        if t not in header
    ]
    if "nothing reads" in " ".join(header.replace("#", " ").split()):
        problems.append('header still says "nothing reads"')
    return problems


def test_trust_is_one_environment_and_workflow_bound_statement() -> None:
    assert trust_problems(_read(_ROLE_FILE)) == []


def test_policy_is_exactly_the_four_statements() -> None:
    assert policy_problems(_read(_ROLE_FILE)) == []


def test_role_has_one_inline_policy_and_no_attachment() -> None:
    text = "\n".join(_strip_comments(t) for t in _tf_files().values())
    assert len(re.findall(r"role\s*=\s*aws_iam_role\.github_ci_persona_verdict\.(?:id|name)\b", text)) == 1
    assert _strip_comments(_read(_ROLE_FILE)).count('resource "aws_iam_role_policy"') == 1
    assert "aws_iam_role_policy_attachment" not in _strip_comments(_read(_ROLE_FILE))


def test_personal_iam_ci_roles_read_lists_the_role() -> None:
    assert _iam_roles_read_resources(_read(_OIDC_FILE), "IAMCIRolesRead").count(_ROLE_ARN) == 1


def test_inference_key_readers_are_exactly_the_declared_ones() -> None:
    literal, wildcard = _key_readers(_tf_files())
    assert literal == _LITERAL_READERS
    assert wildcard == _WILDCARD_READERS


def test_secret_parameters_header_names_the_readers() -> None:
    assert header_problems(_read(_SECRET_PARAMETERS_FILE)) == []


def test_red_trust() -> None:
    base = _read(_ROLE_FILE)
    sub = '"repo:${repo}:environment:${local.persona_verdict_environment}"'
    assert trust_problems(base.replace(sub, sub + ',\n "repo:${repo}:pull_request"'))
    assert trust_problems(base.replace("StringEquals = {", "StringLike = {", 1))
    assert trust_problems(base.replace("persona-verdict.yml@refs/heads/main", "*@refs/heads/main"))


def test_red_policy() -> None:
    base = _read(_ROLE_FILE)
    extra = (
        '  statement {\n    sid       = "Extra"\n    effect    = "Allow"\n'
        '    actions   = ["s3:GetObject"]\n    resources = ["*"]\n  }\n\n'
    )
    assert any("Sids" in p for p in policy_problems(base.replace("  statement {\n", extra + "  statement {\n", 1)))
    smoke = (
        '"arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_smoke_function}",\n'
    )
    assert policy_problems(base.replace(f'      "{_WRITER}",\n', f'      "{_WRITER}",\n      {smoke}', 1))
    no_condition = re.sub(r"\n    condition \{.*?\n    \}\n", "\n", base, count=1, flags=re.S)
    assert any("Bool" in p for p in policy_problems(no_condition))


def test_red_readers() -> None:
    files = _tf_files()
    third = (
        'statement {\n  sid = "Third"\n  effect = "Allow"\n  actions = ["ssm:GetParameter"]\n'
        '  resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:'
        'parameter/secret/agent-platform-deepseek-api-key"]\n}\n'
    )
    assert _key_readers({**files, "extra.tf": third})[0] != _LITERAL_READERS
    wide = third.replace("agent-platform-deepseek-api-key", "*").replace('"Third"', '"Wide"')
    assert _key_readers({**files, "extra.tf": wide})[1] != _WILDCARD_READERS


def test_red_header() -> None:
    base = _read(_SECRET_PARAMETERS_FILE)
    assert header_problems(base.replace(_ROLE_NAME, "some-other-role"))
    assert header_problems(base.replace("PlatformAdmin", "Someone"))
