"""CI invoke grants on the telemetry writer, its smoke twin and the telemetry reader (PLAN-telemetry-writer-function,
PLAN-telemetry-reader-and-smoke-twin).

github_ci_branch is the only CI role that may invoke the telemetry writer: InvokeFunctionUrl and GetFunctionUrlConfig in an
unconditioned statement, InvokeFunction only behind Bool lambda:InvokedViaFunctionUrl = true. github_ci_pr, github_ci_planner
and github_ci_deploy get no invoke-class action on it, matched with IAM-wildcard semantics (a prefix wildcard such as
function:agent-platform-ducklake-* counts). Expected non-invoke coverage is not flagged: the planner's LambdaRead (Get*/List*
on function:agent-platform-*, for refresh) and github_ci_deploy's UpdateFunctionCode on the same prefix, which the companion
deploy-wiring plan's governed deploy needs. Every predicate is a pure function of HCL text with its own red case on
synthetic HCL.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase

import pytest

from tests.fixtures.terraform_hcl_blocks import _TERRAFORM_PERSONAL_DIR, tf_dir_file_texts

_FN = "agent-platform-ducklake-telemetry-writer"
_TARGETS = (
    "agent-platform-ducklake-telemetry-writer",
    "agent-platform-ducklake-telemetry-writer-smoke",
    "agent-platform-ducklake-telemetry-reader",
)


def _forms(fn: str) -> tuple[str, str]:
    arn = f"arn:aws:lambda:eu-west-2:111:function:{fn}"
    return arn, f"{arn}:*"


_FORMS = _forms(_FN)
_INVOKE_PROBES = ("lambda:invokefunction", "lambda:invokefunctionurl", "lambda:invokeasync")
_VIA_URL = ("Bool", "lambda:InvokedViaFunctionUrl", ["true"])
_ALLOWED = "github_ci_branch"
_OTHERS = ("github_ci_pr", "github_ci_planner", "github_ci_deploy")


def _balanced(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        depth += text[i] == "{"
        depth -= text[i] == "}"
        if depth == 0:
            return text[open_idx + 1 : i]
    raise AssertionError("unbalanced braces")


def _strip_comments(text: str) -> str:
    return re.sub(r"(?m)^\s*#.*$", "", text)


def _locals(texts: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for text in texts.values():
        out.update(re.findall(r'^\s*(\w+)\s*=\s*"([^"$]*)"\s*$', _strip_comments(text), re.MULTILINE))
    return out


def _render(token: str, locs: dict[str, str]) -> str:
    token = token.strip().strip('"')
    token = re.sub(r"\$\{aws_lambda_function\.(\w+)\.arn\}", lambda m: _fn_arn(m.group(1)), token)
    token = re.sub(r"^aws_lambda_function\.(\w+)\.arn$", lambda m: _fn_arn(m.group(1)), token)
    token = token.replace("${var.aws_region}", "eu-west-2").replace("${var.account_id}", "111")
    token = re.sub(r"\$\{local\.(\w+)\}", lambda m: locs.get(m.group(1), "UNRESOLVED"), token)
    return re.sub(r"\$\{[^}]*\}", "UNRESOLVED", token)


def _fn_arn(ref: str) -> str:
    return f"arn:aws:lambda:eu-west-2:111:function:agent-platform-{ref.replace('_', '-')}"


def _list(body: str, key: str) -> list[str]:
    m = re.search(rf"\b{key}\s*=\s*\[(.*?)\]", body, re.DOTALL)
    return re.findall(r'"[^"]*"|[\w.]+(?:\$\{[^}]*\})?[\w.:*]*', m.group(1)) if m else []


def _statements(block: str, locs: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for head in re.finditer(r"\bstatement\s*\{", block):
        body = _balanced(block, head.end() - 1)
        conds = []
        for c in re.finditer(r"\bcondition\s*\{", body):
            cbody = _balanced(body, c.end() - 1)
            test = re.search(r'test\s*=\s*"(\w+)"', cbody)
            var = re.search(r'variable\s*=\s*"([^"]+)"', cbody)
            values = [v.strip('"') for v in _list(cbody, "values")]
            conds.append((test.group(1) if test else "", var.group(1) if var else "", values))
        top = re.sub(r"condition\s*\{[^{}]*\}", "", body)
        effect = re.search(r'\beffect\s*=\s*"(\w+)"', top)
        out.append(
            {
                "effect": effect.group(1) if effect else "Allow",
                "actions": [a.strip('"').lower() for a in _list(top, "actions")],
                "not_actions": bool(re.search(r"\bnot_actions\s*=", top)),
                "resources": [_render(r, locs) for r in _list(top, "resources")],
                "conditions": conds,
            }
        )
    return out


def _document(texts: dict[str, str], name: str, locs: dict[str, str], seen: frozenset[str] = frozenset()) -> list[dict]:
    pattern = re.compile(rf'data\s+"aws_iam_policy_document"\s+"{name}"\s*\{{')
    for text in texts.values():
        m = pattern.search(text)
        if m is None:
            continue
        block = _balanced(text, m.end() - 1)
        stmts = _statements(block, locs)
        src = re.search(r"source_policy_documents\s*=\s*\[(.*?)\]", block, re.DOTALL)
        for ref in re.findall(r"data\.aws_iam_policy_document\.(\w+)\.json", src.group(1) if src else ""):
            if ref not in seen:
                stmts += _document(texts, ref, locs, seen | {name})
        return stmts
    raise AssertionError(f"policy document {name!r} not found")


def _hits(stmt: dict, forms: tuple[str, str] = _FORMS) -> bool:
    return any(fnmatchcase(form, r) for form in forms for r in stmt["resources"])


def _covers(stmt: dict, probe: str) -> bool:
    if stmt["not_actions"]:
        return True
    return any(fnmatchcase(probe, a) for a in stmt["actions"])


def _covers_both_forms(stmt: dict, forms: tuple[str, str] = _FORMS) -> bool:
    return all(any(fnmatchcase(form, r) for r in stmt["resources"]) for form in forms)


def branch_problems(texts: dict[str, str], fn: str = _FN) -> list[str]:
    locs = _locals(texts)
    forms = _forms(fn)
    stmts = [s for s in _document(texts, _ALLOWED, locs) if s["effect"] == "Allow" and _hits(s, forms)]
    problems: list[str] = []
    for verb in ("lambda:invokefunctionurl", "lambda:getfunctionurlconfig"):
        ok = any(_covers_both_forms(s, forms) and _covers(s, verb) and not s["conditions"] for s in stmts)
        if not ok:
            problems.append(f"{_ALLOWED} lacks an unconditioned {verb} grant on both ARN forms")
    if not any(
        _covers_both_forms(s, forms) and _covers(s, "lambda:invokefunction") and _VIA_URL in s["conditions"] for s in stmts
    ):
        problems.append(f"{_ALLOWED} lacks a URL-only lambda:invokefunction grant on both ARN forms")
    for s in stmts:
        if _covers(s, "lambda:invokefunction") and _VIA_URL not in s["conditions"]:
            problems.append(f"{_ALLOWED} grants lambda:invokefunction without Bool lambda:InvokedViaFunctionUrl = true")
    return problems


def other_role_problems(texts: dict[str, str], fn: str = _FN) -> list[str]:
    locs = _locals(texts)
    forms = _forms(fn)
    problems: list[str] = []
    for role in _OTHERS:
        for s in _document(texts, role, locs):
            if s["effect"] == "Allow" and _hits(s, forms) and any(_covers(s, p) for p in _INVOKE_PROBES):
                problems.append(f"{role} holds an invoke-class action on the telemetry writer")
    return problems


def _real() -> dict[str, str]:
    return tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)


@pytest.mark.parametrize("fn", _TARGETS)
def test_branch_role_invokes_the_telemetry_functions_url_only(fn: str) -> None:
    assert branch_problems(_real(), fn) == []


@pytest.mark.parametrize("fn", _TARGETS)
def test_no_other_ci_role_can_invoke_the_telemetry_functions(fn: str) -> None:
    assert other_role_problems(_real(), fn) == []


@pytest.mark.parametrize("fn", _TARGETS[1:])
def test_branch_predicate_red_when_a_function_has_no_grant(fn: str) -> None:
    assert branch_problems(_tree(_URL_STMTS + _VIA_STMT), fn) != []


_ARN_REF = "aws_lambda_function.ducklake_telemetry_writer.arn"
_URL_STMTS = f"""
  statement {{
    effect  = "Allow"
    actions = ["lambda:InvokeFunctionUrl", "lambda:GetFunctionUrlConfig"]
    resources = [{_ARN_REF}, "${{{_ARN_REF}}}:*"]
  }}
"""
_VIA_STMT = f"""
  statement {{
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [{_ARN_REF}, "${{{_ARN_REF}}}:*"]
    condition {{
      test     = "Bool"
      variable = "lambda:InvokedViaFunctionUrl"
      values   = ["true"]
    }}
  }}
"""


def _doc(name: str, body: str, sources: str = "") -> str:
    src = f"  source_policy_documents = [{sources}]\n" if sources else ""
    return f'data "aws_iam_policy_document" "{name}" {{\n{src}{body}\n}}\n'


def _tree(branch: str, **others: str) -> dict[str, str]:
    docs = {role: _doc(role, others.get(role, "")) for role in _OTHERS}
    return {"a.tf": _doc(_ALLOWED, branch), "b.tf": "".join(docs.values())}


def test_branch_predicate_green_on_a_complete_synthetic_branch() -> None:
    assert branch_problems(_tree(_URL_STMTS + _VIA_STMT)) == []


_OTHER_COND = '\n    condition {\n      test = "Bool"\n      variable = "aws:SecureTransport"\n      values = ["true"]\n    }'


@pytest.mark.parametrize(
    "branch",
    [
        "",
        _URL_STMTS,
        _VIA_STMT,
        _URL_STMTS + _VIA_STMT.replace('"lambda:InvokedViaFunctionUrl"', '"lambda:Other"'),
        _URL_STMTS + _VIA_STMT.replace('"true"', '"false"'),
        _URL_STMTS.replace(f"[{_ARN_REF}, ", "[") + _VIA_STMT,
        _URL_STMTS.replace('    effect  = "Allow"', '    effect  = "Allow"' + _OTHER_COND) + _VIA_STMT,
    ],
)
def test_branch_predicate_red_on_each_mutation(branch: str) -> None:
    assert branch_problems(_tree(branch)) != []


def test_branch_predicate_red_on_an_unconditioned_invoke_function() -> None:
    unconditioned = re.sub(r"\s*condition \{.*?\n    \}", "", _VIA_STMT, flags=re.DOTALL)
    problems = branch_problems(_tree(_URL_STMTS + unconditioned))
    assert any("without Bool" in p for p in problems)


@pytest.mark.parametrize(
    "resource",
    [
        f"[{_ARN_REF}]",
        f'["${{{_ARN_REF}}}:*"]',
        '["arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-*"]',
        '["arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*"]',
        '["*"]',
    ],
)
@pytest.mark.parametrize(
    "action",
    ['"lambda:InvokeFunction"', '"lambda:InvokeFunctionUrl"', '"lambda:Invoke*"', '"lambda:*"', '"lambda:InvokeAsync"'],
)
def test_other_roles_predicate_red_on_a_wildcard_or_direct_invoke(resource: str, action: str) -> None:
    body = f'statement {{\n effect = "Allow"\n actions = [{action}]\n resources = {resource}\n}}'
    assert other_role_problems(_tree(_URL_STMTS + _VIA_STMT, github_ci_pr=body)) != []


def test_other_roles_predicate_follows_source_policy_documents() -> None:
    texts = _tree(_URL_STMTS + _VIA_STMT)
    wild = 'statement {\n actions = ["lambda:InvokeFunction"]\n resources = ["*"]\n}'
    texts["c.tf"] = _doc("shared", wild)
    texts["b.tf"] = texts["b.tf"].replace(
        'data "aws_iam_policy_document" "github_ci_deploy" {',
        'data "aws_iam_policy_document" "github_ci_deploy" {\n  source_policy_documents = [data.aws_iam_policy_document.shared.json]',  # noqa: E501
    )
    assert other_role_problems(texts) == ["github_ci_deploy holds an invoke-class action on the telemetry writer"]


def test_other_roles_predicate_green_on_the_expected_non_invoke_coverage() -> None:
    read = (
        'statement {\n actions = ["lambda:Get*", "lambda:List*", "lambda:UpdateFunctionCode"]\n'
        ' resources = ["arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*"]\n}'
    )
    assert other_role_problems(_tree(_URL_STMTS + _VIA_STMT, github_ci_planner=read, github_ci_deploy=read)) == []
