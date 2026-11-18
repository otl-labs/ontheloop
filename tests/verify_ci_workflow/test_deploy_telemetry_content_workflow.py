"""Wiring of deploy-telemetry-content.yml: the operator-gated content reader channel (Decision 239 cl.5, Decision 272)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-telemetry-content.yml"


def _doc() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict[str, Any]]:
    return _doc()["jobs"][job]["steps"]


def test_channel_wakes_on_completed_ci_on_main_and_dispatch_only() -> None:
    doc: dict[Any, Any] = _doc()
    trigger: Any = doc.get("on", doc.get(True))
    assert set(trigger) == {"workflow_run", "workflow_dispatch"}
    assert trigger["workflow_run"] == {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"force", "rebaseline"}
    assert all(i["type"] == "boolean" and i["default"] is False for i in trigger["workflow_dispatch"]["inputs"].values())
    assert doc["name"] == "deploy-telemetry-content"


def test_gate_calls_the_shared_gate_with_force_or_rebaseline() -> None:
    gate = _doc()["jobs"]["gate"]
    assert gate["uses"] == "./.github/workflows/deploy-gate.yml"
    assert gate["with"] == {"channel": "telemetry-content", "force": "${{ inputs.force || inputs.rebaseline || false }}"}
    assert "refs/heads/main" in gate["if"]


def test_build_has_no_environment_and_no_id_token() -> None:
    build = _doc()["jobs"]["build"]
    assert "environment" not in build and "id-token" not in build["permissions"]
    assert build["needs"] == "gate"


def test_deploy_is_held_gated_and_checks_approval_before_any_credential() -> None:
    deploy = _doc()["jobs"]["deploy"]
    assert deploy["needs"] == ["gate", "build"]
    assert deploy["if"] == "needs.build.outputs.held == 'false'"
    assert deploy["environment"] == "telemetry-content-deploy"
    steps = _steps("deploy")
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts[0]["with"]["ref"] == "${{ needs.gate.outputs.sha }}"
    assert checkouts[1]["with"]["path"] == ".dispatch-tooling"
    first_run = next(s for s in steps if "run" in s)
    assert first_run["run"].startswith("python3 -I scripts/ci/environment_reviewer_preflight.py approvals")
    assert first_run["working-directory"] == ".dispatch-tooling"


def test_record_needs_deploy_and_installs_before_it_assumes() -> None:
    record = _doc()["jobs"]["record"]
    assert record["needs"] == ["gate", "build", "deploy"] and record["if"] == "needs.deploy.result == 'success'"
    steps = record["steps"]
    install = next(i for i, s in enumerate(steps) if "pip install" in str(s.get("run", "")))
    assume = next(
        i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")
    )
    assert install < assume


def test_every_non_local_action_is_pinned_to_a_commit_sha() -> None:
    uses = [s["uses"] for job in _doc()["jobs"].values() for s in job.get("steps", []) if "uses" in s]
    third_party = [u for u in uses if not u.startswith("./")]
    assert third_party
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in third_party)
