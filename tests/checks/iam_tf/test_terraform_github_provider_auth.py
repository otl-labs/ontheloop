"""Static guard of terraform/github's single credential lane (PLAN-terraform-github-org-rekey, rec-4355).

The provider authenticates only with github_token: an ephemeral variable fed by an installation token minted outside
Terraform (the apply App's key never enters a variable, a saved plan or state), the PR-time plan's token, or the classic
PAT as rollback. There is no app_auth block, because provider 6.12.1 would require its three arguments in config. Each
predicate takes {filename: text} so the red cases run the real predicate on synthetic HCL.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures.terraform_hcl_blocks import tf_dir_file_texts

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TF_GITHUB_DIR = _REPO_ROOT / "terraform" / "github"
_PLAN_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "terraform-github-plan.yml"

_APP_ENV_NAMES = ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PEM_FILE")
_KEY_VARIABLE_FRAGMENTS = ("pem", "private_key", "app_id", "installation")


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"(#|//).*", "", line) for line in text.splitlines())


def _norm(expr: str) -> str:
    return re.sub(r"\s+", "", expr)


def _block(text: str, header: str) -> str | None:
    """Brace-balanced body of the first block whose header matches (comments stripped first)."""
    clean = _strip_comments(text)
    match = re.search(header + r"\s*\{", clean)
    if match is None:
        return None
    depth = 0
    for i in range(match.end() - 1, len(clean)):
        depth += (clean[i] == "{") - (clean[i] == "}")
        if depth == 0:
            return clean[match.end() : i]
    return None


def _attr(body: str, name: str) -> str | None:
    match = re.search(rf"^\s*{name}\s*=\s*(.+)$", body, re.M)
    return _norm(match.group(1)) if match else None


def no_app_auth_block(files: dict[str, str]) -> bool:
    return not any(re.search(r"\bapp_auth\b", _strip_comments(text)) for text in files.values())


def provider_owner_and_token_are_variables(files: dict[str, str]) -> bool:
    provider = _block(files.get("main.tf", ""), r'provider\s+"github"') or ""
    return _attr(provider, "owner") == "var.github_owner" and _attr(provider, "token") == "var.github_token"


def token_variable_is_ephemeral_without_default(files: dict[str, str]) -> bool:
    others = (text for name, text in files.items() if name != "main.tf")
    if any(_block(text, r'variable\s+"github_token"') is not None for text in others):
        return False
    body = _block(files.get("main.tf", ""), r'variable\s+"github_token"')
    if body is None:
        return False
    if _attr(body, "sensitive") != "true" or _attr(body, "ephemeral") != "true" or _attr(body, "default") is not None:
        return False
    conditions = {_attr(v, "condition") for v in re.findall(r"validation\s*\{(.*?)\n\s*\}", body, re.S)}
    return 'var.github_token!=""' in conditions


def no_variable_carries_app_key_material(files: dict[str, str]) -> bool:
    names = re.findall(r'variable\s+"([^"]+)"', _strip_comments("\n".join(files.values())))
    return not any(fragment in name.lower() for name in names for fragment in _KEY_VARIABLE_FRAGMENTS)


def plan_workflow_keeps_the_token_lane(workflow: str) -> bool:
    return "TF_VAR_github_token" in workflow and not any(name in workflow for name in _APP_ENV_NAMES)


_FILE_PREDICATES: dict[str, Callable[[dict[str, str]], bool]] = {
    "no_app_auth": no_app_auth_block,
    "owner_and_token_variables": provider_owner_and_token_are_variables,
    "token_ephemeral_no_default": token_variable_is_ephemeral_without_default,
    "no_key_variables": no_variable_carries_app_key_material,
}

_GOOD = {
    "main.tf": (
        'provider "github" {\n  owner = var.github_owner\n  token = var.github_token\n}\n\n'
        'variable "github_token" {\n  type      = string\n  sensitive = true\n  ephemeral = true\n\n'
        '  validation {\n    condition     = var.github_token != ""\n    error_message = "b"\n  }\n}\n'
    ),
    "variables.tf": 'variable "github_owner" {\n  type = string\n}\n',
}


def _mutated(fname: str, old: str, new: str) -> dict[str, str]:
    files = dict(_GOOD)
    assert old in files[fname], old
    files[fname] = files[fname].replace(old, new)
    return files


_RED_CASES: list[tuple[str, dict[str, str]]] = [
    ("no_app_auth", {**_GOOD, "extra.tf": 'provider "github" {\n  alias = "b"\n  app_auth {\n    id = "1"\n  }\n}\n'}),
    ("no_app_auth", _mutated("main.tf", "}\n", '  dynamic "app_auth" {\n    for_each = []\n    content {}\n  }\n}\n')),
    ("owner_and_token_variables", _mutated("main.tf", "owner = var.github_owner", 'owner = "x"')),
    ("owner_and_token_variables", _mutated("main.tf", "token = var.github_token", "token = var.other")),
    ("token_ephemeral_no_default", _mutated("main.tf", "ephemeral = true", "ephemeral = false")),
    ("token_ephemeral_no_default", _mutated("main.tf", "sensitive = true", "sensitive = false")),
    ("token_ephemeral_no_default", _mutated("main.tf", "ephemeral = true", "ephemeral = true\n  default   = null")),
    ("token_ephemeral_no_default", _mutated("main.tf", 'var.github_token != ""', "true")),
    ("token_ephemeral_no_default", {**_GOOD, "variables.tf": 'variable "github_token" {\n  type = string\n}\n'}),
    ("no_key_variables", {**_GOOD, "extra.tf": 'variable "app_private_key" {\n  type = string\n}\n'}),
    ("no_key_variables", {**_GOOD, "extra.tf": 'variable "installation_id" {\n  type = string\n}\n'}),
]


class TestTerraformGithubProviderAuth:
    @pytest.mark.parametrize("name", sorted(_FILE_PREDICATES))
    def test_real_tree(self, name: str) -> None:
        assert _FILE_PREDICATES[name](tf_dir_file_texts(_TF_GITHUB_DIR))

    @pytest.mark.parametrize("name", sorted(_FILE_PREDICATES))
    def test_synthetic_good_text(self, name: str) -> None:
        assert _FILE_PREDICATES[name](_GOOD)

    @pytest.mark.parametrize(("name", "files"), _RED_CASES)
    def test_red_case(self, name: str, files: dict[str, str]) -> None:
        assert not _FILE_PREDICATES[name](files)

    def test_every_predicate_has_a_red_case(self) -> None:
        assert {name for name, _ in _RED_CASES} == set(_FILE_PREDICATES)

    def test_plan_workflow_keeps_the_token_lane(self) -> None:
        assert plan_workflow_keeps_the_token_lane(_PLAN_WORKFLOW.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "workflow",
        [
            "env:\n  TF_VAR_github_token: x\n  GITHUB_APP_PEM_FILE: y\n",
            "env:\n  TF_VAR_github_token: x\n  GITHUB_APP_ID: y\n",
            "env:\n  OTHER: x\n",
        ],
    )
    def test_workflow_red_cases(self, workflow: str) -> None:
        assert not plan_workflow_keeps_the_token_lane(workflow)
