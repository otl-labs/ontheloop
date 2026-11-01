"""Static guard over terraform/github's import blocks (PLAN-terraform-github-state-reconcile, rec-3136).

Every resource in terraform/github is keyed by repository name, so after a rename or transfer the state
is re-keyed by `terraform state rm` plus an import-only apply of imports.tf. The guard keeps that
re-key path whole: one import block per managed resource, each id derived from variables (never a
literal owner or repository name), the ruleset id from a declared number variable, and the
never-read repository attributes ignored so the import plans no repository update.

Each predicate takes a {filename: text} mapping so the red cases run the real predicate on synthetic
HCL. Names are read from docs/contracts/github-org-transfer.yaml, so this file quotes no identity.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tests.fixtures.terraform_hcl_blocks import find_resource_block, tf_dir_file_texts

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TF_GITHUB_DIR = _REPO_ROOT / "terraform" / "github"
_TRANSFER_CONTRACT = _REPO_ROOT / "docs" / "contracts" / "github-org-transfer.yaml"
_HISTORICAL_REPOSITORY_NAME = "agent-platform"

# address -> reason; only for a resource whose remote object does not exist yet.
EXEMPT: dict[str, str] = {}

_REQUIRED_IGNORED = (
    "ignore_vulnerability_alerts_during_read",
    "merge_commit_message",
    "merge_commit_title",
    "squash_merge_commit_message",
    "squash_merge_commit_title",
)

_RESOURCE_RE = re.compile(r'^\s*resource\s+"([A-Za-z0-9_]+)"\s+"([A-Za-z0-9_]+)"\s*\{', re.M)
_IMPORT_RE = re.compile(r"^\s*import\s*\{", re.M)
_TO_RE = re.compile(r"^\s*to\s*=\s*(\S+)\s*$", re.M)
_ID_RE = re.compile(r"^\s*id\s*=\s*(.+?)\s*$", re.M)
_ENVIRONMENT_ARG_RE = re.compile(r'^\s*environment\s*=\s*"([^"]+)"', re.M)
_ENVIRONMENT_REF_RE = re.compile(
    r"^\s*environment\s*=\s*github_repository_environment\.([A-Za-z0-9_]+)\.environment\s*$", re.M
)
_REFERENCE_RE = re.compile(r"\$\{[^}]*\}|\bvar\.[A-Za-z0-9_]+")


def _repository_name_id(_block: str, _text: str) -> str:
    return "var.repository_name"


def _ruleset_id(_block: str, _text: str) -> str:
    return '"${var.repository_name}:${var.main_protection_ruleset_id}"'


def _environment_id(block: str, _text: str) -> str:
    match = _ENVIRONMENT_ARG_RE.search(block)
    assert match is not None, "github_repository_environment resource declares no environment argument"
    return f'"${{var.repository_name}}:{match.group(1)}"'


def _deployment_policy_id(block: str, text: str) -> str:
    literal = _ENVIRONMENT_ARG_RE.search(block)
    if literal is not None:
        environment = literal.group(1)
    else:
        ref = _ENVIRONMENT_REF_RE.search(block)
        assert ref is not None, (
            "deployment policy environment is neither a literal nor a github_repository_environment reference"
        )
        environment = _ENVIRONMENT_ARG_RE.search(find_resource_block(text, "github_repository_environment", ref.group(1)))
        assert environment is not None, f"referenced environment {ref.group(1)} declares no literal environment"
        environment = environment.group(1)
    return f'"${{var.repository_name}}:{environment}:${{var.tf_trusted_plan_main_policy_id}}"'


_EXPECTED_ID_BY_TYPE: dict[str, Callable[[str, str], str]] = {
    "github_repository": _repository_name_id,
    "github_actions_repository_permissions": _repository_name_id,
    "github_workflow_repository_permissions": _repository_name_id,
    "github_repository_ruleset": _ruleset_id,
    "github_repository_environment": _environment_id,
    "github_repository_environment_deployment_policy": _deployment_policy_id,
}


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "//")))


def _balanced_block(text: str, open_brace: int) -> str:
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : i + 1]
    raise AssertionError(f"unbalanced block starting at offset {open_brace}")


def _joined(files: dict[str, str]) -> str:
    return "\n".join(_strip_comments(text) for text in files.values())


def _managed_addresses(files: dict[str, str]) -> list[str]:
    return [f"{rtype}.{name}" for rtype, name in _RESOURCE_RE.findall(_joined(files))]


def _import_blocks(files: dict[str, str]) -> list[tuple[str, str]]:
    """[(to, raw id expression)] for every import block across the root."""
    text = _joined(files)
    blocks: list[tuple[str, str]] = []
    for match in _IMPORT_RE.finditer(text):
        body = _balanced_block(text, match.end() - 1)
        to = _TO_RE.search(body)
        id_ = _ID_RE.search(body)
        assert to is not None and id_ is not None, f"import block without to or id: {body!r}"
        blocks.append((to.group(1), id_.group(1)))
    return blocks


def _identity_names() -> set[str]:
    contract = yaml.safe_load(_TRANSFER_CONTRACT.read_text(encoding="utf-8"))
    identity = contract["identity"]
    names = {identity[side][field] for side in ("current", "target") for field in ("owner", "repository")}
    return names | {_HISTORICAL_REPOSITORY_NAME}


def assert_one_import_per_managed_resource(files: dict[str, str], exempt: dict[str, str]) -> None:
    managed = _managed_addresses(files)
    targets = [to for to, _ in _import_blocks(files)]
    missing = [a for a in managed if a not in exempt and targets.count(a) != 1]
    assert not missing, f"managed resources without exactly one import block: {missing}"
    stray = [to for to in targets if to not in managed]
    assert not stray, f"import blocks whose 'to' is not a managed resource: {stray}"


def assert_import_ids_match_expected(files: dict[str, str]) -> None:
    text = _joined(files)
    for to, id_ in _import_blocks(files):
        rtype, name = to.split(".", 1)
        assert rtype in _EXPECTED_ID_BY_TYPE, f"no expected import id for resource type {rtype!r} ({to})"
        expected = _EXPECTED_ID_BY_TYPE[rtype](find_resource_block(text, rtype, name), text)
        assert id_ == expected, f"{to} import id is {id_!r}, expected {expected!r}"


def assert_no_literal_identity_in_ids(files: dict[str, str], names: set[str]) -> None:
    for to, id_ in _import_blocks(files):
        literal = _REFERENCE_RE.sub("", id_).lower()
        hits = sorted(n for n in names if n.lower() in literal)
        assert not hits, f"{to} import id {id_!r} carries a literal owner or repository name: {hits}"


def assert_ruleset_id_variable_declared(files: dict[str, str]) -> None:
    text = _joined(files)
    match = re.search(r'^\s*variable\s+"main_protection_ruleset_id"\s*\{', text, re.M)
    assert match is not None, "variable main_protection_ruleset_id is not declared"
    block = _balanced_block(text, match.end() - 1)
    assert re.search(r"^\s*type\s*=\s*number\s*$", block, re.M), "main_protection_ruleset_id is not type = number"
    assert re.search(r"^\s*default\s*=\s*\d+\s*$", block, re.M), "main_protection_ruleset_id has no numeric default"


def assert_repository_ignores_unread_attributes(files: dict[str, str]) -> None:
    block = find_resource_block(_joined(files), "github_repository", "this")
    match = re.search(r"ignore_changes\s*=\s*\[(.*?)\]", block, re.S)
    assert match is not None, "github_repository.this has no lifecycle.ignore_changes"
    ignored = set(re.findall(r"[A-Za-z0-9_]+", match.group(1)))
    missing = [name for name in _REQUIRED_IGNORED if name not in ignored]
    assert not missing, f"github_repository.this ignore_changes lacks {missing}: an import would plan a repository update"


_SYN_RESOURCES = """
resource "github_repository" "this" {
  name = var.repository_name
  lifecycle {
    ignore_changes = [
      description,
      ignore_vulnerability_alerts_during_read,
      merge_commit_message,
      merge_commit_title,
      squash_merge_commit_message,
      squash_merge_commit_title,
    ]
  }
}

resource "github_repository_environment" "gate" {
  repository  = github_repository.this.name
  environment = "gate-env"
}
"""

_SYN_IMPORTS = """
import {
  to = github_repository.this
  id = var.repository_name
}

import {
  to = github_repository_environment.gate
  id = "${var.repository_name}:gate-env"
}
"""

_SYN_VARIABLES = """
variable "main_protection_ruleset_id" {
  type    = number
  default = 42
}
"""


_SYN_POLICY_RESOURCE = """
resource "github_repository_environment_deployment_policy" "gate_main" {
  repository     = github_repository.this.name
  environment    = github_repository_environment.gate.environment
  branch_pattern = "main"
}
"""

_SYN_POLICY_IMPORT = """
import {
  to = github_repository_environment_deployment_policy.gate_main
  id = "${var.repository_name}:gate-env:${var.tf_trusted_plan_main_policy_id}"
}
"""


def _synthetic(**overrides: str) -> dict[str, str]:
    files = {"repo.tf": _SYN_RESOURCES, "imports.tf": _SYN_IMPORTS, "variables.tf": _SYN_VARIABLES}
    files.update(overrides)
    return files


@pytest.fixture(scope="module")
def real() -> dict[str, str]:
    return tf_dir_file_texts(_TF_GITHUB_DIR)


class TestTerraformGithubImportBlocks:
    def test_real_tree_one_import_per_managed_resource(self, real: dict[str, str]) -> None:
        assert _managed_addresses(real), "no managed resources found in terraform/github"
        assert_one_import_per_managed_resource(real, EXEMPT)

    def test_real_tree_import_ids_match_expected(self, real: dict[str, str]) -> None:
        assert_import_ids_match_expected(real)

    def test_real_tree_import_ids_carry_no_literal_identity(self, real: dict[str, str]) -> None:
        assert_no_literal_identity_in_ids(real, _identity_names())

    def test_real_tree_ruleset_id_variable_declared(self, real: dict[str, str]) -> None:
        assert_ruleset_id_variable_declared(real)

    def test_real_tree_repository_ignores_unread_attributes(self, real: dict[str, str]) -> None:
        assert_repository_ignores_unread_attributes(real)

    def test_synthetic_baseline_passes_every_predicate(self) -> None:
        files = _synthetic()
        assert_one_import_per_managed_resource(files, {})
        assert_import_ids_match_expected(files)
        assert_no_literal_identity_in_ids(files, _identity_names())
        assert_ruleset_id_variable_declared(files)
        assert_repository_ignores_unread_attributes(files)

    def test_red_resource_without_import_block(self) -> None:
        files = _synthetic(extra='resource "github_repository" "other" {\n  name = "x"\n}\n')
        with pytest.raises(AssertionError, match="github_repository.other"):
            assert_one_import_per_managed_resource(files, {})

    def test_exempt_resource_without_import_block_passes(self) -> None:
        files = _synthetic(extra='resource "github_repository" "other" {\n  name = "x"\n}\n')
        assert_one_import_per_managed_resource(files, {"github_repository.other": "remote object not created yet"})

    def test_red_duplicate_import_block(self) -> None:
        files = _synthetic(extra=_SYN_IMPORTS)
        with pytest.raises(AssertionError, match="exactly one import block"):
            assert_one_import_per_managed_resource(files, {})

    def test_red_import_to_unmanaged_address(self) -> None:
        files = _synthetic(extra="import {\n  to = github_repository.ghost\n  id = var.repository_name\n}\n")
        with pytest.raises(AssertionError, match="github_repository.ghost"):
            assert_one_import_per_managed_resource(files, {})

    def test_deployment_policy_id_resolves_the_referenced_environment(self) -> None:
        files = _synthetic(
            policy_resources=_SYN_POLICY_RESOURCE,
            policy_imports=_SYN_POLICY_IMPORT,
        )
        assert_import_ids_match_expected(files)

    def test_red_deployment_policy_import_id_carries_a_literal_id(self) -> None:
        files = _synthetic(
            policy_resources=_SYN_POLICY_RESOURCE,
            policy_imports=_SYN_POLICY_IMPORT.replace("${var.tf_trusted_plan_main_policy_id}", "12345"),
        )
        with pytest.raises(AssertionError, match="github_repository_environment_deployment_policy.gate_main import id"):
            assert_import_ids_match_expected(files)

    def test_red_import_id_not_expected_expression(self) -> None:
        files = _synthetic(
            **{"imports.tf": _SYN_IMPORTS.replace("  id = var.repository_name", '  id = "${var.github_owner}"')}
        )
        with pytest.raises(AssertionError, match="github_repository.this import id"):
            assert_import_ids_match_expected(files)

    def test_red_environment_import_id_differs_from_environment_argument(self) -> None:
        files = _synthetic(**{"imports.tf": _SYN_IMPORTS.replace(":gate-env", ":other-env")})
        with pytest.raises(AssertionError, match="github_repository_environment.gate import id"):
            assert_import_ids_match_expected(files)

    def test_red_resource_type_missing_from_table(self) -> None:
        files = _synthetic(
            extra='resource "github_branch" "b" {\n  branch = "x"\n}\n'
            "import {\n  to = github_branch.b\n  id = var.repository_name\n}\n"
        )
        with pytest.raises(AssertionError, match="github_branch"):
            assert_import_ids_match_expected(files)

    @pytest.mark.parametrize("field", ["owner", "repository"])
    @pytest.mark.parametrize("side", ["current", "target"])
    def test_red_literal_identity_in_import_id(self, side: str, field: str) -> None:
        contract = yaml.safe_load(_TRANSFER_CONTRACT.read_text(encoding="utf-8"))
        name = contract["identity"][side][field]
        files = _synthetic(**{"imports.tf": _SYN_IMPORTS.replace(":gate-env", f":{name}")})
        with pytest.raises(AssertionError, match="literal owner or repository name"):
            assert_no_literal_identity_in_ids(files, _identity_names())

    def test_red_historical_repository_name_in_import_id(self) -> None:
        files = _synthetic(
            **{"imports.tf": _SYN_IMPORTS.replace("  id = var.repository_name", f'  id = "{_HISTORICAL_REPOSITORY_NAME}"')}
        )
        with pytest.raises(AssertionError, match="literal owner or repository name"):
            assert_no_literal_identity_in_ids(files, _identity_names())

    def test_red_ruleset_variable_missing(self) -> None:
        files = _synthetic(**{"variables.tf": ""})
        with pytest.raises(AssertionError, match="not declared"):
            assert_ruleset_id_variable_declared(files)

    def test_red_ruleset_variable_not_number(self) -> None:
        files = _synthetic(**{"variables.tf": _SYN_VARIABLES.replace("type    = number", "type    = string")})
        with pytest.raises(AssertionError, match="type = number"):
            assert_ruleset_id_variable_declared(files)

    def test_red_ruleset_variable_without_default(self) -> None:
        files = _synthetic(**{"variables.tf": _SYN_VARIABLES.replace("  default = 42\n", "")})
        with pytest.raises(AssertionError, match="no numeric default"):
            assert_ruleset_id_variable_declared(files)

    @pytest.mark.parametrize("name", _REQUIRED_IGNORED)
    def test_red_ignore_changes_missing_name(self, name: str) -> None:
        files = _synthetic(**{"repo.tf": _SYN_RESOURCES.replace(f"      {name},\n", "")})
        with pytest.raises(AssertionError, match=name):
            assert_repository_ignores_unread_attributes(files)


def test_real_tree_deployment_policy_id_variable_is_a_real_id(real: dict[str, str]) -> None:
    text = _joined(real)
    match = re.search(r'^\s*variable\s+"tf_trusted_plan_main_policy_id"\s*\{', text, re.M)
    assert match is not None, "variable tf_trusted_plan_main_policy_id is not declared"
    default = re.search(r"^\s*default\s*=\s*(\d+)\s*$", _balanced_block(text, match.end() - 1), re.M)
    assert default is not None and int(default.group(1)) > 0, "tf_trusted_plan_main_policy_id has no real numeric default"
