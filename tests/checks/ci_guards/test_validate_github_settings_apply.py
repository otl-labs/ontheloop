"""Red/green tests for validate_github_settings_apply (PLAN-terraform-github-gated-apply, Decision 248)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.ci_guards import _manifest
from scripts.checks.ci_guards import validate_github_settings_apply as v

_ROOT = Path(__file__).resolve().parents[3]
_SETTINGS = v.SETTINGS_TF
_ADMIN = v.ADMIN_TF


Tree = tuple[dict[str, str], dict[str, str], dict[str, Any] | None, str, dict[str, str], str]


@pytest.fixture(scope="module")
def tree() -> Tree:
    return v._load_tree()


@pytest.fixture
def boot(tree: Tree) -> dict[str, str]:
    return dict(tree[1])


@pytest.fixture
def wf(tree: Tree) -> dict[str, Any]:
    assert tree[2] is not None
    return copy.deepcopy(tree[2])


@pytest.fixture
def gh(tree: Tree) -> dict[str, str]:
    return dict(tree[4])


def _swap(files: dict[str, str], name: str, old: str, new: str) -> dict[str, str]:
    assert old in files[name], old
    files[name] = files[name].replace(old, new, 1)
    return files


def test_real_tree_is_green(tree) -> None:
    names, bootstrap = tree[0], tree[1]
    assert v.check_names(names) == []
    assert v.check_trust(bootstrap) == []
    assert v.check_policies(bootstrap) == []


def test_run_all_runs_every_group(monkeypatch) -> None:
    groups = ["check_names", "check_trust", "check_policies", "check_workflow", "check_codeowners", "check_github_root"]
    for name in groups:
        monkeypatch.setattr(v, name, lambda *args, _n=name: [_n])
    assert v.run_all({}, {}, None, "", {}, "") == [[name] for name in groups]


def test_real_tree_meets_the_ci_apply_predicates(tree) -> None:
    _, _, workflow, codeowners, github_tf, gate_text = tree
    assert v.check_workflow(workflow) == []
    assert v.check_codeowners(codeowners) == []
    assert v.check_github_root(github_tf, gate_text) == []
    assert len(v.run_all(*tree)) == 6


def test_registered_on_the_fast_tier() -> None:
    entries = [e for e in _manifest.ENTRIES if e.name == "validate_github_settings_apply"]
    assert len(entries) == 1 and entries[0].pre
    assert "terraform/bootstrap/github_settings_apply.tf" in (entries[0].pre_globs or ())


def test_registered_check_passes_on_the_real_tree(capsys) -> None:
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == [] and "PASS" in capsys.readouterr().out


# (a) -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [v.APPLY_ROLE, v.PREVIEW_ROLE, v.APP_ENTRY, v.APPLY_SCRIPT])
def test_a_another_workflow_naming_a_fenced_name(tree, name) -> None:
    names = dict(tree[0])
    names[".github/workflows/other.yml"] = f"jobs:\n  j:\n    steps:\n      - run: echo {name}\n"
    assert any(x.startswith("a: .github/workflows/other.yml") for x in v.check_names(names))


def test_a_a_composite_action_naming_the_key(tree) -> None:
    names = dict(tree[0])
    names[".github/actions/x/action.yml"] = f"runs:\n  steps:\n    - run: aws secretsmanager x {v.APP_ENTRY}\n"
    assert v.check_names(names) == [f"a: .github/actions/x/action.yml names {v.APP_ENTRY}"]


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
    _swap(boot, _SETTINGS, f'  name = "{v.APP_ENTRY}"', '  name = "agent-platform-other"')
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
    monkeypatch.setattr(v, "_load_tree", lambda: ({}, {_SETTINGS: 'locals {\n  a = "', _ADMIN: ""}, None, "", {}, ""))
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == ["github-settings-apply: inputs unreadable, so no predicate ran"]


# Pins on the rows this check's plan lands -------------------------------------------------------------------------


def test_ci_rca_taxonomy_row() -> None:
    data = yaml.safe_load((_ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    assert "validate_github_settings_apply" in str(data)


def test_environment_taxonomy_apply_gating_names_the_ci_apply() -> None:
    data = yaml.safe_load((_ROOT / "docs" / "contracts" / "environment-taxonomy.yaml").read_text(encoding="utf-8"))
    gating = str(data["axis_a"]["github"]["apply_gating"])
    assert v.WORKFLOW in gating and "terraform-github-plan.yml" in gating and "NEVER auto-apply" in gating


def test_ci_rca_taxonomy_workflow_row() -> None:
    data = yaml.safe_load((_ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    assert data["workflows"]["terraform-github-apply"]["tier"] == "not_a_gate"
    assert data["workflows"]["terraform-github-apply"]["ci_rca"] == "excluded"


def test_deploy_paths_provisioning_row_names_both_roles() -> None:
    data = yaml.safe_load((_ROOT / "docs" / "contracts" / "deploy-paths.yaml").read_text(encoding="utf-8"))
    row = data["admin_out_of_band"]["github_settings_apply_provisioning"]["description"]
    assert v.APPLY_ROLE in row and v.PREVIEW_ROLE in row and v.WORKFLOW in row


def test_secret_material_domains_name_the_entry_and_its_policy() -> None:
    data = yaml.safe_load((_ROOT / "docs" / "contracts" / "secret-material-handling.yaml").read_text(encoding="utf-8"))
    domains = [d for d in data["patterns"]["domains"] if v.APP_ENTRY in d]
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
    names, *rest = v._load_tree()
    names = {**names, ".github/workflows/other.yml": v.APPLY_ROLE}
    monkeypatch.setattr(v, "_load_tree", lambda: (names, *rest))
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == [f"github-settings-apply: a: .github/workflows/other.yml names {v.APPLY_ROLE}"]


def test_a_bad_gate_file_fails_closed(monkeypatch) -> None:
    rest = v._load_tree()
    monkeypatch.setattr(v, "_load_tree", lambda: (*rest[:5], "GATE_TYPES = (\n"))
    failed: list[str] = []
    v.validate_github_settings_apply(failed)
    assert failed == ["github-settings-apply: inputs unreadable, so no predicate ran"]


# (b)-(f), (j) ------------------------------------------------------------------------------------------------------


def _step(wf: dict[str, Any], job: str, key: str, contains: str) -> dict[str, Any]:
    return next(s for s in wf["jobs"][job]["steps"] if contains in str(s.get(key, "")))


def test_b_missing_workflow() -> None:
    assert v.check_workflow(None) == [f"b: {v.WORKFLOW} is missing"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda on: on.update(pull_request={}),
        lambda on: on.pop("workflow_dispatch"),
        lambda on: on["push"].update(branches=["main", "dev"]),
        lambda on: on["push"].update(tags=["v*"]),
    ],
    ids=["pull-request", "no-dispatch", "second-branch", "push-tags"],
)
def test_b_triggers(wf, mutate) -> None:
    mutate(wf["on"])
    assert "b: on: must be exactly push (branches [main]) and workflow_dispatch" in v.check_workflow(wf)


def test_c_apply_environment_not_the_gate(wf) -> None:
    wf["jobs"]["apply"]["environment"] = "tf-trusted-plan"
    assert f"c: the apply job must declare environment {v.GATE_ENV} as a literal" in v.check_workflow(wf)


def test_c_apply_environment_as_a_mapping(wf) -> None:
    wf["jobs"]["apply"]["environment"] = {"name": v.GATE_ENV}
    assert f"c: the apply job must declare environment {v.GATE_ENV} as a literal" in v.check_workflow(wf)


def test_c_preview_assuming_the_apply_role(wf) -> None:
    step = _step(wf, "preview", "with", "role-to-assume")
    step["with"]["role-to-assume"] = step["with"]["role-to-assume"].replace(v.PREVIEW_ROLE, v.APPLY_ROLE)
    got = v.check_workflow(wf)
    assert f"c: only the apply job may assume {v.APPLY_ROLE}, and it assumes nothing else" in got
    assert f"c: the preview job must declare {v.PREVIEW_ENV} and assume only {v.PREVIEW_ROLE}" in got


def test_c_a_third_job_assuming_the_apply_role(wf) -> None:
    wf["jobs"]["extra"] = copy.deepcopy(wf["jobs"]["apply"])
    assert f"c: only the apply job may assume {v.APPLY_ROLE}, and it assumes nothing else" in v.check_workflow(wf)


def test_c_preview_on_another_environment(wf) -> None:
    wf["jobs"]["preview"]["environment"] = v.GATE_ENV
    assert f"c: the preview job must declare {v.PREVIEW_ENV} and assume only {v.PREVIEW_ROLE}" in v.check_workflow(wf)


def test_c_apply_reading_the_plan_app_key(wf) -> None:
    wf["jobs"]["apply"]["steps"].append({"run": "true", "env": {"K": "${{ " + v.PLAN_APP_SIGNER_REF + " }}"}})
    assert "c: only the preview job may read the plan App key" in v.check_workflow(wf)


@pytest.mark.parametrize("job", ["preview", "apply"])
def test_d_if_without_main(wf, job) -> None:
    wf["jobs"][job]["if"] = "${{ github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch' }}"
    assert f"d: the {job} job's if must require refs/heads/main in every disjunct" in v.check_workflow(wf)


def test_d_apply_not_needing_preview(wf) -> None:
    del wf["jobs"]["apply"]["needs"]
    assert "d: the apply job must need preview" in v.check_workflow(wf)


@pytest.mark.parametrize(
    "with_",
    [
        {"persist-credentials": True},
        {"persist-credentials": False, "ref": "x"},
        {"persist-credentials": False, "repository": "a/b"},
    ],
    ids=["persisted", "ref", "repository"],
)
def test_e_checkout(wf, with_) -> None:
    _step(wf, "apply", "uses", "actions/checkout@")["with"] = with_
    assert "e: apply checkout must take no ref or repository and set persist-credentials false" in v.check_workflow(wf)


def test_f_unpinned_action(wf) -> None:
    _step(wf, "preview", "uses", "setup-python")["uses"] = "actions/setup-python@v7"
    assert "f: preview uses actions/setup-python@v7 without a 40-hex commit SHA pin" in v.check_workflow(wf)


@pytest.mark.parametrize(
    "expr",
    [
        "${{ inputs.gate_change }}",
        "${{ needs.preview.outputs.reviewed }}",
        "${{ inputs['gate_change'] }}",
        "${{ toJSON(inputs) }}",
        "${{ github.event.head_commit.message }}",
    ],
)
def test_j_expression_in_a_run_body(wf, expr) -> None:
    wf["jobs"]["apply"]["steps"].append({"run": f"echo {expr}"})
    assert "j: apply interpolates an expression in a run body" in v.check_workflow(wf)


def test_c_a_third_job(wf) -> None:
    wf["jobs"]["extra"] = {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}
    assert "c: the workflow's jobs must be exactly preview and apply" in v.check_workflow(wf)


@pytest.mark.parametrize(
    "expr", ["${{ secrets['TF_GITHUB_PLAN_APP_PRIVATE_KEY'] }}", "${{ toJSON(secrets) }}", "${{ tojson( secrets ) }}"]
)
def test_c_reading_secrets_whole(wf, expr) -> None:
    wf["jobs"]["apply"]["steps"].append({"run": "true", "env": {"K": expr}})
    assert "c: only the preview job may read the plan App key" in v.check_workflow(wf)


# (i) --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", v.OWNED)
def test_i_codeowners_line_missing(tree, path) -> None:
    assert v.check_codeowners(tree[3]) == []
    codeowners = "\n".join(f"/{p} @owner" for p in v.OWNED if p != path)
    assert v.check_codeowners(codeowners) == [f"i: {path} is not code-owned"]


# (k), (l) ---------------------------------------------------------------------------------------------------------

_ENV_FILE = "environments.tf"


def _in_block(gh: dict[str, str], header: str, old: str, new: str) -> dict[str, str]:
    text = gh[_ENV_FILE]
    start = text.index(header)
    end = text.index("\n}\n", start)
    assert old in text[start:end], old
    gh[_ENV_FILE] = text[:start] + text[start:end].replace(old, new) + text[end:]
    return gh


_ENV_HEADER = 'resource "github_repository_environment" "tf_github_apply"'
_POLICY_HEADER = 'resource "github_repository_environment_deployment_policy" "tf_github_apply_main"'


@pytest.mark.parametrize(
    ("old", "new", "arg"),
    [
        ('"tf-github-apply"', '"tf-github-apply-2"', "environment"),
        ("can_admins_bypass   = false", "can_admins_bypass   = true", "can_admins_bypass"),
        ("protected_branches     = false", "protected_branches     = true", "protected_branches"),
        ("custom_branch_policies = true", "custom_branch_policies = false", "custom_branch_policies"),
        ("prevent_destroy = true", "prevent_destroy = false", "prevent_destroy"),
    ],
    ids=["renamed", "admin-bypass", "protected-branches", "custom-policies", "destroyable"],
)
def test_k_environment_shape(gh, old, new, arg) -> None:
    got = v.check_github_root(_in_block(gh, _ENV_HEADER, old, new), "")
    want = old.rsplit(" ", 1)[-1]
    assert [x for x in got if x.startswith("k:")] == [
        f"k: github_repository_environment.tf_github_apply must set {arg} = {want}"
    ]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('branch_pattern = "main"', 'branch_pattern = "*"'),
        (
            "github_repository_environment.tf_github_apply.environment",
            "github_repository_environment.tf_gated_apply.environment",
        ),
    ],
    ids=["any-branch", "other-environment"],
)
def test_k_deployment_policy(gh, old, new) -> None:
    got = v.check_github_root(_in_block(gh, _POLICY_HEADER, old, new), "")
    assert 'k: tf_github_apply_main must bind tf_github_apply to branch_pattern "main"' in got


def test_k_environment_missing(gh) -> None:
    _swap(gh, _ENV_FILE, '"github_repository_environment" "tf_github_apply" {', '"github_repository_environment" "tf_other" {')
    assert len([x for x in v.check_github_root(gh, "") if x.startswith("k: github_repository_environment")]) == 5


def test_l_unclassified_resource_type(tree, gh) -> None:
    gh["extra.tf"] = '# resource "github_ignored" "x" {}\nresource "github_branch_protection" "main" {\n}\n'
    got = [x for x in v.check_github_root(gh, tree[5]) if x.startswith("l:")]
    assert got == [f"l: resource type github_branch_protection is in neither GATE_TYPES nor NON_GATE_TYPES of {v.GATE_SCRIPT}"]


def test_l_reads_the_gate_by_its_assignments(tree, gh) -> None:
    trimmed = tree[5].replace('"github_repository_ruleset"})', "})").replace('RULESET = "github_repository_ruleset"', "")
    assert "l: resource type github_repository_ruleset is in neither GATE_TYPES nor NON_GATE_TYPES of " + v.GATE_SCRIPT in (
        v.check_github_root(gh, trimmed)
    )


def test_apply_job_pins_python_and_preview_checks_login_shape(wf) -> None:
    apply_steps = wf["jobs"]["apply"]["steps"]
    setup = [s for s in apply_steps if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert len(setup) == 1 and setup[0]["with"]["python-version"] == "3.12"
    names = [s.get("name", "") for s in apply_steps]
    assert names.index(setup[0]["name"]) < next(i for i, n in enumerate(names) if n.startswith("Apply only"))
    lookup = next(s for s in wf["jobs"]["preview"]["steps"] if str(s.get("name", "")).startswith("Resolve reviewer ids"))
    assert '[[ "$login" =~ ^[A-Za-z0-9-]+$ ]]' in lookup["run"]
