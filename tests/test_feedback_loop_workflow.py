"""feedback-loop.yml: the daily propose-only friction detector run (PLAN-friction-daily-detector)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.convergence_health.sensor_liveness import cron_period_seconds

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "feedback-loop.yml"
_FORBIDDEN = (
    "terraform", "gh workflow", "/dispatches", "gh api", "update-function-code", "s3 cp", "put-parameter", "apply",
    "writer_url", "file_rec", "upload-artifact", "deploy",
)  # fmt: skip


def _doc() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return list(_doc()["jobs"]["friction"]["steps"])


def _index(predicate: Any) -> int:
    return next(i for i, step in enumerate(_steps()) if predicate(step))


def test_workflow_shape() -> None:
    doc = _doc()
    job = doc["jobs"]["friction"]
    assert doc["name"] == "feedback-loop" and len(doc["jobs"]) == 1
    trigger = doc.get("on", doc.get(True))
    assert set(trigger) == {"schedule", "workflow_dispatch"}
    (entry,) = trigger["schedule"]
    assert cron_period_seconds(entry["cron"]) == 86400 and entry["cron"].endswith("*/24 * * *")
    assert entry["cron"].split()[0] not in {"41", "43"}
    assert not trigger["workflow_dispatch"]
    condition = " ".join(str(job["if"]).split())
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in condition
    assert "otl-workflow-dispatcher[bot]" in condition
    assert job["timeout-minutes"] == 45
    assert job["permissions"] == {"id-token": "write", "contents": "read"} and doc["permissions"] == {"contents": "read"}
    assert doc["concurrency"] == {"group": "feedback-loop", "cancel-in-progress": False}


def test_step_order_role_and_masking() -> None:
    checkout = _index(lambda s: str(s.get("uses", "")).startswith("actions/checkout"))
    install = _index(lambda s: "pip install -r requirements.txt" in str(s.get("run", "")))
    assume = _index(lambda s: str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials"))
    resolve = _index(lambda s: "get-function-url-config" in str(s.get("run", "")))
    command = _index(lambda s: "scripts.feedback_loop friction" in str(s.get("run", "")))
    assert checkout < install < assume < resolve < command
    role = _steps()[assume]
    assert role["with"]["role-to-assume"].endswith("role/agent-platform-github-ci-branch")
    assert "role-session-name" not in role["with"]
    runs = "\n".join(str(s.get("run", "")) for s in _steps())
    assert "for role in reader telemetry_reader; do" in runs
    assert "::add-mask::${URL}" in runs and "::add-mask::${HOST}" in runs
    assert 'friction --summary "$GITHUB_STEP_SUMMARY"' in _steps()[command]["run"]
    assert "--json" not in runs and "--sheet" not in runs


def test_no_writer_filing_upload_or_deploy() -> None:
    body = yaml.safe_dump(_doc()["jobs"], sort_keys=True).lower()
    assert [word for word in _FORBIDDEN if word in body] == []
    assert "writer" not in body.replace("telemetry_reader", "")


def test_taxonomy_row_is_keyed_by_the_workflow_name() -> None:
    taxonomy = yaml.safe_load((ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    rows = next(v for v in taxonomy.values() if isinstance(v, dict) and "feedback-loop" in v)
    row = rows["feedback-loop"]
    assert (row["tier"], row["ci_rca"], row["owner"]) == ("not_a_gate", "excluded", "platform")
    assert _doc()["name"] == "feedback-loop"
