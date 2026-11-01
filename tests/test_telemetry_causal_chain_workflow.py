"""telemetry-causal-chain.yml is the alarm-only 6-hourly production probe under the CI branch identity (T3.2).

It runs on a schedule and on dispatch (main only); never on push or pull_request (so never a required check, Decision 83);
never applies, deploys or dispatches another workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.convergence_health.sensor_liveness import cron_period_seconds

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "telemetry-causal-chain.yml"
_FORBIDDEN = ("terraform", "gh workflow", "/dispatches", "gh api", "update-function-code", "s3 cp", "put-parameter", "apply")


def _doc() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return dict(_doc()["jobs"]["probe"])


def _steps() -> list[dict[str, Any]]:
    return list(_job()["steps"])


def test_the_name_and_triggers() -> None:
    doc = _doc()
    assert doc["name"] == "telemetry-causal-chain" and len(doc["jobs"]) == 1
    trigger = doc["on"] if "on" in doc else doc[True]
    assert set(trigger) == {"schedule", "workflow_dispatch"}
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in " ".join(str(_job()["if"]).split())


def test_exactly_one_every_six_hours_cron() -> None:
    doc = _doc()
    (entry,) = (doc["on"] if "on" in doc else doc[True])["schedule"]
    assert entry["cron"] == "13 */6 * * *"
    assert cron_period_seconds(entry["cron"]) == 21600


def test_permissions_role_checkout_and_timeout() -> None:
    job = _job()
    assert _doc()["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"id-token": "write", "contents": "read", "actions": "read"}
    assert job["timeout-minutes"] == 40
    assert _doc()["concurrency"] == {"group": "telemetry-causal-chain", "cancel-in-progress": False}
    steps = _steps()
    checkout = steps[0]
    assert str(checkout["uses"]).startswith("actions/checkout") and checkout["with"]["fetch-depth"] == 0
    assert steps[1].get("uses") == "./.github/actions/aws-region"
    (assume,) = [s for s in steps if str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials")]
    assert assume["with"]["role-to-assume"].endswith("role/agent-platform-github-ci-branch")
    assert assume["env"] == {"MASK_AWS_ROLE_ID_ACCOUNT_PART": "${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}"}


def test_function_urls_and_hosts_are_resolved_and_masked_and_the_probe_is_run() -> None:
    runs = "\n".join(str(s.get("run", "")) for s in _steps())
    assert "for role in writer reader telemetry_writer telemetry_reader" in runs
    assert "::add-mask::${URL}" in runs and "::add-mask::${HOST%%/*}" in runs
    assert "DUCKLAKE_${role^^}_URL=${URL}" in runs
    assert ".venv/bin/python -m scripts.ducklake_smoke.lambda_telemetry_causal_chain" in runs
    (probe,) = [s for s in _steps() if "lambda_telemetry_causal_chain" in str(s.get("run", ""))]
    assert probe["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}


def test_no_deploy_or_dispatch_verbs() -> None:
    runs = "\n".join(str(s.get("run", "")) for s in _steps()).lower()
    assert not [word for word in _FORBIDDEN if word in runs]
