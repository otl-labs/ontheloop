"""rec-4335 acceptance: the PR-time terraform/github plan workflow and taxonomy wiring.

Split from test_terraform_github_pr_plan.py (SLOC relief, PLAN-trusted-plan-github-root PR 3); moved verbatim.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TF_DIR = REPO_ROOT / "terraform" / "personal"

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terraform-github-plan.yml"
TAXONOMY = REPO_ROOT / "docs" / "contracts" / "environment-taxonomy.yaml"


def _steps(wf: dict) -> list[dict]:
    return wf["jobs"]["plan"]["steps"]


def _step(wf: dict, uses_prefix: str) -> dict:
    return next(s for s in _steps(wf) if s.get("uses", "").startswith(uses_prefix))


class TestWorkflowAndTaxonomy:
    @pytest.fixture(scope="class")
    def wf(self) -> dict:
        return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def test_triggers_exact(self, wf) -> None:
        assert set(wf[True]) == {"pull_request_target"}
        assert wf[True]["pull_request_target"] == {
            "types": ["opened", "synchronize", "reopened"],
            "paths": ["terraform/github/**"],
        }

    def test_guard_and_permissions(self, wf) -> None:
        cond = wf["jobs"]["plan"]["if"]
        assert "github.event.pull_request.head.repo.full_name == github.repository" in cond
        assert "github.event.pull_request.base.ref == 'main'" in cond
        assert "github.actor != 'dependabot[bot]'" in cond
        assert wf["jobs"]["plan"]["environment"] == {"name": "tf-trusted-plan", "deployment": False}
        assert wf["permissions"] == {}
        assert wf["jobs"]["plan"]["permissions"] == {"id-token": "write", "contents": "read", "pull-requests": "write"}

    def test_aws_credentials_step(self, wf) -> None:
        step = _step(wf, "aws-actions/configure-aws-credentials")
        role = step["with"]["role-to-assume"]
        assert "secrets.AWS_ACCOUNT_ID" in role and role.endswith("role/agent-platform-github-ci-trusted-github-plan")
        session = step["with"]["role-session-name"]
        assert session == "tf-trusted-github-plan"
        oidc = (TF_DIR / "oidc.tf").read_text(encoding="utf-8")
        reserved = re.search(r'convergence_writer_session_name\s*=\s*"([^"]+)"', oidc)
        assert reserved and session != reserved.group(1)
        assert "continue-on-error" not in step

    def test_app_token_step(self, wf) -> None:
        step = _step(wf, "actions/create-github-app-token")
        assert set(step["with"]) == {
            "client-id",
            "private-key",
            "owner",
            "repositories",
            "permission-metadata",
            "permission-administration",
            "permission-actions",
        }
        assert all(v == "read" for k, v in step["with"].items() if k.startswith("permission-"))
        assert step["with"]["private-key"] == "${{ secrets.TF_GITHUB_PLAN_APP_PRIVATE_KEY }}"

    def test_plan_and_probe_flags(self, wf) -> None:
        plan = next(s for s in _steps(wf) if s.get("id") == "plan")["run"]
        assert "quiet_run.sh" in plan and "-lock=false" in plan and "-detailed-exitcode" in plan
        probe = next(s for s in _steps(wf) if "Probe" in s.get("name", ""))["run"]
        assert "!= null" in probe and 'has("bypass_actors")' in probe

    def test_nothing_mutating_or_persisted(self, wf) -> None:
        forbidden = [
            "terraform apply",
            "terraform import",
            "state rm",
            "state mv",
            "state push",
            "force-unlock",
            "aws s3 cp",
            "GITHUB_ENV",
        ]
        s3_steps = [s for s in _steps(wf) if "aws s3" in s.get("run", "")]
        assert len(s3_steps) == 1
        assert s3_steps[0]["run"] == (
            'aws s3 sync "s3://agent-platform-data-lake/tf-provider-mirror/" "$RUNNER_TEMP/tf-mirror" '
            '--exclude "*" --include "registry.terraform.io/integrations/github/*" --only-show-errors'
        )
        for s in _steps(wf):
            for token in forbidden:
                assert token not in s.get("run", ""), (token, s.get("name"))
            assert not s.get("uses", "").startswith("actions/upload-artifact")
        last = _steps(wf)[-1]
        assert last["if"] == "always()" and "plan.bin" in last["run"] and "plan.json" in last["run"]

    def test_reviewer_ids_never_in_step_env(self, wf) -> None:
        for s in _steps(wf):
            for key in s.get("env") or {}:
                assert "USER_IDS" not in key.upper() and "REVIEWER_IDS" not in key.upper()

    def test_not_a_required_check(self) -> None:
        assert "terraform-github-plan" not in (REPO_ROOT / "terraform" / "github" / "repo.tf").read_text(encoding="utf-8")

    def test_taxonomy_row(self) -> None:
        row = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))["axis_a"]["github"]
        assert "terraform/github" in row["code_vs_infra"]
        gating = row["apply_gating"]
        assert "terraform-github-plan.yml" in gating and "NEVER auto-apply" in gating and not gating.startswith("admin-only")
        assert "rec-4638" in gating

    def test_admin_root_gate_does_not_derive_github(self) -> None:
        from scripts.checks.verification import validate_admin_root_premerge_gate as gate

        result = gate._derive_admin_only_roots(TAXONOMY)
        roots, err = result
        assert not err
        flat = " ".join(map(str, roots))
        assert "terraform/bootstrap" in flat and "terraform/github" not in flat

    def test_claude_md_pointer(self) -> None:
        text = (REPO_ROOT / "terraform" / "github" / "CLAUDE.md").read_text(encoding="utf-8")
        assert ".github/workflows/terraform-github-plan.yml" in text
