"""The two capacity steps of convergence-health.yml and their evidence row (rec-5022)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.ci import actions_capacity_github as gh

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "convergence-health.yml"
EVIDENCE = ROOT / "docs" / "contracts" / "github-actions-evidence.yaml"
GATE = "${{ !cancelled() && github.ref == 'refs/heads/main' }}"
MEASURE = "Measure Actions capacity (rec-5022)"
UPLOAD = "Upload Actions capacity reading"
PRE_GAUGE_STEPS = [
    None,
    None,
    "Configure AWS credentials (OIDC -- github_ci_branch role)",
    "Set up Python 3.12",
    "Install Python dependencies (venv)",
    "Assess convergence health and escalate",
    "Detect DuckLake code drift (T2.38 / Decision 125/126)",
    "Detect prod-class code drift (T2.43 / Decision 125/126)",
    "Ingest CI fast-tier budget breaches (rec-3288)",
    "Assert scheduled loop liveness (LSA-02)",
]


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return _workflow()["jobs"]["health-check"]["steps"]


def test_capacity_steps_are_last_and_run_unless_cancelled() -> None:
    steps = _steps()
    assert [s.get("name") for s in steps[-2:]] == [MEASURE, UPLOAD]
    assert steps[-3]["name"] == "Assert scheduled loop liveness (LSA-02)"
    assert all(s["if"] == GATE for s in steps[-2:])
    assert "scripts.ci.actions_capacity_gauge --reading-out actions-capacity-reading.json" in steps[-2]["run"]
    assert steps[-2]["run"].splitlines()[0] == "set -euo pipefail"
    assert steps[-1]["uses"] == "actions/upload-artifact@v7"
    assert steps[-1]["with"]["if-no-files-found"] == "warn"


def test_upload_step_matches_evidence_contract() -> None:
    upload = _steps()[-1]
    rows = yaml.safe_load(EVIDENCE.read_text(encoding="utf-8"))
    row = next(r for r in rows["artifact_uploads"] if r.get("step") == UPLOAD)
    assert row["artifact"] == upload["with"]["name"] == "actions-capacity-reading"
    assert row["workflow"] == ".github/workflows/convergence-health.yml" and row["job"] == "health-check"
    cls = rows["retention_classes"][row["retention_class"]]
    assert upload["with"]["retention-days"] == cls["maximum_days"] == 14


def test_existing_steps_permissions_and_inputs_unchanged() -> None:
    wf = _workflow()
    job = wf["jobs"]["health-check"]
    steps = job["steps"][: len(PRE_GAUGE_STEPS)]
    assert [s.get("name") for s in steps] == PRE_GAUGE_STEPS
    assert job["permissions"] == {"id-token": "write", "contents": "read", "actions": "read"}
    assert job["timeout-minutes"] == 10 == gh.JOB_TIMEOUT_MINUTES
    assert list(wf[True]["workflow_dispatch"]["inputs"]) == ["diagnose"]
    assert wf[True]["schedule"] == [{"cron": "37 * * * *"}]
    assert wf["permissions"] == {"contents": "read"}


def test_header_states_gauge_alarm_only_invariants() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    header = text.split("\non:\n", 1)[0]
    assert "rec-5022" in header and "docs/contracts/actions-capacity.yaml" in header
    assert "actions: read" in header and "GH_TOKEN" in header
    assert "no workflow_dispatch, no workflow, plan or runner change" in " ".join(header.replace("#", " ").split())
    assert "red only on a portal write failure" in " ".join(header.replace("#", " ").split())
    assert "never a required check" in " ".join(header.replace("#", " ").split())
