"""telemetry-golden-replay.yml is an alarm-only replay under the CI branch identity (PLAN-telemetry-verification-verbs).

It runs after every successful DuckLake deploy on main, once a day on main and on dispatch (main only); never on push; never
applies, deploys or dispatches another workflow; never a required check.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.convergence_health.sensor_liveness import cron_period_seconds
from scripts.telemetry_capture import read_route

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "telemetry-golden-replay.yml"
_FORBIDDEN = ("terraform", "gh workflow", "/dispatches", "gh api", "update-function-code", "s3 cp", "put-parameter", "apply")


def _doc() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return list(_doc()["jobs"]["replay"]["steps"])


def test_exactly_three_triggers_and_no_push() -> None:
    trigger = _doc().get("on", _doc().get(True))
    assert set(trigger) == {"workflow_run", "schedule", "workflow_dispatch"}
    assert trigger["workflow_run"] == {"workflows": ["deploy-ducklake-lambdas"], "types": ["completed"], "branches": ["main"]}
    condition = " ".join(str(_doc()["jobs"]["replay"]["if"]).split())
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in condition


def test_daily_cron_is_classified_as_86400_seconds() -> None:
    (entry,) = _doc()["on" if "on" in _doc() else True]["schedule"]
    assert re.fullmatch(r"\d{1,2} \*/24 \* \* \*", entry["cron"])
    assert cron_period_seconds(entry["cron"]) == 86400


def test_permissions_role_masking_and_checkout() -> None:
    job = _doc()["jobs"]["replay"]
    assert job["permissions"] == {"id-token": "write", "contents": "read"} and _doc()["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] == 30 and len(_doc()["jobs"]) == 1
    assert _doc()["concurrency"] == {"group": "telemetry-golden-replay", "cancel-in-progress": False}
    steps = _steps()
    checkout = next(
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/checkout")
        and "workflow_run.head_sha" in str(s.get("with", {}).get("ref", ""))
    )
    assert checkout["with"]["fetch-depth"] == 0 and "workflow_run.head_sha" in checkout["with"]["ref"]
    (assume,) = [s for s in steps if str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials")]
    assert assume["with"]["role-to-assume"].endswith("role/agent-platform-github-ci-branch")
    assert assume["env"] == {"MASK_AWS_ROLE_ID_ACCOUNT_PART": "${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}"}
    assert "role-session-name" not in assume["with"]

    # rec-4067: a default-ref checkout + the aws-region action run BEFORE the head_sha checkout,
    # since that checkout's commit may predate config/aws-region (as in ci-rca.yml).
    step0, step1, step2 = steps[0], steps[1], steps[2]
    step0_with = step0.get("with", {})
    assert str(step0.get("uses", "")).startswith("actions/checkout")
    assert "ref" not in step0_with and "path" not in step0_with
    assert step1.get("uses") == "./.github/actions/aws-region"
    assert str(step2.get("uses", "")).startswith("actions/checkout") and "workflow_run.head_sha" in str(step2["with"]["ref"])
    assert step2["with"]["fetch-depth"] == 0
    assert assume["with"]["aws-region"] == "${{ env.AWS_REGION }}"


def test_function_urls_are_resolved_masked_and_the_module_is_run() -> None:
    runs = "\n".join(str(s.get("run", "")) for s in _steps())
    assert "for role in writer reader telemetry_writer" in runs and "::add-mask::${URL}" in runs
    assert "DUCKLAKE_${role^^}_URL=${URL}" in runs
    assert ".venv/bin/python -m scripts.ducklake_smoke.lambda_telemetry_golden_gates" in runs
    assert "pip install -r requirements.txt" in runs


def test_it_never_applies_deploys_or_dispatches() -> None:
    body = yaml.safe_dump(_doc()["jobs"], sort_keys=True).lower()
    assert [word for word in _FORBIDDEN if word in body] == []
    assert "--no-alarms" not in body


def test_the_workflow_name_keys_the_taxonomy_row() -> None:
    taxonomy = yaml.safe_load((ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    rows = next(v for v in taxonomy.values() if isinstance(v, dict) and "telemetry-golden-replay" in v)
    row = rows["telemetry-golden-replay"]
    assert (row["tier"], row["ci_rca"]) == ("not_a_gate", "excluded") and _doc()["name"] == "telemetry-golden-replay"


def test_the_telemetry_reader_url_is_resolved_and_masked() -> None:
    (run,) = [str(s["run"]) for s in _steps() if "get-function-url-config" in str(s.get("run", ""))]
    loop = re.search(r"for role in ([a-z_ ]+); do", run)
    assert loop is not None and loop.group(1).split() == ["writer", "reader", "telemetry_writer", "telemetry_reader"]
    assert '--function-name "agent-platform-ducklake-${role//_/-}"' in run
    assert run.index('echo "::add-mask::${URL}"') < run.index('>> "$GITHUB_ENV"')
    assert f"DUCKLAKE_{'telemetry_reader'.upper()}_URL" == read_route.READER_URL_ENV
