"""telemetry-arrival.yml is an alarm-only check that merged sessions arrived (PLAN-arrival-expectations, rec-4940).

Static shape: the three triggers and the same-repository main-push guard, a cron the liveness sweep accepts, scoped
permissions, the masked reader URL, the arrival invocation, the pytest-junit artifact ci-rca reads, and the ci-rca wiring.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.ci_rca import taxonomy
from scripts.convergence_health.sensor_liveness import cron_period_seconds
from scripts.telemetry_capture import read_route

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "telemetry-arrival.yml"
_FORBIDDEN = (
    "terraform",
    "gh workflow",
    "/dispatches",
    "gh api",
    "update-function-code",
    "s3 cp",
    "put-parameter",
    "file-rec",
)


def _doc() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict[str, Any]:
    return _doc()["jobs"]["arrival"]


def _steps() -> list[dict[str, Any]]:
    return list(_job()["steps"])


def _index(predicate: Any) -> int:
    return next(i for i, step in enumerate(_steps()) if predicate(step))


def test_triggers_and_job_guard() -> None:
    doc = _doc()
    assert doc["name"] == "telemetry-arrival" and list(doc["jobs"]) == ["arrival"]
    trigger = doc.get("on", doc.get(True))
    assert set(trigger) == {"workflow_run", "schedule", "workflow_dispatch"}
    assert trigger["workflow_run"] == {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}
    (entry,) = trigger["schedule"]
    assert entry["cron"] == "17 */12 * * *" and cron_period_seconds(entry["cron"]) == 43200
    guard = " ".join(str(_job()["if"]).split())
    assert "github.event_name == 'schedule'" in guard
    assert "github.event.workflow_run.event == 'push'" in guard
    assert "github.event.workflow_run.head_branch == 'main'" in guard
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in guard
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in guard


def test_permissions_concurrency_and_timeout_are_scoped() -> None:
    doc = _doc()
    assert doc["permissions"] == {"contents": "read"}
    assert _job()["permissions"] == {"id-token": "write", "contents": "read", "pull-requests": "read"}
    assert doc["concurrency"] == {"group": "telemetry-arrival", "cancel-in-progress": False}
    assert _job()["timeout-minutes"] == 30


def test_step_order_and_full_history_checkout() -> None:
    checkout = _index(lambda s: str(s.get("uses", "")).startswith("actions/checkout"))
    install = _index(lambda s: "pip install -r requirements.txt" in str(s.get("run", "")))
    assume = _index(lambda s: str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials"))
    resolve = _index(lambda s: "get-function-url-config" in str(s.get("run", "")))
    command = _index(lambda s: "scripts.telemetry_verify arrival" in str(s.get("run", "")))
    assert checkout < install < assume < resolve < command
    assert _steps()[checkout]["with"] == {"fetch-depth": 0}
    role = _steps()[assume]
    assert role["with"]["role-to-assume"].endswith("role/agent-platform-github-ci-branch")
    assert role["env"] == {"MASK_AWS_ROLE_ID_ACCOUNT_PART": "${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}"}


def test_the_reader_url_and_host_are_masked_and_secrets_stay_in_env() -> None:
    (run,) = [str(s["run"]) for s in _steps() if "get-function-url-config" in str(s.get("run", ""))]
    written = run.index('>> "$GITHUB_ENV"')
    assert run.index('echo "::add-mask::${URL}"') < written and run.index('echo "::add-mask::${HOST}"') < written
    assert "agent-platform-ducklake-telemetry-reader" in run and read_route.READER_URL_ENV in run
    for step in _steps():
        assert "${{" not in str(step.get("run", ""))
    body = yaml.safe_dump(_doc()["jobs"], sort_keys=True).lower()
    assert [word for word in _FORBIDDEN if word in body] == []


def test_the_run_step_invokes_arrival_with_a_junit_path() -> None:
    step = _steps()[_index(lambda s: "scripts.telemetry_verify arrival" in str(s.get("run", "")))]
    assert "--junit-xml reports/pytest-junit.xml" in step["run"] and "mkdir -p reports" in step["run"]
    assert step["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}


def test_the_junit_artifact_uploads_always_under_the_name_ci_rca_reads() -> None:
    step = _steps()[-1]
    assert step["name"] == "Upload pytest junit report (v2 fingerprint substrate)" and step["if"] == "always()"
    assert step["with"]["name"] == "pytest-junit" and step["with"]["path"] == "reports/pytest-junit.xml"
    assert step["with"]["retention-days"] == 14
    evidence = yaml.safe_load((ROOT / "docs/contracts/github-actions-evidence.yaml").read_text(encoding="utf-8"))
    rows = [r for r in evidence["artifact_uploads"] if r["workflow"] == ".github/workflows/telemetry-arrival.yml"]
    assert rows == [
        {
            "workflow": ".github/workflows/telemetry-arrival.yml",
            "job": "arrival",
            "step": step["name"],
            "artifact": "pytest-junit",
            "retention_class": "ordinary_ci",
        }
    ]


def test_ci_rca_watches_the_workflow_by_name() -> None:
    assert "telemetry-arrival" in (ROOT / ".github/workflows/ci-rca.yml").read_text(encoding="utf-8")
    ci_rca = yaml.safe_load((ROOT / ".github/workflows/ci-rca.yml").read_text(encoding="utf-8"))
    assert "telemetry-arrival" in ci_rca.get("on", ci_rca.get(True))["workflow_run"]["workflows"]
    row = taxonomy.load_taxonomy()["workflows"]["telemetry-arrival"]
    assert (row["tier"], row["ci_rca"], row["owner"]) == ("not_a_gate", "watched", "platform")
    assert _doc()["name"] == "telemetry-arrival"


def test_arrival_log_lines_classify_to_their_categories() -> None:
    tail = "lost_window=0 excused=0 pending=0 untrailered=1"
    red = f"ARRIVAL status=red judged=4 captured=2 missing=2 {tail}\nARRIVAL_MISSING sha=abc"
    unreadable = f"ARRIVAL status=unreadable judged=0 captured=0 missing=0 {tail}"
    assert taxonomy.classify_failure(red)[:2] == ("code_regression", "arrival_missing")
    assert taxonomy.classify_failure(unreadable)[:2] == ("environment", "arrival_unreadable")


def test_a_realistic_full_job_log_classifies_by_the_arrival_pattern() -> None:
    log = "\n".join(
        [
            "Run actions/checkout@v7",
            "Successfully installed pyyaml-6.0.2 boto3-1.40.0",
            "Resolved DUCKLAKE_TELEMETRY_READER_URL (masked).",
            "ARRIVAL status=red judged=38 captured=22 missing=1 lost_window=15 excused=0 pending=2 untrailered=3",
            "ARRIVAL_MISSING sha=9a4fd4bd383f",
            "##[error]Process completed with exit code 1.",
        ]
    )
    assert re.search(r"^ARRIVAL status=red", log, re.MULTILINE)
    assert taxonomy.classify_failure(log)[:2] == ("code_regression", "arrival_missing")
