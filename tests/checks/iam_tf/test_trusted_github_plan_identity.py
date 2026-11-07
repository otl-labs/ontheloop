"""Standing guard for PLAN-trusted-plan-github-root (rec-4638): the terraform/github PR-plan role.

PR 2 pins the bootstrap pre-add: the apply role's IAMRolesRead names the new role ahead of its creation, so the
gated apply's iam:GetRole after the create and every later refresh succeed. PR 3 adds the role's own cases.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READS_FILE = _REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply_reads.tf"
_ROLE_NAME = "agent-platform-github-ci-trusted-github-plan"
_ROLE_ARN = f"arn:aws:iam::${{var.account_id}}:role/{_ROLE_NAME}"


def _iam_roles_read_resources(text: str, sid: str = "IAMRolesRead") -> list[str]:
    match = re.search(rf'[Ss]id\s*=\s*"{sid}".*?[Rr]esources?\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    assert match, f"{sid} statement not found"
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return re.findall(r'"([^"]+)"', body)


def test_bootstrap_iam_roles_read_lists_the_github_plan_role() -> None:
    resources = _iam_roles_read_resources(_READS_FILE.read_text(encoding="utf-8"))
    assert resources.count(_ROLE_ARN) == 1


def test_bootstrap_assertion_reddens_without_the_arn() -> None:
    text = _READS_FILE.read_text(encoding="utf-8").replace(_ROLE_ARN, "arn:aws:iam::${var.account_id}:role/other")
    assert _ROLE_ARN not in _iam_roles_read_resources(text)
