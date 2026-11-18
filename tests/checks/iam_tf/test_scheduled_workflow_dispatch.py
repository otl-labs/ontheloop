"""Standing guard for PLAN-scheduled-workflow-dispatch (rec-4233), built in four PRs.

PR 1 pins the bootstrap pre-stage: the apply role's IAMRolesRead names the workflow dispatcher role ahead of its
creation, so the gated apply's iam:GetRole after the create and every later refresh succeed. PR 3 adds the stack's
own cases to TestScheduledWorkflowDispatch (the role, the function, the rules and alarms held equal to the register,
the grants in oidc.tf and oidc_ci_roles.tf, the trusted-plan HCL screen); PR 2 and PR 4 add no cases here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.ci import terraform_hcl_screen
from tests.checks.iam_tf._ci_platform_role_iam import _hcl_statement_blocks, _parse_hcl_statement
from tests.checks.iam_tf._platform_security_hcl import (
    _attr,
    _block_body,
    _resource_body,
    _resources,
    _strip_comments,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READS_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply_reads.tf"
_ROLE_NAME = "agent-platform-workflow-dispatcher"
_ROLE_ARN = f"arn:aws:iam::${{var.account_id}}:role/{_ROLE_NAME}"
_PERSONAL = _REPO_ROOT / "terraform" / "personal"
_STACK_FILE = _PERSONAL / "scheduled_workflow_dispatch.tf"
_REGISTER = _REPO_ROOT / "config" / "lambda" / "scheduled_workflow_dispatcher" / "schedules.yaml"
_FUNCTION_ARN = "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.workflow_dispatcher_function}"
_BOUNDARY = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
_ALARM_PREFIX = "agent-platform-workflow-dispatch"
_MAP_RE = re.compile(r"^\{(.*)\}\[var\.environment\]$")


def _iam_roles_read_resources(text: str, sid: str = "IAMRolesRead") -> list[str]:
    match = re.search(rf'[Ss]id\s*=\s*"{sid}".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, f"{sid} statement not found"
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return re.findall(r'"([^"]+)"', body)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _register() -> dict:
    return yaml.safe_load(_read(_REGISTER))


def _unquote(raw: str | None) -> str | None:
    return None if raw is None else raw.strip().strip('"')


def _data_body(text: str, name: str) -> str | None:
    m = re.search(rf'data\s+"aws_iam_policy_document"\s+"{name}"\s*\{{', _strip_comments(text))
    return None if m is None else _block_body(text, m.end() - 1)


def _map_index(raw: str | None) -> dict[str, str] | None:
    m = _MAP_RE.match(raw or "")
    if m is None:
        return None
    return {k: _unquote(v) or "" for k, v in re.findall(r"(\w+)\s*=\s*(\"[^\"]*\"|\w+)", m.group(1))}


def _admitted_environments() -> set[str]:
    m = re.search(r'variable\s+"environment".*?regex\("\^\(([^)]*)\)\$"', _read(_PERSONAL / "variables.tf"), re.S)
    assert m, "environment validation not found"
    return set(m.group(1).split("|"))


def _github_cron_minute_and_hours(workflow: str) -> tuple[str, int]:
    m = re.search(r"cron:\s*'(\S+) (\S+) ", _read(_REPO_ROOT / ".github" / "workflows" / workflow))
    assert m, workflow
    hours = re.fullmatch(r"\*/(\d+)", m.group(2))
    return m.group(1), int(hours.group(1)) if hours else 1


def _aws_cron_minute_and_hours(cron: str) -> tuple[str, int]:
    m = re.fullmatch(r"cron\((\S+) (\S+) \S+ \S+ \S+ \S+\)", cron)
    assert m, cron
    hours = re.fullmatch(r"\d+/(\d+)", m.group(2))
    return m.group(1), int(hours.group(1)) if hours else 1


def _row_resource(row: str) -> str:
    return row.replace("-", "_")


def _role_problems(tf: str) -> list[str]:
    role = _resource_body(tf, "aws_iam_role", "workflow_dispatcher")
    if role is None:
        return ["role missing"]
    out = []
    if _unquote(_attr(role, "permissions_boundary")) != _BOUNDARY:
        out.append("role boundary")
    if _attr(role, "assume_role_policy") != "data.aws_iam_policy_document.lambda_assume.json":
        out.append("role trust")
    return out


def _policy_problems(tf: str, key_parameter: str) -> list[str]:
    body = _data_body(tf, "workflow_dispatcher")
    if body is None:
        return ["policy document missing"]
    got = {}
    for block in _hcl_statement_blocks(body):
        st = _parse_hcl_statement(block)
        got[st.sid] = (st.effect, sorted(st.actions), sorted(st.resources))
    key_arn = f"arn:aws:ssm:${{var.aws_region}}:${{var.account_id}}:parameter{key_parameter}"
    log_arn = f"arn:aws:logs:${{var.aws_region}}:${{var.account_id}}:log-group:/aws/lambda/{_ROLE_NAME}:*"
    want = {
        "WorkflowDispatcherLogs": ("Allow", ["logs:CreateLogStream", "logs:PutLogEvents"], [log_arn]),
        "WorkflowDispatcherAppKeyRead": ("Allow", ["ssm:GetParameter"], [key_arn]),
    }
    return [] if got == want else [f"policy statements {got}"]


def _function_problems(tf: str) -> list[str]:
    body = _resource_body(tf, "aws_lambda_function", "workflow_dispatcher")
    if body is None:
        return ["function missing"]
    expected = {
        "handler": "src.data.handlers.workflow_dispatcher_handler.handler",
        "s3_key": "lambda-packages/data-pipeline.zip",
        "runtime": "python3.12",
        "reserved_concurrent_executions": "1",
    }
    out = [f"function {k}" for k, v in expected.items() if _unquote(_attr(body, k)) != v]
    stripped = _strip_comments(body)
    if not re.search(r"ignore_changes\s*=\s*\[\s*source_code_hash\s*\]", stripped):
        out.append("function ignore_changes")
    if len(re.findall(r"source_code_hash", stripped)) != 1:
        out.append("function sets source_code_hash")
    return out


def _flag_problems(tf: str) -> list[str]:
    envs = _admitted_environments()
    fn = _resource_body(tf, "aws_lambda_function", "workflow_dispatcher") or ""
    flag = re.search(r"WORKFLOW_DISPATCH_ENABLED\s*=\s*(.+)", _strip_comments(fn))
    exprs = {"flag": (flag.group(1).strip() if flag else None, "false")}
    prod = re.search(r"workflow_dispatch_production\s*=\s*(.+)", _strip_comments(tf))
    exprs["production"] = (prod.group(1).strip() if prod else None, "false")
    for name, rtype, rname in _resources(tf):
        if rtype == "aws_cloudwatch_event_rule" and rname.startswith("workflow_dispatch_"):
            body = _resource_body(tf, rtype, rname) or ""
            exprs[rname] = (_attr(body, "state"), "DISABLED")
    out = []
    for label, (raw, off) in exprs.items():
        mapping = _map_index(raw)
        if mapping is None or set(mapping) != envs or mapping.get("sit") != off:
            out.append(f"environment map {label}: {raw}")
    return out


def _rules_problems(tf: str, schedules: dict) -> list[str]:
    out = []
    kinds = {
        k: [n for _, t, n in _resources(tf) if t == k and n.startswith("workflow_dispatch_")]
        for k in ("aws_cloudwatch_event_rule", "aws_cloudwatch_event_target", "aws_lambda_permission")
    }
    for kind, names in kinds.items():
        if sorted(names) != sorted(f"workflow_dispatch_{_row_resource(r)}" for r in schedules):
            out.append(f"{kind} set {sorted(names)}")
    for row, spec in schedules.items():
        res = f"workflow_dispatch_{_row_resource(row)}"
        rule = _resource_body(tf, "aws_cloudwatch_event_rule", res) or ""
        target = _resource_body(tf, "aws_cloudwatch_event_target", res) or ""
        if _unquote(_attr(rule, "name")) != f"{_ALARM_PREFIX}-{row}":
            out.append(f"rule name {row}")
        if _unquote(_attr(rule, "schedule_expression")) != spec["aws_cron"]:
            out.append(f"rule cron {row}")
        if _attr(target, "input") != f'jsonencode({{ schedule = "{row}" }})':
            out.append(f"target input {row}")
        if _attr(target, "rule") != f"aws_cloudwatch_event_rule.{res}.name":
            out.append(f"target rule {row}")
    return out


def _alarm_problems(tf: str, schedules: dict) -> list[str]:
    out = []
    alarms = [n for _, t, n in _resources(tf) if t == "aws_cloudwatch_metric_alarm" and n.startswith("workflow_dispatch")]
    if sorted(alarms) != sorted(
        ["workflow_dispatcher_errors"] + [f"workflow_dispatch_not_fired_{_row_resource(r)}" for r in schedules]
    ):
        out.append(f"alarm set {sorted(alarms)}")
    for name in alarms:
        body = _resource_body(tf, "aws_cloudwatch_metric_alarm", name) or ""
        if not (_unquote(_attr(body, "alarm_name")) or "").startswith(_ALARM_PREFIX):
            out.append(f"alarm name {name}")
        if _attr(body, "actions_enabled") != "local.workflow_dispatch_production":
            out.append(f"alarm actions_enabled {name}")
        if _attr(body, "alarm_actions") != "[aws_sns_topic.alerts.arn]":
            out.append(f"alarm topic {name}")
        want = "notBreaching" if name == "workflow_dispatcher_errors" else "breaching"
        if _unquote(_attr(body, "treat_missing_data")) != want:
            out.append(f"alarm treat_missing_data {name}")
    for row, spec in schedules.items():
        body = _resource_body(tf, "aws_cloudwatch_metric_alarm", f"workflow_dispatch_not_fired_{_row_resource(row)}") or ""
        if _attr(body, "period") != str(spec["alarm_period_seconds"]):
            out.append(f"alarm period {row}")
        if _attr(body, "evaluation_periods") != str(spec["alarm_evaluation_periods"]):
            out.append(f"alarm evaluation_periods {row}")
        if f"aws_cloudwatch_event_rule.workflow_dispatch_{_row_resource(row)}.name" not in body:
            out.append(f"alarm rule {row}")
    return out


def _stack_problems(tf: str, oidc: str, ci: str, reg: dict) -> list[str]:
    out = _role_problems(tf) + _policy_problems(tf, reg["key_parameter"]) + _function_problems(tf) + _flag_problems(tf)
    out += _rules_problems(tf, reg["schedules"]) + _alarm_problems(tf, reg["schedules"])
    if _iam_roles_read_resources(oidc, "IAMPlatformRolesRead").count(_ROLE_ARN) != 1:
        out.append("oidc.tf IAMPlatformRolesRead")
    if _iam_roles_read_resources(ci, "ProdLambdaInvokeCI").count(_FUNCTION_ARN) != 1:
        out.append("oidc_ci_roles.tf ProdLambdaInvokeCI")
    return out


def _real_inputs() -> tuple[str, str, str, dict]:
    return _read(_STACK_FILE), _read(_PERSONAL / "oidc.tf"), _read(_PERSONAL / "oidc_ci_roles.tf"), _register()


class TestScheduledWorkflowDispatch:
    def test_bootstrap_iam_roles_read_lists_the_dispatcher_role(self) -> None:
        resources = _iam_roles_read_resources(_READS_FILE.read_text(encoding="utf-8"))
        assert resources.count(_ROLE_ARN) == 1

    def test_stack_matches_the_register_and_the_guards(self) -> None:
        assert _stack_problems(*_real_inputs()) == []

    def test_register_key_parameter_is_the_secret_path(self) -> None:
        assert _register()["key_parameter"] == "/secret/agent-platform-workflow-dispatcher-app-key"

    def test_each_register_cron_matches_its_github_cron_and_alarm_covers_the_period(self) -> None:
        for row, spec in _register()["schedules"].items():
            minute, hours = _aws_cron_minute_and_hours(spec["aws_cron"])
            assert (minute, hours) == _github_cron_minute_and_hours(spec["workflow"]), row
            assert spec["alarm_period_seconds"] >= hours * 3600, row

    def test_hcl_screen_passes_on_the_real_root(self) -> None:
        assert terraform_hcl_screen.screen_root(_PERSONAL) == []


class TestScheduledWorkflowDispatchRedCases:
    def test_assertion_reddens_without_the_arn(self) -> None:
        text = _READS_FILE.read_text(encoding="utf-8").replace(_ROLE_ARN, "arn:aws:iam::${var.account_id}:role/other")
        assert _ROLE_ARN not in _iam_roles_read_resources(text)


def _stack_with(old: str, new: str, which: int = 0) -> list[str]:
    inputs = list(_real_inputs())
    assert old in inputs[which], old
    inputs[which] = inputs[which].replace(old, new, 1)
    return _stack_problems(*inputs)


class TestScheduledWorkflowDispatchStackRedCases:
    def test_extra_ssm_action(self) -> None:
        assert _stack_with('actions   = ["ssm:GetParameter"]', 'actions   = ["ssm:GetParameter", "ssm:PutParameter"]')

    def test_wildcard_key_arn(self) -> None:
        assert _stack_with("parameter/secret/agent-platform-workflow-dispatcher-app-key", "parameter/secret/*")

    def test_kms_decrypt_statement(self) -> None:
        stmt = 'statement {\n sid = "Kms"\n actions = ["kms:Decrypt"]\n resources = ["*"]\n }\n'
        assert _stack_with(
            'statement {\n    sid       = "WorkflowDispatcherAppKeyRead"',
            stmt + '  statement {\n    sid       = "WorkflowDispatcherAppKeyRead"',
        )

    def test_sit_rule_enabled(self) -> None:
        assert _stack_with('sit = "DISABLED"', 'sit = "ENABLED"')

    def test_sit_flag_true(self) -> None:
        assert _stack_with('sit = "false" }[var.environment]', 'sit = "true" }[var.environment]')

    def test_alarm_actions_enabled_true(self) -> None:
        assert _stack_with("actions_enabled = local.workflow_dispatch_production", "actions_enabled = true")

    def test_rule_cron_differs_from_register_row(self) -> None:
        assert _stack_with("cron(17 * * * ? *)", "cron(18 * * * ? *)")

    def test_register_row_without_rule(self) -> None:
        inputs = list(_real_inputs())
        reg = inputs[3]
        reg["schedules"]["extra-loop"] = dict(reg["schedules"]["terraform-drift"])
        assert _stack_problems(*inputs)

    def test_function_without_ignore_changes(self) -> None:
        assert _stack_with("ignore_changes = [source_code_hash]", "ignore_changes = [tags]")

    def test_function_with_source_code_hash(self) -> None:
        assert _stack_with("  timeout       = 60\n", '  timeout       = 60\n  source_code_hash = "x"\n')

    def test_missing_platform_roles_read_entry(self) -> None:
        assert _stack_with('role/agent-platform-workflow-dispatcher"', 'role/other"', which=1)

    def test_missing_invoke_grant(self) -> None:
        assert _stack_with('function:${local.workflow_dispatcher_function}",', 'function:other",', which=2)

    def test_for_each_on_a_rule_is_refused_by_the_screen(self) -> None:
        tf = _read(_STACK_FILE).replace(
            'resource "aws_cloudwatch_event_rule" "workflow_dispatch_main_canary" {',
            'resource "aws_cloudwatch_event_rule" "workflow_dispatch_main_canary" {\n  for_each = toset(["a"])',
            1,
        )
        assert terraform_hcl_screen.screen_tree({"scheduled_workflow_dispatch.tf": tf})
