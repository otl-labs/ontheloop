"""Red/green tests for validate_github_settings_apply (PLAN-terraform-github-gated-apply, Decision 248)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.ci_guards import _manifest
from scripts.checks.ci_guards import validate_github_settings_apply as v

_ROOT = Path(__file__).resolve().parents[3]
_SETTINGS = v.SETTINGS_TF
_ADMIN = v.ADMIN_TF


@pytest.fixture(scope="module")
def tree() -> tuple[dict[str, str], dict[str, str]]:
    return v._load_tree()


@pytest.fixture
def boot(tree: tuple[dict[str, str], dict[str, str]]) -> dict[str, str]:
    return dict(tree[1])


def _swap(files: dict[str, str], name: str, old: str, new: str) -> dict[str, str]:
    assert old in files[name], old
    files[name] = files[name].replace(old, new, 1)
    return files


def test_real_tree_is_green(tree) -> None:
    assert v.run_all(*tree) == [[], [], []]


def test_registered_on_the_fast_tier() -> None:
    entries = [e for e in _manifest.ENTRIES if e.name == "validate_github_settings_apply"]
    assert len(entries) == 1 and entries[0].pre
    assert "terraform/bootstrap/github_settings_apply.tf" in entries[0].pre_globs


def test_registered_check_passes_on_the_real_tree(capsys) -> None:
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == [] and "PASS" in capsys.readouterr().out


# (a) -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [v.APPLY_ROLE, v.PREVIEW_ROLE, v.APP_SECRET, v.APPLY_SCRIPT])
def test_a_another_workflow_naming_a_fenced_name(tree, name) -> None:
    names = dict(tree[0])
    names[".github/workflows/other.yml"] = f"jobs:\n  j:\n    steps:\n      - run: echo {name}\n"
    assert any(x.startswith("a: .github/workflows/other.yml") for x in v.check_names(names))


def test_a_a_composite_action_naming_the_key(tree) -> None:
    names = dict(tree[0])
    names[".github/actions/x/action.yml"] = f"runs:\n  steps:\n    - run: aws secretsmanager x {v.APP_SECRET}\n"
    assert v.check_names(names) == [f"a: .github/actions/x/action.yml names {v.APP_SECRET}"]


def test_a_the_apply_workflow_itself_may_name_them(tree) -> None:
    names = dict(tree[0])
    names[f".github/workflows/{v.WORKFLOW}"] = f"# {v.APPLY_ROLE} {v.PREVIEW_ROLE} {v.APPLY_SCRIPT}\n"
    assert v.check_names(names) == []
    names[f".github/actions/{v.WORKFLOW}"] = v.APPLY_ROLE
    assert v.check_names(names) != []


# (g) -------------------------------------------------------------------------------------------------------------


def test_g_missing_role(boot) -> None:
    _swap(boot, _SETTINGS, 'resource "aws_iam_role" "github_settings_preview"', 'resource "aws_iam_role" "other"')
    assert "g: aws_iam_role.github_settings_preview is missing" in v.check_trust(boot)


def test_g_second_trust_statement(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        '    Statement = [\n      {\n        Effect = "Allow"\n        Principal = {',
        '    Statement = [\n      { Effect = "Allow", Principal = "*", Action = "sts:AssumeRole" },\n'
        '      {\n        Effect = "Allow"\n        Principal = {',
    )
    assert any(x.startswith("g: github-settings-apply trust") for x in v.check_trust(boot))


def test_g_string_like_condition(boot) -> None:
    _swap(boot, _SETTINGS, "          StringEquals = {", '          StringLike = { "x" = "*" }\n          StringEquals = {')
    assert any(x.startswith("g: github-settings-apply trust") for x in v.check_trust(boot))


def test_g_apply_role_on_the_wrong_environment(boot) -> None:
    _swap(boot, _SETTINGS, 'github_settings_gate_env = "tf-gated-apply"', 'github_settings_gate_env = "tf-trusted-plan"')
    assert any(x.startswith("g: github-settings-apply trust") for x in v.check_trust(boot))


def test_g_preview_role_on_a_ref_subject(boot) -> None:
    _swap(boot, _SETTINGS, '["repo:${repo}:environment:tf-trusted-plan"]', '["repo:${repo}:ref:refs/heads/main"]')
    assert any(x.startswith("g: github-settings-preview trust") for x in v.check_trust(boot))


def test_g_a_second_workflow_ref(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        '"otl-labs/ontheloop/.github/workflows/terraform-github-apply.yml@refs/heads/main",',
        '"otl-labs/ontheloop/.github/workflows/terraform-github-apply.yml@refs/heads/main",\n'
        '    "otl-labs/ontheloop/.github/workflows/terraform-github-apply.yml@refs/heads/x",',
    )
    assert any("github_settings_workflow_refs" in x for x in v.check_trust(boot))


def test_g_a_literal_workflow_ref_bypassing_the_local(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        '"token.actions.githubusercontent.com:job_workflow_ref" = local.github_settings_workflow_refs\n'
        '          }\n        }\n      }\n    ]\n  })\n}\n\nresource "aws_iam_role_policy" "github_settings_apply"',
        '"token.actions.githubusercontent.com:job_workflow_ref" = ["x"]\n'
        '          }\n        }\n      }\n    ]\n  })\n}\n\nresource "aws_iam_role_policy" "github_settings_apply"',
    )
    assert any(x.startswith("g: github-settings-apply trust") for x in v.check_trust(boot))


def test_g_boundary_dropped(boot) -> None:
    _swap(boot, _SETTINGS, "  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn\n", "")
    assert any("boundary" in x for x in v.check_trust(boot))


# (h) -------------------------------------------------------------------------------------------------------------


def test_h_iam_action_on_the_apply_policy(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        'Action   = ["secretsmanager:GetSecretValue"]',
        'Action   = ["secretsmanager:GetSecretValue", "iam:PassRole"]',
    )
    assert any("github_settings_apply must grant" in x and "iam" in x for x in v.check_policies(boot))


def test_h_resource_outside_the_three(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        'Resource = [local.github_settings_state_arn]\n      },\n      {\n        Sid      = "TfstateGithubLock"',
        'Resource = [local.github_settings_state_arn, "arn:aws:s3:::agent-platform-data-lake/tfstate/*"]\n'
        '      },\n      {\n        Sid      = "TfstateGithubLock"',
    )
    assert any("github_settings_apply must grant" in x for x in v.check_policies(boot))


def test_h_a_fourth_apply_statement(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        '        Sid      = "TfstateGithubLock"',
        '        Sid      = "Extra"\n        Effect   = "Allow"\n        Action   = ["s3:ListBucket"]\n'
        '        Resource = ["arn:aws:s3:::agent-platform-data-lake"]\n      },\n'
        '      {\n        Sid      = "TfstateGithubLock"',
    )
    assert any("github_settings_apply must grant" in x for x in v.check_policies(boot))


def test_h_preview_policy_writes_state(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        'Action   = ["s3:GetObject"]\n        Resource = [local.github_settings_state_arn]',
        'Action   = ["s3:GetObject", "s3:PutObject"]\n        Resource = [local.github_settings_state_arn]',
    )
    assert any("github_settings_preview must grant" in x for x in v.check_policies(boot))


def test_h_size_precondition_dropped(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        "length(jsonencode(jsondecode(local.github_settings_preview_policy_json))) <= 10240",
        "length(jsonencode(jsondecode(local.github_settings_preview_policy_json))) <= 6144",
    )
    assert "h: aws_iam_role_policy.github_settings_preview lacks its 10,240 B precondition" in v.check_policies(boot)


def test_h_data_source_names_another_entry(boot) -> None:
    _swap(boot, _SETTINGS, f'  name = "{v.APP_SECRET}"', '  name = "agent-platform-other"')
    assert any(x.startswith("h: data.aws_secretsmanager_secret") for x in v.check_policies(boot))


_KEY = "h: the apply App key policy must be one Deny of all but metadata verbs to all but the four principals"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            '"arn:aws:iam::${var.account_id}:root",',
            '"arn:aws:iam::${var.account_id}:root",\n "arn:aws:iam::${var.account_id}:role/PlatformDev",',
        ),
        ('"arn:aws:iam::${var.account_id}:user/platform-breakglass",\n', ""),
        ('        Effect    = "Deny"', '        Effect    = "Allow"'),
        ("        NotAction = [", "        Action = ["),
        (
            '          "secretsmanager:ListSecretVersionIds",\n',
            '          "secretsmanager:ListSecretVersionIds",\n "secretsmanager:GetSecretValue",\n',
        ),
        ("          ArnNotLike = {", "          StringNotLike = {"),
        ("  block_public_policy = true", "  block_public_policy = false"),
        ("  depends_on = [aws_iam_role_policy.platform_admin_ops]\n", ""),
        ("  secret_arn          = data.aws_secretsmanager_secret.github_apply_app.arn", '  secret_arn          = "x"'),
    ],
)
def test_h_key_policy_shape(boot, old, new) -> None:
    _swap(boot, _SETTINGS, old, new)
    assert _KEY in v.check_policies(boot)


_ADMIN_MSG = (
    "h: AdminOps GithubApplyKeyResourcePolicy must grant exactly the two resource-policy verbs on the entry's exact-suffix ARN"
)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("agent-platform-terraform-github-apply-app-??????", "agent-platform-terraform-github-apply-app-*"),
        ("agent-platform-terraform-github-apply-app-??????", "agent-platform-*"),
        (
            '          "secretsmanager:DeleteResourcePolicy",\n',
            '          "secretsmanager:DeleteResourcePolicy",\n "secretsmanager:GetSecretValue",\n',
        ),
        ('          "secretsmanager:DeleteResourcePolicy",\n', ""),
        ('Sid    = "GithubApplyKeyResourcePolicy"', 'Sid    = "Renamed"'),
    ],
)
def test_h_admin_ops_grant_shape(boot, old, new) -> None:
    _swap(boot, _ADMIN, old, new)
    assert _ADMIN_MSG in v.check_policies(boot)


def test_h_admin_ops_grant_with_a_condition(boot) -> None:
    _swap(
        boot,
        _ADMIN,
        'Sid    = "GithubApplyKeyResourcePolicy"',
        'Sid    = "GithubApplyKeyResourcePolicy"\n        Condition = { Bool = { "aws:SecureTransport" = "true" } }',
    )
    assert _ADMIN_MSG in v.check_policies(boot)


# Parser -----------------------------------------------------------------------------------------------------------


def test_parser_keeps_expressions_and_literals_apart() -> None:
    got = v.parse(
        '{\n  a = "x" # c\n  b = [1, true, local.y]\n  c = flatten([\n    for r in z : ["${r}"]\n  ])\n  d { e = 1 }\n}'
    )
    assert got == {"a": "x", "b": [1, True, "local.y"], "c": 'flatten([for r in z : ["${r}"]])', "d": [{"e": 1}]}
    assert isinstance(got["b"][2], v.Expr) and not isinstance(got["a"], v.Expr)


def test_unparseable_input_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(v, "_load_tree", lambda: ({}, {_SETTINGS: 'locals {\n  a = "', _ADMIN: ""}))
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == ["github-settings-apply: inputs unreadable, so no predicate ran"]


# Pins on the rows this check's plan lands -------------------------------------------------------------------------


def test_ci_rca_taxonomy_row() -> None:
    data = yaml.safe_load((_ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    assert "validate_github_settings_apply" in str(data)


def test_deploy_paths_provisioning_row_names_both_roles() -> None:
    data = yaml.safe_load((_ROOT / "docs" / "contracts" / "deploy-paths.yaml").read_text(encoding="utf-8"))
    row = data["admin_out_of_band"]["github_settings_apply_provisioning"]["description"]
    assert v.APPLY_ROLE in row and v.PREVIEW_ROLE in row and v.WORKFLOW in row


def test_secret_material_domains_name_the_entry_and_its_policy() -> None:
    data = yaml.safe_load((_ROOT / "docs" / "contracts" / "secret-material-handling.yaml").read_text(encoding="utf-8"))
    domains = [d for d in data["patterns"]["domains"] if v.APP_SECRET in d]
    assert len(domains) == 1 and "resource policy" in domains[0] and SETTINGS_PATH in domains[0]


SETTINGS_PATH = "terraform/bootstrap/github_settings_apply.tf"


def test_parser_reads_escapes_labelled_blocks_and_refuses_junk() -> None:
    assert v.parse('{\n  a = "q\\"x"\n  dynamic "d" { e = 1 }\n}') == {"a": 'q\\"x', "dynamic": [{"e": 1}]}
    with pytest.raises(ValueError, match="unexpected"):
        v.parse("{\n  ? = 1\n}")
    assert v._jsonencode(v.Expr("local.missing"), {}) is None


def test_h_a_deny_statement_in_the_apply_policy(boot) -> None:
    _swap(
        boot,
        _SETTINGS,
        '        Sid      = "AppKeyRead"\n        Effect   = "Allow"',
        '        Sid      = "AppKeyRead"\n        Effect   = "Deny"',
    )
    assert any("github_settings_apply must grant" in x and "AppKeyRead" in x for x in v.check_policies(boot))


def test_registered_check_reports_violations(monkeypatch) -> None:
    names, boot = v._load_tree()
    names = {**names, ".github/workflows/other.yml": v.APPLY_ROLE}
    monkeypatch.setattr(v, "_load_tree", lambda: (names, boot))
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == [f"github-settings-apply: a: .github/workflows/other.yml names {v.APPLY_ROLE}"]
