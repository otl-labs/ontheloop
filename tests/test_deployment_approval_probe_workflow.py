"""Static tests for the inert rec-4695 probe workflow and its Terraform Environment."""

from __future__ import annotations

import re
from typing import Any

import yaml

from scripts.checks._common import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "deployment-approval-probe.yml"
ENVIRONMENTS_TF = ROOT / "terraform" / "github" / "environments.tf"


def _wf() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def _tf_block() -> str:
    text = ENVIRONMENTS_TF.read_text(encoding="utf-8")
    match = re.search(r'resource "github_repository_environment" "deployment_approval_probe" \{.*?\n\}\n', text, re.S)
    assert match, "deployment_approval_probe resource missing"
    return match.group(0)


class TestWorkflow:
    def test_only_workflow_dispatch_with_bot_leg_input(self) -> None:
        on = _wf()["on"]
        assert list(on) == ["workflow_dispatch"]
        assert list(on["workflow_dispatch"]["inputs"]) == ["bot_leg"]

    def test_top_level_permissions_empty(self) -> None:
        assert _wf()["permissions"] == {}

    def test_gated_job_is_inert(self) -> None:
        job = _wf()["jobs"]["gated"]
        assert job["environment"] == "deployment-approval-probe"
        assert job["permissions"] == {}
        assert all("uses" not in step for step in job["steps"])
        assert "secrets." not in yaml.safe_dump(job)

    def test_bot_job_shape(self) -> None:
        job = _wf()["jobs"]["bot-attempt"]
        assert "environment" not in job
        assert job["permissions"] == {"contents": "read", "actions": "read", "deployments": "write"}
        assert "inputs.bot_leg" in job["if"]
        assert set(re.findall(r"secrets\.(\w+)", yaml.safe_dump(job))) == {"GITHUB_TOKEN"}
        run = "\n".join(s.get("run", "") for s in job["steps"])
        assert "scripts.session.deployment_approval_probe" in run and "--leg ci-bot" in run
        assert "|| true" not in run and "continue-on-error" not in yaml.safe_dump(job)

    def test_no_id_token_anywhere(self) -> None:
        assert "id-token" not in WORKFLOW.read_text(encoding="utf-8").replace("never add id-token", "")


class TestTerraform:
    def test_resource_mirrors_gate_with_no_literal_id(self) -> None:
        block = _tf_block()
        assert 'environment         = "deployment-approval-probe"' in block
        assert "users = var.gated_apply_reviewer_user_ids" in block
        assert "prevent_self_review = false" in block
        assert "protected_branches     = true" in block
        assert not re.search(r"\b\d{5,}\b", block)

    def test_never_an_oidc_subject(self) -> None:
        for root in ("bootstrap", "personal"):
            for path in (ROOT / "terraform" / root).rglob("*"):
                if path.is_file() and path.suffix in {".tf", ".tfvars", ".yaml", ".json"}:
                    assert "deployment-approval-probe" not in path.read_text(encoding="utf-8", errors="replace"), path
