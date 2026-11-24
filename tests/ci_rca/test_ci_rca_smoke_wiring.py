"""rec-5493: the post-merge CI RCA smoke workflow is wired to prove the rows it flips."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci_rca import coverage

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SMOKE = "ci-rca-smoke.yml"


def _wf(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(wf: dict[str, Any]) -> dict[str, Any]:
    return wf["on"] if "on" in wf else wf[True]


def _step(wf: dict[str, Any], job: str, ident: str) -> dict[str, Any]:
    for index, step in enumerate(wf["jobs"][job]["steps"]):
        if (step.get("id") or step.get("name") or f"#{index}") == ident:
            return step
    raise AssertionError(f"{job}::{ident} not found")


def _max_turns(run: str) -> str:
    tokens = run.split()
    return tokens[tokens.index("--max-turns") + 1]


def test_smoke_workflow_proves_flipped_rows() -> None:
    cov = coverage.load()
    assert cov.check() == []
    rows = cov._smoke_rows()
    assert rows
    for key, row in rows:
        assert row["smoke_step"] in cov.smoke, key


def test_smoke_triggers_are_exactly_push_on_main() -> None:
    assert _on(_wf(SMOKE)) == {"push": {"branches": ["main"]}}


def test_smoke_agent_step_equals_the_ci_rca_agent_step_and_the_taxonomy_cap() -> None:
    rca = _step(_wf("ci-rca.yml"), "rca", "rca_agent")
    smoke = _step(_wf(SMOKE), "smoke", "rca_agent")
    assert smoke["run"] == rca["run"]
    assert smoke["timeout-minutes"] == rca["timeout-minutes"]
    taxonomy = yaml.safe_load((ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))["workflows"]
    caps = taxonomy["CI RCA Smoke"]["agent_loop_caps"]
    assert [c["value"] for c in caps if c["kind"] == "max_turns"] == [int(_max_turns(smoke["run"]))]
    assert _max_turns(rca["run"]) == _max_turns(smoke["run"])


@pytest.mark.parametrize(
    "name",
    ["Install Claude Code CLI", "Set up Python 3.12", "Activate venv", "Configure AWS credentials (OIDC -- branch role)"],
)
def test_setup_steps_equal_their_rca_partners(name: str) -> None:
    rca = _step(_wf("ci-rca.yml"), "rca", name)
    smoke = _step(_wf(SMOKE), "smoke", name)
    for field in ("run", "uses", "with", "env"):
        assert smoke.get(field) == rca.get(field), field


def test_smoke_sink_variables_appear_in_no_other_workflow() -> None:
    offenders = [
        p.name for p in WORKFLOWS.glob("*.yml") if p.name != SMOKE and "CI_RCA_SMOKE_" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_smoke_job_permissions_are_least_privilege() -> None:
    wf = _wf(SMOKE)
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["smoke"]["permissions"] == {"id-token": "write", "contents": "read"}
    assert "permissions" not in wf["jobs"]["select"]


def test_select_job_references_no_secret() -> None:
    assert "secrets." not in yaml.safe_dump(_wf(SMOKE)["jobs"]["select"])


def test_smoke_is_watched_by_ci_rca_and_the_taxonomy() -> None:
    assert "CI RCA Smoke" in _on(_wf("ci-rca.yml"))["workflow_run"]["workflows"]
    assert _wf(SMOKE)["name"] == "CI RCA Smoke"
    taxonomy = yaml.safe_load((ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))["workflows"]
    assert taxonomy["CI RCA Smoke"]["ci_rca"] == "watched"


def test_smoke_does_not_watch_itself_or_ci_rca() -> None:
    assert "workflow_run" not in _on(_wf(SMOKE))
    assert "CI RCA" not in _on(_wf("ci-rca.yml"))["workflow_run"]["workflows"]


def test_verify_step_env_arms_the_filing_parser() -> None:
    env = _step(_wf(SMOKE), "smoke", "verify")["env"]
    assert env["CI_RCA_SMOKE_SINK"] == "/tmp/ci-rca-smoke-sink.jsonl"
    assert env["CI_RCA_SMOKE_ID"] == "smoke-${{ github.run_id }}"


def test_agent_and_fallback_steps_write_to_separate_sinks_with_one_id() -> None:
    wf = _wf(SMOKE)
    agent, fallback = _step(wf, "smoke", "rca_agent")["env"], _step(wf, "smoke", "fallback")["env"]
    assert agent["CI_RCA_SMOKE_SINK"] != fallback["CI_RCA_SMOKE_SINK"]
    assert agent["CI_RCA_SMOKE_ID"] == fallback["CI_RCA_SMOKE_ID"]


def test_evidence_step_never_uploads_and_has_no_masking() -> None:
    step = _step(_wf(SMOKE), "smoke", "evidence")
    assert "--no-upload" in step["run"]
    assert "continue-on-error" not in step
    assert "|| true" not in step["run"]


def test_smoke_telemetry_capture_is_registered() -> None:
    capture = yaml.safe_load((ROOT / "config" / "telemetry" / "capture.yaml").read_text(encoding="utf-8"))
    assert {"workflow": f".github/workflows/{SMOKE}", "agent": "ci_rca", "content_policy": "full"} in capture["ci_workflows"]
