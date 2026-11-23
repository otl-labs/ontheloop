"""rec-5166's acceptance: the credentialed smoke runs as a CI job in the persona-verdict Environment, not from a session."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from botocore.exceptions import ClientError

from scripts import smoke_test_inference_credentials as smoke
from scripts.llm import tier_selection

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/persona-verdict.yml"
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '


def _smoke_job() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["smoke"]


def _committed_roles() -> list[str]:
    inference, _ = tier_selection.load()
    tiers = inference["tier_model"]["tiers"]
    return [t["role"] for t in tiers if not str(t.get("state", "")).startswith("Deferred") and t["role"] in smoke._ROLES]


def test_credentialed_run_is_a_ci_job() -> None:
    job = _smoke_job()
    assert job["environment"] == "persona-verdict"
    assert job["permissions"]["id-token"] == "write"
    steps = [s for s in job["steps"] if str(s.get("run", "")).startswith(RUN_PREFIX + "inference-smoke")]
    assert sorted(s["env"]["PERSONA_SMOKE_ROLE"] for s in steps) == sorted(_committed_roles())
    condition = str(job["if"])
    for conjunct in (
        "github.event.workflow_run.event == 'pull_request'",
        "needs.prepare.outputs.allowlisted == 'true'",
        "needs.prepare.outputs.fixture == 'true'",
    ):
        assert conjunct in condition


def test_the_smoke_module_names_ci_as_the_credentialed_route() -> None:
    doc = smoke.__doc__ or ""
    assert "agent_platform profile in CC-web" not in doc
    assert "inference-smoke" in doc and "CloudShell" in doc


def test_a_failure_line_never_prints_exception_text_that_could_hold_an_arn(capsys: pytest.CaptureFixture[str]) -> None:
    arn = "arn:aws:iam::123:role/x"
    error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": f"not allowed on {arn}"}}, "GetParameter")
    session = _Session(error)
    assert smoke.run("primary", region="xx-test-1", session=session) == 1
    err = capsys.readouterr()
    assert "arn:aws" not in err.err + err.out and "AccessDeniedException" in err.err


class _Session:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def client(self, name: str, region_name: str) -> Any:
        error = self.error

        class _Client:
            def get_parameter(self, **kw: Any) -> Any:
                raise error

        return _Client()
