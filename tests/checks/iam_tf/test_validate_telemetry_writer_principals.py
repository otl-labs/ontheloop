"""Mirror tests for validate_telemetry_writer_principals (rec-4420, rec-5177; T3.22 c4)."""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from pathlib import Path

import pytest
import yaml

from scripts.checks.iam_tf import validate_telemetry_writer_principals as mod
from scripts.contracts import load_contract

ROOT = Path(__file__).resolve().parents[3]
ARN = "arn:aws:lambda:${var.aws_region}:${var.account_id}:function"
WRITER = f"{ARN}:agent-platform-ducklake-telemetry-writer"
PERSONAL = "terraform/personal"
BOOTSTRAP = "terraform/bootstrap"
PERSONA = f"{PERSONAL}/oidc_persona_verdict_role.tf"
SESSION = f"{BOOTSTRAP}/agent_session_user.tf"
ADMIN = f"{BOOTSTRAP}/platform_admin_policies.tf"
BRANCH = f"{PERSONAL}/oidc_ci_roles.tf"


@cache
def _real() -> dict[str, str]:
    return {
        p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
        for rel in mod._ROOTS
        for p in sorted((ROOT / rel).glob("*.tf"))
    }


def _classes() -> list[str]:
    return list(load_contract(ROOT / mod.CONTRACT_REL).contract.governance.principal_classes)


def _registry() -> mod._Registry:
    reg, problems = mod.load_registry((ROOT / mod.REGISTRY_REL).read_text(encoding="utf-8"), _classes())
    assert reg is not None and not problems, problems
    return reg


def _findings(tree: dict[str, str]) -> list[str]:
    return mod.analyse(tree, _registry())[0]


def _with(**edits: Callable[[str], str]) -> dict[str, str]:
    tree = dict(_real())
    for rel, fn in edits.items():
        tree[rel] = fn(tree[rel])
    return tree


def _add(rel: str, text: str) -> dict[str, str]:
    tree = dict(_real())
    tree[rel] = tree.get(rel, "") + "\n" + text
    return tree


def _sub(old: str, new: str, count: int = 1) -> Callable[[str], str]:
    def fn(text: str) -> str:
        assert old in text, old
        return text.replace(old, new, count)

    return fn


def _role_policy(sid: str, action: str, resource: str, role: str = "aws_iam_role.other.id", key: str = "x") -> str:
    return (
        f'resource "aws_iam_role_policy" "{key}" {{\n  name = "{key}"\n  role = {role}\n  policy = jsonencode({{\n'
        f'    Statement = [{{\n      Sid = "{sid}"\n      Effect = "Allow"\n      Action = {action}\n'
        f"      Resource = {resource}\n    }}]\n  }})\n}}\n"
    )


def _expect(tree: dict[str, str], needle: str) -> None:
    found = _findings(tree)
    assert any(needle in f for f in found), (needle, found)


def test_every_declared_principal_class_holds_exactly_its_grant() -> None:
    reg = _registry()
    findings, examined = mod.analyse(_real(), reg)
    assert findings == []
    assert examined > 0
    declared = {(c.grant.file, s) for c in reg.classes.values() for s in c.grant.sids}
    declared |= {(e.file, s) for e in reg.exclusions.values() for s in e.sids}
    assert mod.holder_sids(_real()) == declared
    assert list(reg.classes) == _classes()
    failed: list[str] = []
    mod.validate_telemetry_writer_principals(failed, ROOT)
    assert failed == []


UNDECLARED = {
    "role_invoke_function_url": lambda: _add(
        f"{PERSONAL}/zz.tf", _role_policy("X", '"lambda:InvokeFunctionUrl"', f'"{WRITER}"')
    ),
    "role_lambda_star": lambda: _add(f"{PERSONAL}/zz.tf", _role_policy("X", '"lambda:*"', f'"{ARN}:agent-platform-*"')),
    "user_execute_api": lambda: _add(
        f"{BOOTSTRAP}/zz.tf", _role_policy("X", '"execute-api:Invoke"', '"arn:aws:execute-api:*:*:*/*/POST/*"')
    ),
    "invoke_async": lambda: _add(f"{PERSONAL}/zz.tf", _role_policy("X", '"lambda:InvokeAsync"', f'"{WRITER}:1"')),
    "non_literal_resource": lambda: _add(
        f"{PERSONAL}/zz.tf",
        _role_policy("X", '"lambda:InvokeFunctionUrl"', "[aws_lambda_function.ducklake_telemetry_writer.arn]"),
    ),
    "live_alias": lambda: _add(f"{PERSONAL}/zz.tf", _role_policy("X", '"lambda:InvokeFunctionUrl"', f'"{WRITER}:live"')),
    "wildcard_name_alias": lambda: _add(
        f"{PERSONAL}/zz.tf", _role_policy("X", '"lambda:InvokeFunction"', f'"{ARN}:*-telemetry-*:live"')
    ),
    "not_action": lambda: _add(
        f"{PERSONAL}/zz.tf",
        f'data "aws_iam_policy_document" "x" {{\n  statement {{\n    sid = "X"\n    effect = "Allow"\n'
        f'    not_actions = ["s3:*"]\n    resources = ["{WRITER}"]\n  }}\n}}\n',
    ),
}


def _persona_no_condition(text: str) -> str:
    return re.sub(r"\n\s*condition \{.*?\n\s*\}\n", "\n", text, count=1, flags=re.S)


DRIFT = {
    "unconditioned_invoke_function": (_with(**{PERSONA: _persona_no_condition}), "without Bool"),
    "invoke_async_on_declared": (
        _with(
            **{
                PERSONA: _sub(
                    'actions = ["lambda:InvokeFunctionUrl"]', 'actions = ["lambda:InvokeFunctionUrl", "lambda:InvokeAsync"]'
                )
            }
        ),
        "outside its function_url path",
    ),
    "not_action_on_declared": (
        _with(**{PERSONA: _sub('actions = ["lambda:InvokeFunctionUrl"]', 'not_actions = ["s3:*"]')}),
        "NotAction",
    ),
    "sid_moved": (_with(**{PERSONA: _sub('sid     = "TelemetryWriterInvoke"\n', 'sid     = "Moved"\n')}), "appears 0 times"),
    "sid_duplicated": (
        _add(
            PERSONA,
            'data "aws_iam_policy_document" "dup" {\n  statement {\n    sid = "TelemetryWriterInvoke"\n    effect = "Allow"\n'
            '    actions = ["s3:GetObject"]\n    resources = ["*"]\n  }\n}\n',
        ),
        "appears 2 times",
    ),
    "identity_renamed": (
        _with(
            **{
                PERSONA: _sub(
                    'name                 = "agent-platform-github-ci-persona-verdict"', 'name                 = "renamed"'
                )
            }
        ),
        "not named",
    ),
    "rest_route_gains_url": (
        _with(
            **{
                SESSION: _sub(
                    'Action   = "execute-api:Invoke"', 'Action   = ["execute-api:Invoke", "lambda:InvokeFunctionUrl"]'
                )
            }
        ),
        "rest_route class yet holds",
    ),
    "platform_admin_narrowed": (
        _with(
            **{
                ADMIN: lambda t: re.sub(
                    r'(Sid\s*=\s*"LambdaAdminManagement".*?Resource\s*=\s*)"\*"', rf'\1"{WRITER}"', t, count=1, flags=re.S
                )
            }
        ),
        'no longer has Resource "*"',
    ),
    "boundary_inline": (
        _add(
            f"{BOOTSTRAP}/zz.tf",
            'resource "aws_iam_role_policy" "b" {\n  role = aws_iam_role.other.id\n'
            "  policy = aws_iam_policy.github_ci_apply_boundary.arn\n}\n",
        ),
        "permissions boundary",
    ),
    "boundary_literal_arn": (
        _add(
            f"{PERSONAL}/zz.tf",
            'resource "aws_iam_role_policy_attachment" "b" {\n  role = aws_iam_role.other.name\n'
            '  policy_arn = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"\n}\n',
        ),
        "permissions boundary",
    ),
    "aws_managed_attachment": (
        _add(
            f"{PERSONAL}/zz.tf",
            'resource "aws_iam_role_policy_attachment" "m" {\n  role = aws_iam_role.other.name\n'
            '  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"\n}\n',
        ),
        "not an aws_iam_policy declared",
    ),
    "policy_file": (
        _add(
            f"{PERSONAL}/zz.tf",
            'resource "aws_iam_role_policy" "f" {\n  role = aws_iam_role.other.id\n  policy = file("p.json")\n}\n',
        ),
        "not statically readable",
    ),
    "policy_templatefile": (
        _add(
            f"{PERSONAL}/zz.tf",
            'resource "aws_iam_role_policy" "f" {\n  role = aws_iam_role.other.id\n  policy = templatefile("p.json", {})\n}\n',
        ),
        "not statically readable",
    ),
    "policy_heredoc": (
        _add(
            f"{PERSONAL}/zz.tf",
            f'resource "aws_iam_role_policy" "f" {{\n  role = aws_iam_role.other.id\n'
            f'  policy = <<EOT\n{{"Statement":[{{"Effect":"Allow",'
            f'"Action":"lambda:InvokeFunctionUrl","Resource":"{WRITER}"}}]}}\nEOT\n}}\n',
        ),
        "not statically readable",
    ),
    "dynamic_statement": (
        _add(
            f"{PERSONAL}/zz.tf",
            'data "aws_iam_policy_document" "d" {\n  dynamic "statement" {\n'
            '    for_each = var.x\n    content {\n      actions = ["s3:Get*"]\n    }\n  }\n}\n',
        ),
        "dynamically",
    ),
    "branch_doc_on_other_role": (
        _add(
            f"{PERSONAL}/zz.tf",
            'resource "aws_iam_role_policy" "r" {\n  role = aws_iam_role.github_ci_pr.id\n'
            "  policy = data.aws_iam_policy_document.github_ci_branch.json\n}\n",
        ),
        "aws_iam_role.github_ci_pr",
    ),
    "branch_doc_composed": (
        _add(
            f"{PERSONAL}/zz.tf",
            'data "aws_iam_policy_document" "c" {\n'
            "  source_policy_documents = [data.aws_iam_policy_document.github_ci_branch.json]\n}\n"
            'resource "aws_iam_role_policy" "r" {\n  role = aws_iam_role.other.id\n'
            "  policy = data.aws_iam_policy_document.c.json\n}\n",
        ),
        "aws_iam_role.other",
    ),
    "attachment_roles_list": (
        _add(
            f"{PERSONAL}/zz.tf",
            'resource "aws_iam_policy" "p" {\n  policy = data.aws_iam_policy_document.github_ci_branch.json\n}\n'
            'resource "aws_iam_policy_attachment" "a" {\n  name = "a"\n'
            "  roles = [aws_iam_role.other.name]\n  policy_arn = aws_iam_policy.p.arn\n}\n",
        ),
        "aws_iam_role.other",
    ),
    "second_policy_for_session_json": (
        _add(
            f"{BOOTSTRAP}/zz.tf",
            'resource "aws_iam_policy" "dup" {\n  policy = local.agent_session_ducklake_policy_json\n}\n'
            'resource "aws_iam_user_policy_attachment" "dup" {\n'
            "  user = aws_iam_user.other.name\n  policy_arn = aws_iam_policy.dup.arn\n}\n",
        ),
        "aws_iam_user.other",
    ),
    "session_statements_reused": (
        _add(
            f"{BOOTSTRAP}/zz.tf",
            'resource "aws_iam_user_policy" "dup" {\n  user = aws_iam_user.other.name\n'
            "  policy = jsonencode({ Statement = local.agent_session_ducklake_statements })\n}\n",
        ),
        "aws_iam_user.other",
    ),
    "admin_ops_reused": (
        _add(
            f"{BOOTSTRAP}/zz.tf",
            'resource "aws_iam_role_policy" "dup" {\n  role = aws_iam_role.other.id\n'
            "  policy = local.platform_admin_ops_policy_json\n}\n",
        ),
        "exclusion PlatformAdmin",
    ),
    "boundary_json_as_policy": (
        _add(
            f"{BOOTSTRAP}/zz.tf",
            'resource "aws_iam_policy" "dup" {\n  policy = local.github_ci_apply_boundary_policy_json\n}\n'
            'resource "aws_iam_role_policy_attachment" "dup" {\n'
            "  role = aws_iam_role.other.name\n  policy_arn = aws_iam_policy.dup.arn\n}\n",
        ),
        "exclusion ci_apply_boundary",
    ),
    "admin_ops_in_source_documents": (
        _add(
            f"{BOOTSTRAP}/zz.tf",
            'data "aws_iam_policy_document" "dup" {\n  source_policy_documents = [local.platform_admin_ops_policy_json]\n}\n'
            'resource "aws_iam_role_policy" "dup" {\n  role = aws_iam_role.other.id\n'
            "  policy = data.aws_iam_policy_document.dup.json\n}\n",
        ),
        "exclusion PlatformAdmin",
    ),
    "statement_without_effect": (
        _add(
            f"{PERSONAL}/zz.tf",
            f"locals {{\n  s = merge(local.base, {{\n"
            f'    Action = "lambda:InvokeFunctionUrl"\n    Resource = "{WRITER}"\n  }})\n}}\n',
        ),
        "no literal Effect",
    ),
}


CASES = {f"undeclared_{k}": (v, "not declared") for k, v in UNDECLARED.items()} | {f"drift_{k}": v for k, v in DRIFT.items()}


@pytest.mark.parametrize("case", sorted(CASES))
def test_undeclared_and_drifted_grants_fail(case: str) -> None:
    tree, needle = CASES[case]
    _expect(tree() if callable(tree) else tree, needle)


def test_malformed_contract_is_refused() -> None:
    text = (ROOT / mod.REGISTRY_REL).read_text(encoding="utf-8")
    classes = _classes()
    assert mod.load_registry(text, classes)[1] == []
    assert mod.load_registry(None, classes)[0] is None
    bad = {
        "missing class": (text, classes[:-1]),
        "extra class": (text, classes + ["extra"]),
        "duplicated token": (text, classes + [classes[0]]),
        "unknown row key": (text.replace("project_scope_enforcement:", "bogus: 1, project_scope_enforcement:", 1), classes),
        "unknown path": (text.replace("paths: [rest_route]", "paths: [carrier_pigeon]", 1), classes),
        "undefined path": (re.sub(r"\n  rest_route: [^\n]*", "", text, count=1), classes),
        "bad admitted_by": (text.replace("admitted_by: Decision 256", "admitted_by: Decision two", 1), classes),
        "duplicated yaml key": (text + "\nprocedure: again\n", classes),
        "exclusion as class": (
            text.replace("exclusions:", "exclusions:\n  PlatformAdmin: {file: x, sids: [y], reason: z}\n  dup_marker: {}", 1),
            classes,
        ),
    }
    for name, (t, c) in bad.items():
        reg, problems = mod.load_registry(t, c)
        assert reg is None or problems, name


def test_non_holders_stay_green() -> None:
    comment = "# aws_iam_policy.github_ci_apply_boundary is attached elsewhere\n"
    ok = (
        _role_policy("G", '"lambda:Get*"', f'"{WRITER}"', key="g")
        + _role_policy("S", '"lambda:InvokeFunctionUrl"', f'"{WRITER}-smoke"', key="s")
        + comment
        + _role_policy("D", '"iam:*"', "aws_iam_policy.github_ci_apply_boundary.arn", key="d").replace('"Allow"', '"Deny"')
        + _role_policy("C", '"iam:CreateRole"', '"*"', key="c").replace(
            'Resource = "*"',
            'Resource = "*"\n'
            '      Condition = { StringEquals = { "iam:PermissionsBoundary" = '
            '"arn:aws:iam::1:policy/agent-platform-github-ci-apply-boundary" } }',
        )
    )
    assert _findings(_add(f"{PERSONAL}/zz.tf", ok)) == []


def test_registry_matches_real_classes_and_registration() -> None:
    raw = yaml.safe_load((ROOT / mod.REGISTRY_REL).read_text(encoding="utf-8"))
    assert list(raw["classes"]) == _classes()


def test_check_entry_point_fails_on_an_undeclared_holder(tmp_path: Path) -> None:
    for rel in (mod.REGISTRY_REL, mod.CONTRACT_REL):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    bad = tmp_path / PERSONAL / "zz.tf"
    bad.parent.mkdir(parents=True)
    bad.write_text(_role_policy("X", '"lambda:InvokeFunctionUrl"', f'"{WRITER}"'), encoding="utf-8")
    failed: list[str] = []
    mod.validate_telemetry_writer_principals(failed, tmp_path)
    assert failed and any("not declared" in f for f in failed)


def test_check_entry_point_fails_without_a_registry(tmp_path: Path) -> None:
    (tmp_path / mod.CONTRACT_REL).parent.mkdir(parents=True)
    (tmp_path / mod.CONTRACT_REL).write_text((ROOT / mod.CONTRACT_REL).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / PERSONAL).mkdir(parents=True)
    (tmp_path / PERSONAL / "a.tf").write_text("# empty\n", encoding="utf-8")
    failed: list[str] = []
    mod.validate_telemetry_writer_principals(failed, tmp_path)
    assert failed and any("is missing" in f for f in failed)


def test_declared_sid_outside_a_statement_fails_closed() -> None:
    tree = _with(
        **{PERSONA: lambda t: re.sub(r'statement \{([^"]*?sid\s*=\s*"TelemetryWriterInvoke")', r"blk {\1", t, count=1)}
    )
    _expect(tree, "not readable as a policy statement")


def test_exclusion_token_reused_as_class_is_refused() -> None:
    text = (
        (ROOT / mod.REGISTRY_REL)
        .read_text(encoding="utf-8")
        .replace("  PlatformDev: {identity", "  PlatformAdmin: {identity", 1)
    )
    classes = [("PlatformAdmin" if c == "PlatformDev" else c) for c in _classes()]
    assert any("also a class" in p for p in mod.load_registry(text, classes)[1])


def test_inline_policy_block_on_a_role_is_read() -> None:
    bad = 'resource "aws_iam_role" "r" {\n  inline_policy {\n    name = "x"\n    policy = file("p.json")\n  }\n}\n'
    _expect(_add(f"{PERSONAL}/zz.tf", bad), "not statically readable")


def test_unbalanced_file_fails_closed() -> None:
    _expect(_add(f"{PERSONAL}/zz.tf", 'resource "aws_iam_role" "r" {\n'), "unbalanced")


def test_check_skips_without_terraform_roots_and_fails_on_a_broken_contract(tmp_path: Path) -> None:
    failed: list[str] = []
    mod.validate_telemetry_writer_principals(failed, tmp_path)
    assert failed == []
    (tmp_path / PERSONAL).mkdir(parents=True)
    (tmp_path / PERSONAL / "a.tf").write_text("# empty\n", encoding="utf-8")
    mod.validate_telemetry_writer_principals(failed, tmp_path)
    assert failed and any("cannot be loaded" in f for f in failed)


def test_mask_hides_string_interpolation_and_heredoc_contents() -> None:
    text = 'a = "x{${join(",", y)}}"\nb = <<EOT\n{ not a block }\nEOT\nc = {}\n'
    masked = mod._mask(text)
    assert len(masked) == len(text)
    assert masked.count("{") == 1 and masked.count("}") == 1
    assert "not a block" not in masked


def test_blocks_and_attrs_split_nested_blocks_off_the_last_value() -> None:
    text = (
        'resource "aws_iam_role_policy" "p" {\n  role = aws_iam_role.r.id\n  policy = local.x\n\n'
        "  lifecycle {\n    a = 1\n  }\n}\n"
    )
    ((kind, labels, body),) = mod._blocks(text)
    assert (kind, labels) == ("resource", ("aws_iam_role_policy", "p"))
    assert mod._attrs(body) == {"role": "aws_iam_role.r.id", "policy": "local.x"}


def test_prune_drops_lifecycle_and_depends_on() -> None:
    text = "x = 1\ndepends_on = [a.b]\nlifecycle {\n  precondition {\n    condition = local.q\n  }\n}\ny = local.z\n"
    pruned = mod._prune(text)
    assert "local.q" not in pruned and "a.b" not in pruned and "local.z" in pruned


def test_prep_substitutes_only_uniquely_assigned_literal_locals() -> None:
    files = {
        "a.tf": 'locals {\n  fn = "writer"\n  dup = "one"\n}\n# comment ${local.fn}\nx = "${local.fn}-${local.dup}"\n',
        "b.tf": 'locals {\n  dup = "two"\n}\n',
    }
    assert 'x = "writer-${local.dup}"' in mod._prep(files)["a.tf"]


def test_match_rejects_unbalanced_text() -> None:
    with pytest.raises(ValueError):
        mod._match("{ [", 0)


def test_mask_handles_escaped_quotes_and_an_unterminated_string() -> None:
    assert mod._mask('a = "x\\"{y"').count("{") == 0
    assert mod._mask('b = "never closed {').count("{") == 0
