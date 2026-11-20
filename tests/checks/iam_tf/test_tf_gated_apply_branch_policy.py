"""rec-4651 / rec-4549: every GitHub Environment but the approval probe is main-only by an explicit policy."""

from __future__ import annotations

import re
from pathlib import Path

from tests.fixtures.terraform_hcl_blocks import find_resource_block, tf_dir_file_texts

_TF_DIR = Path(__file__).resolve().parents[3] / "terraform" / "github"
EXEMPT = frozenset({"deployment_approval_probe"})
_ENV = "github_repository_environment"
_POLICY = "github_repository_environment_deployment_policy"
_GATE_VAR = "var.gated_apply_reviewer_user_ids"


def _text() -> str:
    return "\n".join(tf_dir_file_texts(_TF_DIR).values())


def _names(text: str, rtype: str) -> list[str]:
    return re.findall(rf'^resource\s+"{rtype}"\s+"([A-Za-z0-9_]+)"', text, re.M)


def _attr(block: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*$", block, re.M)
    return m.group(1) if m else None


def _env_label(block: str) -> str:
    return (_attr(block, "environment") or "").strip('"')


def main_only_problems(text: str) -> tuple[list[str], set[str]]:
    problems: list[str] = []
    examined: set[str] = set()
    policies = {n: find_resource_block(text, _POLICY, n) for n in _names(text, _POLICY)}
    for name in _names(text, _ENV):
        if name in EXEMPT:
            continue
        block = find_resource_block(text, _ENV, name)
        label = _env_label(block)
        examined.add(label)
        if _attr(block, "protected_branches") != "false" or _attr(block, "custom_branch_policies") != "true":
            problems.append(f"{name}: not custom-policy only")
        bound = [p for p in policies.values() if _attr(p, "environment") in (f"{_ENV}.{name}.environment", f'"{label}"')]
        if len(bound) != 1:
            problems.append(f"{name}: expected exactly one deployment policy, found {len(bound)}")
            continue
        if _attr(bound[0], "branch_pattern") != '"main"' or _attr(bound[0], "tag_pattern") is not None:
            problems.append(f"{name}: policy is not branch_pattern main")
    if len(examined) < 2:
        problems.append("fewer than two environments examined")
    return problems, examined


def gated_problems(text: str) -> list[str]:
    out: list[str] = []
    for name in ("tf_gated_apply", "pypi"):
        if name not in _names(text, _ENV):
            out.append(f"{name}: not declared")
            continue
        block = find_resource_block(text, _ENV, name)
        if not re.search(rf"reviewers\s*\{{[^}}]*users\s*=\s*{re.escape(_GATE_VAR)}", block):
            out.append(f"{name}: no reviewers block bound to {_GATE_VAR}")
        if _attr(block, "prevent_self_review") != "false":
            out.append(f"{name}: prevent_self_review is not false")
        if not re.search(r"prevent_destroy\s*=\s*true", block):
            out.append(f"{name}: no prevent_destroy")
    return out


def test_every_environment_but_the_probe_is_main_only() -> None:
    problems, examined = main_only_problems(_text())
    assert problems == []
    assert {"tf-gated-apply", "pypi"} <= examined


def test_gated_environments_keep_their_reviewer() -> None:
    assert gated_problems(_text()) == []


def test_pypi_has_no_admin_bypass() -> None:
    block = find_resource_block(_text(), _ENV, "pypi")
    assert _attr(block, "environment") == '"pypi"' and _attr(block, "can_admins_bypass") == "false"


_GOOD = """
resource "github_repository_environment" "a" {
  environment = "a"
  reviewers {
    users = var.gated_apply_reviewer_user_ids
  }
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}
resource "github_repository_environment_deployment_policy" "a_main" {
  environment    = github_repository_environment.a.environment
  branch_pattern = "main"
}
resource "github_repository_environment" "b" {
  environment = "b"
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}
resource "github_repository_environment_deployment_policy" "b_main" {
  environment    = "b"
  branch_pattern = "main"
}
"""


def test_synthetic_green_case() -> None:
    assert main_only_problems(_GOOD)[0] == []


def test_red_protected_branches_true() -> None:
    bad = _GOOD.replace("protected_branches     = false", "protected_branches     = true", 1)
    assert main_only_problems(bad)[0]


def test_red_wildcard_pattern() -> None:
    assert main_only_problems(_GOOD.replace('"main"', '"*"', 1))[0]


def test_red_missing_policy() -> None:
    bad = _GOOD.replace('resource "github_repository_environment_deployment_policy" "a_main"', 'resource "x" "a_main"')
    assert any("a:" in p for p in main_only_problems(bad)[0])


def test_red_policy_bound_to_another_environment() -> None:
    bad = _GOOD.replace("github_repository_environment.a.environment", "github_repository_environment.b.environment")
    assert any("a:" in p for p in main_only_problems(bad)[0])


def test_red_second_policy_by_literal_name() -> None:
    bad = (
        _GOOD
        + 'resource "github_repository_environment_deployment_policy" "a_dup" {\n  environment = "a"\n'
        + '  branch_pattern = "main"\n}\n'
    )
    assert any("a:" in p for p in main_only_problems(bad)[0])


def test_red_missing_reviewers_block() -> None:
    text = _GOOD.replace("users = var.gated_apply_reviewer_user_ids", "users = []")
    assert gated_problems(text)
