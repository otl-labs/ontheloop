"""Structural mirror for the gated-apply drift guard (PLAN-t2-47-c2-pending-gated-ordering).

The guard step sits in gated-apply only, after apply and before the record writer; its output feeds
the writer's `refused` input fail-closed and the wake step's APPLY_OUTCOME. Also pins the contract
wiring: every marker the delegate writes is registered in the convergence marker vocabulary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(".github/workflows/terraform-apply-sandbox.yml")
TAXONOMY = Path("docs/contracts/environment-taxonomy.yaml")
DEPLOY_PATHS = Path("docs/contracts/deploy-paths.yaml")
_WAKE = "Best-effort post-merge SHA->PR wake"


def _steps(job: str) -> list[dict[str, Any]]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][job]["steps"]


def _index(steps: list[dict[str, Any]], key: str, value: str) -> int:
    return next(i for i, step in enumerate(steps) if step.get(key) == value)


def test_drift_guard_step_exists_in_gated_apply_only() -> None:
    gated = _steps("gated-apply")
    guard = gated[_index(gated, "id", "drift_guard")]
    assert guard["name"] == "Drift guard (refuse green over a post-route drift red)"
    assert "wake" not in guard["name"].lower()
    assert guard["if"] == "steps.apply.outcome == 'success'"
    assert "continue-on-error" not in guard
    assert "scripts.ci.convergence_classify --drift-guard" in guard["run"]
    assert "|| RC=$?" in guard["run"]
    for job in ("apply-sandbox",):
        assert all(step.get("id") != "drift_guard" for step in _steps(job))


def test_gated_apply_writer_refused_fed_by_drift_guard() -> None:
    gated = _steps("gated-apply")
    guard_idx = _index(gated, "id", "drift_guard")
    writer_idx = _index(gated, "id", "record_write")
    assert _index(gated, "id", "apply") < guard_idx < writer_idx
    refused = gated[writer_idx]["with"]["refused"]
    assert "steps.apply.outcome == 'success'" in refused
    assert "steps.drift_guard.outputs.refused != 'false'" in refused


def test_other_writers_are_untouched() -> None:
    sandbox = _steps("apply-sandbox")
    writer = sandbox[_index(sandbox, "id", "record_write")]
    assert writer["with"]["refused"] == "${{ steps.precond.outputs.refused }}"
    assert "drift_guard" not in WORKFLOW.read_text(encoding="utf-8").split("gated-apply:")[0]
    reconcile = Path(".github/workflows/reconcile.yml").read_text(encoding="utf-8")
    assert "drift_guard" not in reconcile


def test_wake_steps_call_delegate_and_gated_apply_reports_refusal() -> None:
    sandbox = _steps("apply-sandbox")
    gated = _steps("gated-apply")
    sandbox_wake = sandbox[_index(sandbox, "name", _WAKE)]
    gated_wake = gated[_index(gated, "name", _WAKE)]
    assert sandbox_wake["run"].strip() == "python3 -m scripts.ci.apply_wake --job apply-sandbox"
    assert gated_wake["run"].strip() == "python3 -m scripts.ci.apply_wake --job gated-apply"
    outcome = gated_wake["env"]["APPLY_OUTCOME"]
    assert "'refused'" in outcome and "steps.drift_guard.outputs.refused != 'false'" in outcome
    assert gated_wake["env"]["HEAD_SHA"] == "${{ github.sha }}"


def test_marker_vocabulary_registers_stale_gated_approval() -> None:
    vocabulary = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))["convergence_marker_vocabulary"]
    for marker in ("pending_gated", "pending_codification", "stale_gated_approval"):
        assert marker in vocabulary, f"{marker} is written by scripts.ci.convergence_classify but not registered"
    assert "routed_at_role" in vocabulary["pending_gated"]
    deploy_paths = yaml.safe_load(DEPLOY_PATHS.read_text(encoding="utf-8"))
    assert "stale_gated_approval" in str(deploy_paths["reconcile"]["heal_verb_selection"])
