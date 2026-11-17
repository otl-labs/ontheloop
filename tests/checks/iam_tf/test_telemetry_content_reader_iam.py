"""rec-4696 acceptance: the telemetry content reader's lock, roles, route and placeholder (Decision 269).

Credit-free text tests over terraform/bootstrap, terraform/github and the committed placeholder zip. The HCL policy
documents are read by a small literal-object parser (comments, strings with ${...} interpolation, lists and objects), so
the boundary's size is measured on the rendered JSON the way AWS measures it (whitespace-stripped, rec-2793). Every
predicate returns a problem list and has a red twin driving the same predicate over mutated real text (Decision 235).
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = _ROOT / "terraform" / "bootstrap"
_ACCOUNT = "".join(["1234", "5678", "9012"])
_REGION = (_ROOT / "config" / "aws-region").read_text(encoding="utf-8").strip()
_PLACEHOLDER = _ROOT / "terraform" / "lambda-placeholders" / "telemetry_content_placeholder.zip"
_PLACEHOLDER_SHA256 = "2a04334f468f2a85f1c683c3544127c070058ccf063fcb64b7a1fe7def167464"  # pragma: allowlist secret
_PLACEHOLDER_MEMBER = "src/lambdas/ducklake_telemetry_content_reader/handler.py"
_FUNCTION = "agent-platform-ducklake-telemetry-content"
_BOUNDARY_PROD_BYTES = 5427
_BOUNDARY_LIMIT = 6144
_PREFIXES = (
    "agent-platform-ducklake-writer*",
    "agent-platform-ducklake-reader*",
    "agent-platform-ducklake-telemetry-*",
)
_CHANGE_ACTIONS = {"lambda:C*", "lambda:D*", "lambda:P*", "lambda:T*", "lambda:U*"}


def _read(name: str) -> str:
    return (_BOOTSTRAP / name).read_text(encoding="utf-8")


class _Hcl:
    """Parse one `name = jsonencode({...})` literal into Python values; unresolved expressions stay strings."""

    def __init__(self, text: str, env: dict[str, str]) -> None:
        self.s, self.i, self.env = text, 0, env

    def _ws(self) -> None:
        s = self.s
        while self.i < len(s):
            if s[self.i] in " \t\r\n,":
                self.i += 1
            elif s[self.i] == "#" or s.startswith("//", self.i):
                while self.i < len(s) and s[self.i] != "\n":
                    self.i += 1
            else:
                return

    def _string(self) -> str:
        s, out = self.s, []
        self.i += 1
        while s[self.i] != '"':
            if s[self.i] == "\\":
                out.append(s[self.i + 1])
                self.i += 2
            elif s.startswith("${", self.i):
                end = s.index("}", self.i)
                out.append(self.env.get(s[self.i + 2 : end].strip(), s[self.i : end + 1]))
                self.i = end + 1
            else:
                out.append(s[self.i])
                self.i += 1
        self.i += 1
        return "".join(out)

    def value(self) -> Any:
        self._ws()
        c = self.s[self.i]
        if c == '"':
            return self._string()
        if c == "[":
            self.i += 1
            items: list[Any] = []
            while self._ws() or self.s[self.i] != "]":
                items.append(self.value())
            self.i += 1
            return items
        if c == "{":
            self.i += 1
            obj: dict[str, Any] = {}
            while self._ws() or self.s[self.i] != "}":
                if self.s[self.i] == '"':
                    key = self._string()
                else:
                    key = re.match(r"[\w.:-]+", self.s[self.i :]).group(0)  # type: ignore[union-attr]
                    self.i += len(key)
                self._ws()
                assert self.s[self.i] == "=", self.s[self.i : self.i + 40]
                self.i += 1
                obj[key] = self.value()
            self.i += 1
            return obj
        m = re.match(r"[^\s,\]\}]+", self.s[self.i :])
        assert m is not None
        self.i += len(m.group(0))
        token = m.group(0)
        return {"true": True, "false": False}.get(token, self.env.get(token, token))


def _local_json(text: str, name: str, env: dict[str, str]) -> dict[str, Any]:
    head = re.search(rf"^\s*{name}\s*=\s*jsonencode\(", text, re.M)
    assert head is not None, name
    parser = _Hcl(text, env)
    parser.i = head.end()
    value = parser.value()
    assert isinstance(value, dict)
    return value


def _env(sit: bool = False) -> dict[str, str]:
    suffix = "-sit" if sit else ""
    return {
        "var.account_id": _ACCOUNT,
        "var.aws_region": _REGION,
        "local.data_lake_bucket_arn": f"arn:aws:s3:::agent-platform-data-lake{suffix}",
        "local.data_lake_bucket_name": f"agent-platform-data-lake{suffix}",
        "local.ducklake_telemetry_content_function": _FUNCTION,
        "local.telemetry_content_data_prefix": "ducklake-telemetry",
        "local.telemetry_content_blob_prefix": "telemetry-blobs/ducklake_telemetry",
    }


def _boundary(text: str | None = None, sit: bool = False) -> dict[str, Any]:
    return _local_json(text or _read("github_ci_apply_boundary.tf"), "github_ci_apply_boundary_policy_json", _env(sit))


def _minified(doc: dict[str, Any]) -> int:
    return len(json.dumps(doc, separators=(",", ":")))


def _statements(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["Sid"]: s for s in doc["Statement"] if "Sid" in s}


def _as_list(value: Any) -> list[str]:
    return value if isinstance(value, list) else [value]


def _function_arns(stmt: dict[str, Any]) -> list[str]:
    arns = _as_list(stmt.get("Resource", []))
    condition = stmt.get("Condition", {}).get("ArnLike", {}).get("lambda:FunctionArn", [])
    return [r for r in arns + _as_list(condition) if "function:" in r]


def boundary_problems(text: str) -> list[str]:
    problems: list[str] = []
    if _minified(_boundary(text)) != _BOUNDARY_PROD_BYTES:
        problems.append(f"boundary: PROD minified is {_minified(_boundary(text))} B, not {_BOUNDARY_PROD_BYTES}")
    if _minified(_boundary(text, sit=True)) >= _BOUNDARY_LIMIT:
        problems.append("boundary: SIT minified reaches the 6,144 B limit")
    sids = _statements(_boundary(text))
    change = sids.get("DenyTelemetryContentChange")
    if change is None:
        return problems + ["boundary: DenyTelemetryContentChange is absent"]
    if change["Effect"] != "Deny" or set(change["Action"]) != _CHANGE_ACTIONS:
        problems.append("change: not a Deny of exactly the five Lambda write families")
    want = f"arn:aws:lambda:{_REGION}:*:function:{_FUNCTION}*"
    if change["Resource"] != want:
        problems.append("change: resource is not the content function and its qualifiers")
    url_only = [s for s in _boundary(text)["Statement"] if any(f"function:{_PREFIXES[0]}" in r for r in _function_arns(s))]
    if len(url_only) != 3:
        problems.append(f"url-only: {len(url_only)} Denies name the writer prefix, expected 3")
    for stmt in url_only:
        arns = _function_arns(stmt)
        if {r.split("function:")[-1] for r in arns} != set(_PREFIXES) or any(r.split(":")[4] != "*" for r in arns):
            problems.append(
                f"url-only: {stmt.get('Sid')} does not name exactly the three prefixes with the account wildcarded"
            )
    fence = sids["DenyTelemetryProdPrefixIO"]["Condition"]["ArnNotLike"]["aws:PrincipalArn"]
    if f"arn:aws:iam::{_ACCOUNT}:role/telemetry-content-reader" not in fence:
        problems.append("fence: role/telemetry-content-reader is not excepted")
    protected = [
        r for s in _boundary(text)["Statement"] for r in _as_list(s.get("Resource", [])) if r.endswith("role/ducklake-api-*")
    ]
    if not protected or "arn:aws:iam::*:role/telemetry-content-*" not in _as_list(
        next(
            s["Resource"]
            for s in _boundary(text)["Statement"]
            if "arn:aws:iam::*:role/ducklake-api-*" in _as_list(s.get("Resource", []))
        )
    ):
        problems.append("protected: role/telemetry-content-* is not in the protected-role Deny")
    return problems


def test_boundary_renders_5427_bytes_prod_and_carries_the_lock() -> None:
    assert boundary_problems(_read("github_ci_apply_boundary.tf")) == []


def test_boundary_sit_rendering_is_under_the_limit() -> None:
    assert _BOUNDARY_PROD_BYTES < _minified(_boundary(sit=True)) < _BOUNDARY_LIMIT


def test_boundary_predicate_is_red_on_each_mutation() -> None:
    text = _read("github_ci_apply_boundary.tf")
    cases = [
        (
            '"lambda:C*", "lambda:D*", "lambda:P*", "lambda:T*", "lambda:U*"',
            '"lambda:C*", "lambda:D*", "lambda:P*", "lambda:T*"',
        ),
        ('function:agent-platform-ducklake-telemetry-content*"', 'function:agent-platform-ducklake-telemetry-contentx"'),
        (
            '"arn:aws:iam::${var.account_id}:role/telemetry-content-reader"',
            '"arn:aws:iam::${var.account_id}:role/telemetry-x"',
        ),
        (', "arn:aws:iam::*:role/telemetry-content-*"', ""),
        ('function:agent-platform-ducklake-writer*"', 'function:agent-platform-ducklake-writer"'),
        ('function:agent-platform-ducklake-reader*"', 'function:agent-platform-ducklake-reader:*"'),
        (
            "arn:aws:lambda:${var.aws_region}:*:function:agent-platform-ducklake-telemetry-*",
            "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-telemetry-*",
        ),
    ]
    for old, new in cases:
        assert old in text, old
        assert boundary_problems(text.replace(old, new, 1)) != [], old


def _inline(name: str, local: str) -> dict[str, Any]:
    return _local_json(_read(name), local, _env())


def reader_role_problems(text: str) -> list[str]:
    problems: list[str] = []
    role = re.search(r'resource "aws_iam_role" "telemetry_content" \{(.*?)\n\}', text, re.S)
    if role is None or 'name                 = "telemetry-content-reader"' not in role.group(1):
        return ["reader: role telemetry-content-reader is absent"]
    if "permissions_boundary = " not in role.group(1) or "agent-platform-github-ci-apply-boundary" not in role.group(1):
        problems.append("reader: the role does not carry the CI apply boundary")
    if "lambda.amazonaws.com" not in role.group(1):
        problems.append("reader: the role is not a Lambda execution role")
    doc = _local_json(text, "telemetry_content_policy_json", _env())
    grants = {s["Sid"]: (sorted(_as_list(s["Action"])), sorted(_as_list(s["Resource"]))) for s in doc["Statement"]}
    bucket = "arn:aws:s3:::agent-platform-data-lake"
    want = {
        "Logs": (
            ["logs:CreateLogStream", "logs:PutLogEvents"],
            [f"arn:aws:logs:{_REGION}:{_ACCOUNT}:log-group:platform-security-telemetry-content-access:*"],
        ),
        "TelemetryCatalogDataRead": (["s3:GetObject"], [f"{bucket}/ducklake-telemetry/*"]),
        "TelemetryCatalogDataList": (["s3:GetBucketLocation", "s3:ListBucket"], [bucket]),
        "TelemetryBlobRead": (["s3:GetObject"], [f"{bucket}/telemetry-blobs/ducklake_telemetry/*"]),
        "TelemetryReaderDsnParameterRead": (
            ["ssm:GetParameter"],
            [f"arn:aws:ssm:{_REGION}:{_ACCOUNT}:parameter/secret/agent-platform-ducklake-telemetry-reader-dsn"],
        ),
    }
    if grants != want:
        problems.append(f"reader: grants differ from the five approved statements: {sorted(set(grants) ^ set(want))}")
    if any(s["Effect"] != "Allow" for s in doc["Statement"]):
        problems.append("reader: a non-Allow statement")
    return problems


def test_reader_role_carries_the_boundary_and_exact_read_grants() -> None:
    assert reader_role_problems(_read("telemetry_content_reader.tf")) == []


def test_reader_role_predicate_is_red_on_each_mutation() -> None:
    text = _read("telemetry_content_reader.tf")
    cases = [
        ('  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"\n', ""),
        ('Action   = ["s3:GetObject"]', 'Action   = ["s3:GetObject", "s3:PutObject"]'),
        ("log-group:platform-security-telemetry-content-access:*", "log-group:*"),
        ("parameter/secret/agent-platform-ducklake-telemetry-reader-dsn", "parameter/secret/*"),
    ]
    for old, new in cases:
        assert old in text, old
        assert reader_role_problems(text.replace(old, new, 1)) != [], old


def deploy_role_problems(text: str) -> list[str]:
    problems: list[str] = []
    role = re.search(r'resource "aws_iam_role" "telemetry_content_deploy" \{(.*?)\n\}', text, re.S)
    if role is None:
        return ["deploy: role telemetry-content-deploy is absent"]
    body = role.group(1)
    if "permissions_boundary" in body:
        problems.append("deploy: the role carries a boundary (it must be boundary-free by explicit exclusion)")
    if body.count("Statement") != 1 or 'Effect = "Allow"' not in body or body.count("sts:AssumeRoleWithWebIdentity") != 1:
        problems.append("deploy: trust is not one web-identity statement")
    if "StringLike" in body or ":ref:" in body or "pull_request" in body:
        problems.append("deploy: trust has a StringLike, ref or pull_request condition")
    for needle in (
        '"token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"',
        ":environment:telemetry-content-deploy",
        "token.actions.githubusercontent.com:job_workflow_ref",
    ):
        if needle not in body:
            problems.append(f"deploy: trust lacks {needle}")
    if re.findall(r'"(otl-labs/ontheloop/[^"]+)"', text) != [
        "otl-labs/ontheloop/.github/workflows/deploy-telemetry-content.yml@refs/heads/main"
    ]:
        problems.append("deploy: the workflow ref is not the deploy workflow at main")
    doc = _local_json(text, "telemetry_content_deploy_policy_json", _env())
    grants = {s["Sid"]: (sorted(_as_list(s["Action"])), sorted(_as_list(s["Resource"]))) for s in doc["Statement"]}
    want = {
        "DeployCode": (
            [
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
            ],
            [f"arn:aws:lambda:{_REGION}:{_ACCOUNT}:function:{_FUNCTION}"],
        ),
        "LayerRead": (
            ["lambda:GetLayerVersion"],
            [
                f"arn:aws:lambda:{_REGION}:{_ACCOUNT}:layer:ducklake-deps:*",
                f"arn:aws:lambda:{_REGION}:{_ACCOUNT}:layer:ducklake-extensions:*",
            ],
        ),
    }
    if grants != want:
        problems.append("deploy: grants are not exactly DeployCode and LayerRead")
    return problems


def test_deploy_role_is_boundary_free_trusts_one_environment_job_and_holds_two_grants() -> None:
    assert deploy_role_problems(_read("telemetry_content_deploy_role.tf")) == []


def test_deploy_role_predicate_is_red_on_each_mutation() -> None:
    text = _read("telemetry_content_deploy_role.tf")
    cases = [
        (
            '  name        = "telemetry-content-deploy"\n',
            '  name        = "telemetry-content-deploy"\n  permissions_boundary = "x"\n',
        ),
        ("StringEquals = {", "StringLike = {"),
        ("@refs/heads/main", "@refs/heads/dev"),
        ('"lambda:UpdateFunctionConfiguration",', '"lambda:UpdateFunctionConfiguration",\n          "lambda:InvokeFunction",'),
        ("sts:AssumeRoleWithWebIdentity", "sts:AssumeRole"),
    ]
    for old, new in cases:
        assert old in text, old
        assert deploy_role_problems(text.replace(old, new, 1)) != [], old


def test_the_content_function_has_no_function_url_and_the_route_is_keyed_telemetry_content() -> None:
    for path in _BOOTSTRAP.glob("*.tf"):
        assert "aws_lambda_function_url" not in re.sub(r"(?m)^\s*#.*$", "", path.read_text(encoding="utf-8")), path.name
    assert '"telemetry/content"' in _read("agent_ducklake_api.tf")


def test_function_is_count_gated_to_sandbox_with_reserved_concurrency_one_and_ignored_code() -> None:
    text = _read("telemetry_content_reader.tf")
    assert "telemetry_content_count             = { sandbox = 1, sit = 0 }[var.environment]" in text
    function = re.search(r'resource "aws_lambda_function" "telemetry_content" \{(.*?)\n\}', text, re.S)
    assert function is not None
    body = function.group(1)
    assert "reserved_concurrent_executions = 1" in body
    assert "ignore_changes = [filename, source_code_hash, layers, handler, environment]" in body
    assert "telemetry_content_placeholder.zip" in body and "function_url" not in body


def test_access_log_group_keeps_a_year_and_survives_a_revert() -> None:
    group = re.search(
        r'resource "aws_cloudwatch_log_group" "telemetry_content" \{(.*?)\n\}', _read("telemetry_content_reader.tf"), re.S
    )
    assert group is not None
    assert "platform-security-telemetry-content-access" in group.group(1)
    assert "retention_in_days = 365" in group.group(1) and "skip_destroy      = true" in group.group(1)


def placeholder_problems(path: Path) -> list[str]:
    problems: list[str] = []
    if hashlib.sha256(path.read_bytes()).hexdigest() != _PLACEHOLDER_SHA256:
        problems.append("placeholder: sha256 differs from the pinned bytes")
    with zipfile.ZipFile(path) as zf:
        if zf.namelist() != [_PLACEHOLDER_MEMBER]:
            problems.append("placeholder: not exactly the handler member")
    return problems


def test_placeholder_zip_is_pinned_and_answers_503_to_every_event(tmp_path: Path) -> None:
    assert placeholder_problems(_PLACEHOLDER) == []
    with zipfile.ZipFile(_PLACEHOLDER) as zf:
        source = zf.read(_PLACEHOLDER_MEMBER).decode("utf-8")
    module_path = tmp_path / "placeholder_handler.py"
    module_path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("placeholder_handler", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for event in ({}, {"body": "{}"}, None):
        response = module.handler(event, None)
        assert response["statusCode"] == 503 and json.loads(response["body"]) == {"error": "content_reader_not_deployed"}


def test_placeholder_predicate_is_red_on_changed_bytes_and_extra_members(tmp_path: Path) -> None:
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(changed, "w") as zf:
        zf.writestr(_PLACEHOLDER_MEMBER, "def handler(event, context=None):\n    return None\n")
        zf.writestr("extra.py", "x = 1\n")
    assert len(placeholder_problems(changed)) == 2


def test_the_placeholder_is_tracked_not_ignored() -> None:
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.zip" in text
    assert text.index("!terraform/lambda-placeholders/telemetry_content_placeholder.zip") > text.index("*.zip")


def test_bootstrap_apply_holds_no_lambda_write_action() -> None:
    doc = _local_json(_read("bootstrap_ci_apply.tf"), "bootstrap_apply_write_json", _env())
    actions = [a for s in doc["Statement"] if s.get("Effect") == "Allow" for a in _as_list(s["Action"])]
    assert not [a for a in actions if a.startswith("lambda:") and not fnmatch.fnmatch(a, "lambda:Get*")]
    guard = _read("bootstrap_ci_apply.tf")
    assert "role/telemetry-content-*" in guard


def test_bootstrap_apply_predicate_is_red_when_a_lambda_write_appears() -> None:
    text = _read("bootstrap_ci_apply.tf")
    mutated = text.replace('Action   = ["s3:PutObject"]', 'Action   = ["s3:PutObject", "lambda:UpdateFunctionCode"]', 1)
    assert mutated != text
    doc = _local_json(mutated, "bootstrap_apply_write_json", _env())
    actions = [a for s in doc["Statement"] if s.get("Effect") == "Allow" for a in _as_list(s["Action"])]
    assert "lambda:UpdateFunctionCode" in actions


def _filter(text: str) -> str:
    match = re.search(r'"platform-security-telemetry-content-change"\s*=\s*"((?:[^"\\]|\\.)*)"', text)
    assert match is not None, "the telemetry-content-change filter is absent"
    return match.group(1).replace('\\"', '"')


def alarm_problems(pattern: str) -> list[str]:
    problems: list[str] = []
    if len(pattern) >= 1024:
        problems.append("alarm: the filter reaches 1,024 characters")
    if '$.eventSource = "iam.amazonaws.com"' not in pattern or "[Tt][Ee][Ll][Ee][Mm][Ee][Tt][Rr][Yy]-[Cc][Oo]" not in pattern:
        problems.append("alarm: the case-insensitive IAM role clause is missing")
    if '$.eventSource = "lambda.amazonaws.com"' not in pattern or pattern.count(f"*{_FUNCTION}*") < 2:
        problems.append("alarm: the Lambda write clause is missing")
    if pattern.count("$.errorMessage") < 2:
        problems.append("alarm: denied calls are not matched on errorMessage in both clauses")
    return problems


def test_content_change_filter_has_both_clauses_and_fits_the_limit() -> None:
    assert alarm_problems(_filter(_read("platform_security_alarms.tf"))) == []
    assert 'resource "aws_cloudwatch_metric_alarm" "platform_security_telemetry_content_change"' in _read(
        "platform_security_alarms.tf"
    )


def test_content_change_filter_predicate_is_red_on_each_mutation() -> None:
    pattern = _filter(_read("platform_security_alarms.tf"))
    cases = [
        ('$.eventSource = "lambda.amazonaws.com"', '$.eventSource = "lambdax.amazonaws.com"'),
        ("[Tt][Ee][Ll][Ee][Mm][Ee][Tt][Rr][Yy]-[Cc][Oo]", "telemetry-co"),
        ("($.errorMessage = %role.", "($.requestParameters.x = %role."),
        ('($.errorMessage = "*agent-platform-ducklake-telemetry-content*")', '($.x = "y")'),
        ('$.eventSource = "iam.amazonaws.com"', '$.eventSource = "iam.amazonaws.com" && ' + '($.x = "' + "y" * 1000 + '")'),
    ]
    for old, new in cases:
        assert old in pattern, old
        assert alarm_problems(pattern.replace(old, new)) != [], old


def test_github_environment_has_a_reviewer_no_admin_bypass_and_one_main_policy() -> None:
    text = (_ROOT / "terraform" / "github" / "environments.tf").read_text(encoding="utf-8")
    env = re.search(r'resource "github_repository_environment" "telemetry_content_deploy" \{(.*?)\n\}', text, re.S)
    policy = re.search(
        r'resource "github_repository_environment_deployment_policy" "telemetry_content_deploy_main" \{(.*?)\n\}', text, re.S
    )
    assert env is not None and policy is not None
    body = env.group(1)
    assert 'environment         = "telemetry-content-deploy"' in body
    assert "users = var.gated_apply_reviewer_user_ids" in body
    assert "can_admins_bypass   = false" in body
    assert "custom_branch_policies = true" in body and "protected_branches     = false" in body
    assert "prevent_destroy = true" in body
    assert 'branch_pattern = "main"' in policy.group(1)
    assert len(re.findall(r'resource "github_repository_environment_deployment_policy" "telemetry_content_deploy', text)) == 1
