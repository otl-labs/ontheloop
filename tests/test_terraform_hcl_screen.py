"""Static screen and plan backstop for the trusted plan (Decision 238 cl.3-4, PLAN-trusted-plan-cutover PR 2)."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ci import terraform_hcl_screen as screen  # noqa: E402

SCRIPT = ROOT / "scripts" / "ci" / "terraform_hcl_screen.py"
FIXTURE = ROOT / "tests" / "fixtures" / "terraform_show_terraform_data.json"
GOOD = 'resource "aws_sns_topic" "t" {\n  name = "x"\n}\n'


def rules(files: dict[str, str]) -> set[str]:
    return {r for r, _f, _l in screen.screen_tree(files)}


def one(text: str, name: str = "x.tf") -> set[str]:
    return rules({name: text})


def test_real_tree_passes() -> None:
    assert screen.screen_root(ROOT / "terraform" / "personal") == []


def test_cli_screen_pass_and_refusal(tmp_path: Path) -> None:
    (tmp_path / "a.tf").write_text(GOOD, encoding="utf-8")
    ok = subprocess.run([sys.executable, "-I", str(SCRIPT), "screen", "--root", str(tmp_path)], capture_output=True, text=True)
    assert (ok.returncode, ok.stdout.strip()) == (0, "PASS")
    (tmp_path / "b.tf").write_text("locals {\n  x = <<EOT\nhi\nEOT\n}\n", encoding="utf-8")
    bad = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "screen", "--root", str(tmp_path)], capture_output=True, text=True
    )
    assert bad.returncode == 1
    assert bad.stdout.strip() == "REFUSED heredoc b.tf:2"


STRING_LEGAL = [
    'locals {\n  a = "arn:aws:s3:::b/*"\n  b = "x << y"\n  c = "100%"\n  d = "cron(0 3 ? * SUN *)"\n  e = "a == b && !c"\n}\n',
    'locals {\n  a = "$${escaped}"\n  b = "${var.x}-y"\n}\n',
    "locals {\n  # comment with /* and << and %{\n  a = 1 // trailing\n}\n",
]


@pytest.mark.parametrize("text", STRING_LEGAL)
def test_legal_text_inside_strings_and_comments(text: str) -> None:
    assert one(text) == set()


LEXER_RED = [
    ("heredoc", "locals {\n  a = <<EOT\nx\nEOT\n}\n"),
    ("block_comment", "locals {\n  /* hi */\n  a = 1\n}\n"),
    ("template_directive", 'locals {\n  a = "%{ if true }x%{ endif }"\n}\n'),
    ("nested_interpolation", 'locals {\n  a = "${foo("${bar}")}"\n}\n'),
    ("non_ascii_or_control", 'locals {\n  a = "café"\n}\n'),
    ("non_ascii_or_control", "locals {\r\n  a = 1\r\n}\r\n"),
    ("unterminated_string", 'locals {\n  a = "abc\n}\n'),
    ("unbalanced_braces", "locals {\n  a = 1\n"),
    ("unclassified_character", "locals {\n  a = 1 ; 2\n}\n"),
]


@pytest.mark.parametrize(("rule", "text"), LEXER_RED)
def test_lexer_refusals(rule: str, text: str) -> None:
    assert rule in one(text)


@pytest.mark.parametrize("kw", ["terraform", "provider", "backend", "module", "import", "check", "ephemeral"])
def test_banned_toplevel_keywords(kw: str) -> None:
    assert "toplevel_block" in one(f'{kw} "x" {{\n  a = 1\n}}\n')


def test_providers_tf_only_keyword_in_another_file() -> None:
    assert "toplevel_block" in rules({"main.tf": 'provider "aws" {\n  region = "x"\n}\n'})
    assert rules({"providers.tf": 'provider "aws" {\n  region = "x"\n}\n'}) == set()


def test_stray_toplevel_tokens() -> None:
    assert "unclassified_token" in one("x = 1\n")
    assert "unclassified_token" in one('"str" {\n}\n')


def test_resource_and_data_type_allowlists() -> None:
    assert "resource_type" in one('resource "aws_iam_user" "u" {\n  name = "x"\n}\n')
    assert "data_type" in one('data "aws_secretsmanager_secret_version" "s" {\n  secret_id = "x"\n}\n')
    assert "data_type" in one('data "aws_ssm_parameter" "s" {\n  name = "x"\n  with_decryption = true\n}\n')
    assert one('data "aws_iam_policy_document" "d" {\n  statement {\n    actions = ["s3:Get*"]\n  }\n}\n') == set()


FUNCTION_RED = [
    'provider::aws::arn_parse("x")',
    'templatefile("x.tpl", {})',
    'fileset(path.module, "*")',
    "timestamp()",
    "uuid()",
    'filebase64("x")',
    "sensitive(var.x)",
    "nonsensitive(var.x)",
]


@pytest.mark.parametrize("call", FUNCTION_RED)
def test_function_allowlist(call: str) -> None:
    assert one(f"locals {{\n  a = {call}\n}}\n") & {"function_not_allowlisted", "provider_function"}


@pytest.mark.parametrize(
    "path",
    [
        '"/etc/passwd"',
        '"${path.module}/../../config/other.yaml"',
        '"${path.root}/x"',
        "var.p",
        '"${var.p}"',
        '"/proc/self/environ"',
    ],
)
def test_file_path_rule(path: str) -> None:
    assert "file_path_not_allowlisted" in one(f"locals {{\n  a = file({path})\n}}\n")


def test_file_allowed_paths() -> None:
    for p in sorted(screen.ALLOWED_FILE_PATHS):
        assert one(f'locals {{\n  a = file("{p}")\n}}\n') == set()


SHAPE_RED = [
    ("meta_argument", 'resource "aws_sns_topic" "t" {\n  count = 2\n  name = "x"\n}\n'),
    ("meta_argument", 'resource "aws_sns_topic" "t" {\n  for_each = toset(["a"])\n  name = "x"\n}\n'),
    ("dynamic_block", 'resource "aws_sns_topic" "t" {\n  dynamic "x" {\n    for_each = []\n    content {}\n  }\n}\n'),
    ("conditional_operator", "locals {\n  a = var.x ? 1 : 2\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = 1 == 2\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = 1 != 2\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = 1 < 2\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = 1 >= 2\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = true && false\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = true || false\n}\n"),
    ("comparison_or_logical_operator", "locals {\n  a = !true\n}\n"),
    ("for_if_clause", "locals {\n  a = [for x in [1] : x if x]\n}\n"),
    ("validation_only_function", 'locals {\n  a = regex("a", "b")\n}\n'),
    ("validation_only_function", 'locals {\n  a = can(length("a"))\n}\n'),
    ("try_form_not_allowed", 'locals {\n  a = try(var.x, "d")\n}\n'),
    (
        "try_form_not_allowed",
        'locals {\n  a = try(yamldecode(file("${path.module}/../../config/feature_flags.yaml")).a, var.x)\n}\n',
    ),
    ("try_form_not_allowed", 'locals {\n  a = try(jsondecode("{}").a, "d")\n}\n'),
]


@pytest.mark.parametrize(("rule", "text"), SHAPE_RED)
def test_shape_rules(rule: str, text: str) -> None:
    assert rule in one(text)


def test_shape_constructs_allowed_where_main_uses_them() -> None:
    text = (
        'variable "v" {\n  type = string\n\n  validation {\n    condition     = can(regex("^[0-9]+$", var.v))\n'
        '    error_message = "digits"\n  }\n}\n'
        'resource "aws_iam_role" "r" {\n  name = "r"\n  assume_role_policy = "{}"\n\n  lifecycle {\n'
        '    precondition {\n      condition     = length(jsonencode(jsondecode("{}"))) <= 10240\n'
        '      error_message = "too big"\n    }\n  }\n}\n'
        'locals {\n  a = [for r in ["x"] : upper_name(r)]\n'
        '  b = try(yamldecode(file("${path.module}/../../config/lambda/ducklake/version.yaml")).duckdb_version, "")\n'
        '  c = ceil(2 * 3 / 1000) * 1000\n  d = format("%s-%s", "a", "b")\n}\n'
    )
    assert "function_not_allowlisted" in one(text)
    assert one(text.replace("upper_name(r)", "r")) == set()


def test_condition_operators_only_in_condition_blocks() -> None:
    text = (
        'resource "aws_sns_topic" "t" {\n  name = "x"\n\n  lifecycle {\n    postcondition {\n'
        '      condition     = self.name == "x"\n      error_message = "m"\n    }\n  }\n}\n'
    )
    assert one(text) == set()
    assert "comparison_or_logical_operator" in one(text.replace("lifecycle", "other").replace("postcondition", "inner"))


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("x.tf.json", "tf_json"),
        ("override.tf", "override_file"),
        ("x_override.tf", "override_file"),
        ("terraform.tfvars", "tfvars_file"),
        ("x.auto.tfvars", "tfvars_file"),
        ("x.auto.tfvars.json", "tfvars_file"),
        ("x.txt", "file_not_tf"),
        ("x.sh", "file_not_tf"),
    ],
)
def test_tree_rules(name: str, rule: str) -> None:
    assert rule in rules({name: "", "a.tf": GOOD})


def test_tree_allows_main_only_and_docs() -> None:
    files = dict.fromkeys(screen.MAIN_ONLY_FILES | screen.DOC_FILES, "")
    files["a.tf"] = GOOD
    assert rules(files) == set()


def test_read_root_rejects_subdirectory_and_bad_names(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / ".terraform").mkdir()
    (tmp_path / "bad name.tf").write_text(GOOD, encoding="utf-8")
    found = screen.screen_root(tmp_path)
    assert ("subdirectory", "sub", 0) in found
    assert any(r == "file_name" for r, _f, _l in found)
    assert not any(f == ".terraform" for _r, f, _l in found)


def test_non_utf8_file_refused(tmp_path: Path) -> None:
    (tmp_path / "a.tf").write_bytes(b'locals {\n a = "\xff"\n}\n')
    assert any(r == "non_ascii_or_control" for r, _f, _l in screen.screen_root(tmp_path))


# ---------------------------------------------------------------- backstop


def _good_plan() -> dict:
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan["configuration"]["provider_config"] = {"aws": {"name": "aws"}, "neon": {"name": "neon"}}
    plan["configuration"]["root_module"]["resources"] = [
        {"mode": "managed", "type": "aws_sns_topic", "name": "t"},
        {"mode": "data", "type": "aws_iam_policy_document", "name": "d"},
    ]
    return plan


def test_backstop_passes_real_plan_fixture_with_resource_and_variable_checks() -> None:
    plan = _good_plan()
    kinds = {c["address"]["kind"] for c in plan["checks"]}
    assert kinds == {"resource", "var"}
    assert screen.backstop(plan) is None


def _mutate(fn) -> dict:
    plan = copy.deepcopy(_good_plan())
    fn(plan)
    return plan


@pytest.mark.parametrize(
    ("rule", "fn"),
    [
        (
            "provider_config",
            lambda p: p["configuration"]["provider_config"].update({"aws.alt": {"name": "aws", "alias": "alt"}}),
        ),
        ("provider_config", lambda p: p["configuration"]["provider_config"]["aws"].update({"alias": "x"})),
        ("provider_config", lambda p: p["configuration"]["provider_config"].pop("neon")),
        ("module_calls", lambda p: p["configuration"]["root_module"].update({"module_calls": {"m": {}}})),
        (
            "resource_type",
            lambda p: p["configuration"]["root_module"]["resources"].append({"mode": "managed", "type": "aws_iam_user"}),
        ),
        (
            "data_type",
            lambda p: p["configuration"]["root_module"]["resources"].append({"mode": "data", "type": "aws_ssm_parameter"}),
        ),
        ("importing", lambda p: p.update({"resource_changes": [{"change": {"importing": {"id": "x"}}}]})),
        ("check_block", lambda p: p["checks"].append({"address": {"kind": "check", "name": "c"}})),
        ("plan_unreadable", lambda p: p.pop("configuration")),
    ],
)
def test_backstop_refusals(rule: str, fn) -> None:
    assert screen.backstop(_mutate(fn)) == rule


def test_backstop_cli_output_has_no_address_or_expression(tmp_path: Path) -> None:
    plan = _mutate(
        lambda p: p["configuration"]["root_module"]["resources"].append(
            {"mode": "managed", "type": "aws_iam_user", "name": "secretname"}
        )
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "backstop", "--plan-json", str(path)], capture_output=True, text=True
    )
    assert out.returncode == 1
    assert out.stdout.strip() == "BACKSTOP_REFUSED resource_type"
    assert "secretname" not in out.stdout + out.stderr
    path.write_text(json.dumps(_good_plan()), encoding="utf-8")
    ok = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "backstop", "--plan-json", str(path)], capture_output=True, text=True
    )
    assert (ok.returncode, ok.stdout.strip()) == (0, "BACKSTOP_OK")
    missing = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "backstop", "--plan-json", str(tmp_path / "nope")], capture_output=True, text=True
    )
    assert missing.stdout.strip() == "BACKSTOP_REFUSED plan_unreadable"


@pytest.mark.parametrize(
    "block",
    [
        'provisioner "local-exec" {\n    command = "x"\n  }',
        'connection {\n    host = "x"\n  }',
    ],
)
def test_provisioner_and_connection_blocks_refused(block: str) -> None:
    assert "provisioner_block" in one(f'resource "aws_s3_bucket" "b" {{\n  {block}\n}}\n')
    assert "provisioner_block" in one(f"removed {{\n  from = aws_s3_bucket.b\n  {block}\n}}\n")


def test_connection_attribute_name_is_not_a_block() -> None:
    assert "provisioner_block" not in one("locals {\n  connection = 1\n}\n")


@pytest.mark.parametrize("key", ["provisioners", "count_expression", "for_each_expression"])
def test_backstop_refuses_provisioners_and_meta_expressions(key: str) -> None:
    plan = _good_plan()
    plan["configuration"]["root_module"]["resources"][0][key] = [{"type": "local-exec"}] if key == "provisioners" else {"x": 1}
    assert screen.backstop(plan) == "provisioner_or_meta_argument"


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("provisioner_block", 'resource "aws_s3_bucket" "b" {\n  provisioner local-exec {\n    command = "x"\n  }\n}\n'),
        ("provisioner_block", 'resource "aws_s3_bucket" "b" {\n  connection ssh {\n  }\n}\n'),
        ("dynamic_block", 'resource "aws_s3_bucket" "b" {\n  dynamic x {\n    for_each = [1]\n    content {}\n  }\n}\n'),
        ("unclassified_token", "resource aws_instance x {\n  a = 1\n}\n"),
        ("unclassified_token", "data external e {\n  a = 1\n}\n"),
    ],
)
def test_unquoted_label_forms_are_refused(rule: str, text: str) -> None:
    assert rule in one(text)


def test_dotted_connection_reference_is_not_a_block() -> None:
    assert "provisioner_block" not in one("locals {\n  a = local.connection\n}\n")


GH = "terraform/github"


def _gh_rules(files: dict[str, str]) -> set[str]:
    from scripts.ci import terraform_hcl_screen_policy as policies

    return {r for r, _f, _l in screen.screen_tree(files, policies.POLICIES[GH])}


def _imp(to: str, id_: str) -> str:
    return f"import {{\n  to = {to}\n  id = {id_}\n}}\n"


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, cwd=ROOT)


class TestGithubRoot:
    def test_real_tree_is_green(self) -> None:
        out = _cli("screen", "--root", GH, "--policy", GH)
        assert (out.returncode, out.stdout.strip()) == (0, "PASS")

    @pytest.mark.parametrize(
        "block",
        [
            _imp("github_repository.this", "var.repository_name"),
            _imp("github_repository_ruleset.m", '"${var.repository_name}:${var.main_protection_ruleset_id}"'),
            _imp("github_repository_environment.e", '"${var.repository_name}:tf-trusted-plan"'),
            _imp("github_repository.this", '"literal-id"'),
        ],
    )
    def test_import_ids_main_uses_are_accepted(self, block: str) -> None:
        assert _gh_rules({"i.tf": block}) == set()

    @pytest.mark.parametrize(
        "text",
        [
            'resource "github_actions_secret" "s" {\n}\n',
            'resource "github_actions_environment_secret" "s" {\n}\n',
            'resource "github_actions_variable" "s" {\n}\n',
            'resource "github_repository_webhook" "s" {\n}\n',
            'resource "github_repository_deploy_key" "s" {\n}\n',
            'resource "aws_s3_bucket" "s" {\n}\n',
            'data "github_user" "u" {\n}\n',
            'provider "github" {\n}\n',
            "terraform {\n}\n",
            'locals {\n  a = file("x")\n}\n',
            "locals {\n  a = jsonencode({})\n}\n",
        ],
    )
    def test_github_red_cases(self, text: str) -> None:
        assert _gh_rules({"x.tf": text})

    def test_provider_block_is_allowed_only_in_main_tf(self) -> None:
        assert _gh_rules({"main.tf": 'provider "github" {\n}\n'}) == set()
        assert _gh_rules({"other.tf": 'provider "github" {\n}\n'}) == {"toplevel_block"}

    @pytest.mark.parametrize(
        "block",
        [
            _imp("github_repository.this", "lower(var.repository_name)"),
            _imp("github_repository.this", '"x:${var.github_token}"'),
            _imp("github_repository.this", '"${var.repository_name}:${var.github_token}"'),
            _imp("github_repository.this", '"${var.repository_name}:${var.gated_apply_reviewer_user_ids}"'),
            _imp("github_repository.this", '"${var.repository_name}:${var.not_in_the_set}"'),
            _imp("github_repository.this", '"${var.main_protection_ruleset_id}"'),
            _imp("github_repository.this", "var.main_protection_ruleset_id"),
            _imp("github_repository.this", '"${var.repository_name}:${lower(var.repository_name)}"'),
            _imp("github_actions_secret.s", "var.repository_name"),
            'import "x" {\n  to = github_repository.this\n  id = var.github_token\n}\n',
            _imp("github_repository.this[0]", "var.repository_name"),
            _imp('github_repository.this["a"]', "var.repository_name"),
            _imp("module.m.github_repository.this", "var.repository_name"),
            "import {\n  to = github_repository.this\n  id = var.repository_name\n  provider = github\n}\n",
            "import {\n  to = github_repository.this\n}\n",
        ],
    )
    def test_import_shape_violations(self, block: str) -> None:
        assert "import_shape" in _gh_rules({"i.tf": block})

    def test_import_is_refused_for_the_personal_row(self) -> None:
        assert "toplevel_block" in one(_imp("aws_sns_topic.t", '"x"'))

    def test_unknown_policy_row_is_refused(self) -> None:
        out = _cli("screen", "--root", GH, "--policy", "terraform/nothing")
        assert (out.returncode, out.stdout.strip()) == (1, "REFUSED unknown_root")

    @pytest.mark.parametrize(
        "args",
        [
            ("--root", GH),
            ("--root", "./terraform/github/"),
            ("--root", str(ROOT / "terraform" / "github")),
            ("--root", "terraform/personal", "--policy", GH),
        ],
    )
    def test_root_policy_mismatch(self, args: tuple[str, ...]) -> None:
        out = _cli("screen", *args)
        assert (out.returncode, out.stdout.strip()) == (1, "REFUSED root_policy_mismatch")

    def test_tmp_path_root_with_github_policy_is_screened_not_refused(self, tmp_path: Path) -> None:
        (tmp_path / "a.tf").write_text('resource "github_repository" "r" {\n  name = "x"\n}\n', encoding="utf-8")
        out = _cli("screen", "--root", str(tmp_path), "--policy", GH)
        assert (out.returncode, out.stdout.strip()) == (0, "PASS")

    def test_github_backstop_provider_and_importing_rules(self, tmp_path: Path) -> None:
        from scripts.ci import terraform_hcl_screen_policy as policies

        gh = policies.POLICIES[GH]
        plan = {"configuration": {"provider_config": {"github": {}}, "root_module": {}}, "resource_changes": []}
        assert screen.backstop(plan, gh) is None
        assert screen.backstop(plan) == "provider_config"
        bad = {"configuration": {"provider_config": {"aws": {}, "neon": {}}, "root_module": {}}}
        assert screen.backstop(bad, gh) == "provider_config"
        imp = {"type": "github_repository", "change": {"importing": {"id": "x"}}}
        plan["resource_changes"] = [imp]
        assert screen.backstop(plan, gh) is None
        plan["resource_changes"] = [{"type": "github_actions_secret", "change": {"importing": {"id": "x"}}}]
        assert screen.backstop(plan, gh) == "importing"
        personal = {"configuration": {"provider_config": {"aws": {}, "neon": {}}, "root_module": {}}}
        personal["resource_changes"] = [{"type": "aws_sns_topic", "change": {"importing": {"id": "x"}}}]
        assert screen.backstop(personal) == "importing"

    def test_backstop_cli_policy_flag(self, tmp_path: Path) -> None:
        f = tmp_path / "p.json"
        f.write_text(json.dumps({"configuration": {"provider_config": {"github": {}}, "root_module": {}}}), encoding="utf-8")
        ok = _cli("backstop", "--plan-json", str(f), "--policy", GH)
        assert (ok.returncode, ok.stdout.strip()) == (0, "BACKSTOP_OK")
        assert _cli("backstop", "--plan-json", str(f)).stdout.strip() == "BACKSTOP_REFUSED provider_config"
        assert _cli("backstop", "--plan-json", str(f), "--policy", "x/y").stdout.strip() == "BACKSTOP_REFUSED unknown_root"
