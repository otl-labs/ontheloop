"""Deploy-gate wiring (Decision 249): both Lambda code-deploy channels wake on completed CI runs, call the shared
deploy-gate.yml, and run every job at the gate's sha; the golden replay and the drift sensor follow the same gate."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from scripts.ci.deploy_gate import FULL_TIER_JOB, FULL_TIER_STEP
from scripts.convergence_health.code_drift import DUCKLAKE_SOURCE_PATHSPECS, PROD_SOURCE_PATHSPECS
from scripts.verify_ci_workflow._shared import FULL_STEP_IF

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_CHANNELS = {
    "deploy-ducklake-lambdas.yml": ("ducklake", DUCKLAKE_SOURCE_PATHSPECS),
    "deploy-prod-lambdas.yml": ("prod", PROD_SOURCE_PATHSPECS),
}
_SHA = "${{ needs.gate.outputs.sha }}"
_DELETED_PUSH_GLOBS = {
    "deploy-ducklake-lambdas.yml": [
        "src/lambdas/ducklake_*/**",
        "src/common/ducklake_*.py",
        "config/lambda/ducklake/**",
        "src/row_rules/**",
        "src/telemetry/**",
    ],
    "deploy-prod-lambdas.yml": [
        "src/data/handlers/**",
        "scripts/aws_profile.py",
        "scripts/llm/github_models_client.py",
        "scripts/llm/client.py",
        "scripts/llm/utils.py",
        "scripts/run_scheduled_agent.py",
        "scripts/s3_log_store.py",
        "scripts/tool_runtime.py",
        "config/lambda/data-pipeline/**",
    ],
}


def _load(name: str) -> dict[Any, Any]:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(doc: dict[Any, Any]) -> dict[str, Any]:
    return doc.get("on", doc.get(True))


def _watched(path: str, pathspecs: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") or fnmatch(path, p) for p in pathspecs)


def _sample(glob: str) -> str:
    return glob.replace("/**", "/probe.py").replace("*", "x")


def test_channels_wake_on_completed_ci_and_dispatch_only() -> None:
    for name in _CHANNELS:
        trigger = _on(_load(name))
        assert set(trigger) == {"workflow_run", "workflow_dispatch"}, name
        assert trigger["workflow_run"] == {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}
        force = trigger["workflow_dispatch"]["inputs"]["force"]
        assert force["type"] == "boolean" and force["default"] is False


def test_gate_job_calls_the_shared_workflow_with_the_right_guards() -> None:
    for name, (channel, _) in _CHANNELS.items():
        doc = _load(name)
        gate = doc["jobs"]["gate"]
        assert gate["uses"] == "./.github/workflows/deploy-gate.yml" and gate["with"]["channel"] == channel
        assert gate["with"]["force"] == "${{ inputs.force || false }}"
        assert gate["secrets"] == "inherit"  # pragma: allowlist secret
        assert gate["permissions"] == {"id-token": "write", "contents": "read", "actions": "read"}
        assert doc["permissions"]["actions"] == "read"
        condition = " ".join(str(gate["if"]).split())
        assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in condition
        for term in (
            "github.event.workflow_run.conclusion == 'success'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.head_repository.full_name == github.repository",
        ):
            assert term in condition


def test_every_other_job_needs_gate_and_checks_out_the_gate_sha() -> None:
    for name in _CHANNELS:
        for job_id, job in _load(name)["jobs"].items():
            if job_id == "gate":
                continue
            needs = [job["needs"]] if isinstance(job["needs"], str) else job["needs"]
            assert "gate" in needs, (name, job_id)
            checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
            assert checkouts and checkouts[-1].get("with", {}).get("ref") == _SHA, (name, job_id)
            assert all("ref" not in c.get("with", {}) for c in checkouts[:-1]), (name, job_id)


def test_root_jobs_are_conditioned_on_the_gate_deciding_to_deploy() -> None:
    ducklake = _load("deploy-ducklake-lambdas.yml")["jobs"]
    for job_id in ("deploy-maintenance", "reconcile-gate"):
        assert ducklake[job_id]["if"] == "needs.gate.outputs.deploy == 'true'"
    assert _load("deploy-prod-lambdas.yml")["jobs"]["deploy"]["if"] == "needs.gate.outputs.deploy == 'true'"
    assert set(_load("deploy-prod-lambdas.yml")["jobs"]["smoke"]["needs"]) == {"gate", "deploy"}


def test_deploy_jobs_record_the_gate_sha() -> None:
    for name in _CHANNELS:
        for job_id, job in _load(name)["jobs"].items():
            if job_id.startswith("deploy-") and job_id != "deploy-gate" or job_id == "deploy":
                assert job["env"]["LAMBDA_ARTIFACT_SHA"] == _SHA, (name, job_id)


def test_github_sha_is_gone_from_both_channel_files() -> None:
    for name in _CHANNELS:
        assert "github.sha" not in (_WORKFLOWS / name).read_text(encoding="utf-8"), name


def test_freshness_asserts_are_gate_calls() -> None:
    for name, (channel, _) in _CHANNELS.items():
        runs = [s["run"] for j in _load(name)["jobs"].values() for s in j.get("steps", []) if "freshness" in s.get("run", "")]
        assert runs and all(f'deploy_gate freshness --channel {channel} --expected-sha "{_SHA}"' in r for r in runs)


def test_deploy_gate_is_workflow_call_only_with_outputs_and_manifest() -> None:
    doc = _load("deploy-gate.yml")
    trigger = _on(doc)
    assert set(trigger) == {"workflow_call"}
    assert trigger["workflow_call"]["inputs"]["channel"]["required"] is True
    assert trigger["workflow_call"]["inputs"]["force"]["default"] is False
    assert set(trigger["workflow_call"]["outputs"]) == {"sha", "deploy", "reason"}
    steps = doc["jobs"]["gate"]["steps"]
    resolve = next(s for s in steps if s.get("id") == "resolve")
    assert "inputs.force && '--force' || ''" in resolve["run"] and "FORCE" not in resolve["run"]
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["with"]["name"] == "deploy-manifest" and upload["if"] == "always()" and upload["with"]["retention-days"] == 7
    assert next(s for s in steps if s.get("id") == "assume")["env"] == {
        "MASK_AWS_ROLE_ID_ACCOUNT_PART": "${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}"
    }


def test_ci_still_has_the_full_tier_step_the_gate_matches() -> None:
    job = _load("ci.yml")["jobs"][FULL_TIER_JOB]
    (step,) = [s for s in job["steps"] if s.get("name") == FULL_TIER_STEP]
    assert step["run"].strip() == "python -m scripts.validate" and step["if"] == FULL_STEP_IF


def test_convergence_health_drift_steps_carry_a_token() -> None:
    steps = _load("convergence-health.yml")["jobs"]["health-check"]["steps"]
    drift = [s for s in steps if "--ducklake-drift" in s.get("run", "") or "--prod-drift" in s.get("run", "")]
    assert len(drift) == 2 and all(s["env"]["GH_TOKEN"] == "${{ github.token }}" for s in drift)


def test_golden_replay_follows_the_deploying_run() -> None:
    jobs = _load("telemetry-golden-replay.yml")["jobs"]
    assert jobs["replay"]["needs"] == "decision" and "concurrency" in jobs["replay"]


def test_every_deleted_push_glob_is_still_watched_and_plans_are_not() -> None:
    for name, (_, pathspecs) in _CHANNELS.items():
        for glob in _DELETED_PUSH_GLOBS[name]:
            assert _watched(_sample(glob), pathspecs), (name, glob)
        assert not _watched("docs/plans/PLAN-x.yaml", pathspecs)
