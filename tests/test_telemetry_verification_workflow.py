"""telemetry-verification.yml is an alarm-only daily CI-branch-identity monitor identity (PLAN-telemetry-verification-monitor).

It runs on a daily every-N-hours cron and on dispatch (main only); never on push; assumes the role after checkout and the
venv install so the 3,600 s role session covers the monitor; never applies, deploys or dispatches another workflow; never a
required check.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.convergence_health.sensor_liveness import cron_period_seconds
from scripts.telemetry_capture import read_route

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "telemetry-verification.yml"
_FORBIDDEN = ("terraform", "gh workflow", "/dispatches", "gh api", "update-function-code", "s3 cp", "put-parameter", "apply")


def _doc() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return _doc()["jobs"]["monitor"]


def _steps() -> list[dict[str, Any]]:
    return list(_job()["steps"])


def _index(predicate: Any) -> int:
    return next(i for i, step in enumerate(_steps()) if predicate(step))


def test_workflow_shape() -> None:
    doc = _doc()
    assert doc["name"] == "telemetry-verification" and len(doc["jobs"]) == 1
    trigger = doc.get("on", doc.get(True))
    assert set(trigger) == {"schedule", "workflow_dispatch"}
    (entry,) = trigger["schedule"]
    assert cron_period_seconds(entry["cron"]) == 86400 and entry["cron"].endswith("*/24 * * *")
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"quiet_day", "skip_trees"} and all(i["required"] is False for i in inputs.values())
    condition = " ".join(str(_job()["if"]).split())
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in condition
    assert _job()["timeout-minutes"] == 70
    assert _job()["permissions"] == {"id-token": "write", "contents": "read"} and doc["permissions"] == {"contents": "read"}
    assert doc["concurrency"] == {"group": "telemetry-verification", "cancel-in-progress": False}

    checkout = _index(lambda s: str(s.get("uses", "")).startswith("actions/checkout"))
    install = _index(lambda s: "pip install -r requirements.txt" in str(s.get("run", "")))
    assume = _index(lambda s: str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials"))
    resolve = _index(lambda s: "get-function-url-config" in str(s.get("run", "")))
    command = _index(lambda s: "scripts.telemetry_verify monitor" in str(s.get("run", "")))
    assert checkout < install < assume < resolve < command

    role = _steps()[assume]
    assert role["with"]["role-to-assume"].endswith("role/agent-platform-github-ci-branch")
    assert role["env"] == {"MASK_AWS_ROLE_ID_ACCOUNT_PART": "${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}"}
    assert role["with"]["aws-region"] == "${{ env.AWS_REGION }}" and "role-session-name" not in role["with"]

    runs = "\n".join(str(s.get("run", "")) for s in _steps())
    assert "for role in writer reader" in runs and "::add-mask::${URL}" in runs and "::add-mask::${HOST}" in runs
    assert "DUCKLAKE_${role^^}_URL=${URL}" in runs

    step = _steps()[command]
    assert step["env"] == {"QUIET_DAY": "${{ inputs.quiet_day }}", "SKIP_TREES": "${{ inputs.skip_trees }}"}
    assert "${{" not in step["run"]

    body = yaml.safe_dump(_doc()["jobs"], sort_keys=True).lower()
    assert [word for word in _FORBIDDEN if word in body] == []
    assert "--no-alarms" not in body


def test_taxonomy_row_is_keyed_by_the_workflow_name() -> None:
    taxonomy = yaml.safe_load((ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    rows = next(v for v in taxonomy.values() if isinstance(v, dict) and "telemetry-verification" in v)
    row = rows["telemetry-verification"]
    assert (row["tier"], row["ci_rca"], row["owner"]) == ("not_a_gate", "excluded", "platform")
    assert _doc()["name"] == "telemetry-verification"


def test_the_telemetry_reader_url_and_host_are_masked() -> None:
    (run,) = [str(s["run"]) for s in _steps() if "get-function-url-config" in str(s.get("run", ""))]
    loop = re.search(r"for role in ([a-z_ ]+); do", run)
    assert loop is not None and loop.group(1).split() == ["writer", "reader", "telemetry_reader"]
    assert '--function-name "agent-platform-ducklake-${role//_/-}"' in run
    written = run.index('>> "$GITHUB_ENV"')
    assert run.index('echo "::add-mask::${URL}"') < written and run.index('echo "::add-mask::${HOST}"') < written
    assert f"DUCKLAKE_{'telemetry_reader'.upper()}_URL" == read_route.READER_URL_ENV
