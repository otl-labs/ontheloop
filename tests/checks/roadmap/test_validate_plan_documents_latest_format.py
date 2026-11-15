"""rec-5102: a newly added plan declares the newest PlanDocument schema_version; the floor of 5 still binds the gates."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.roadmap import _plan_rescout_wiring as wiring
from scripts.checks.roadmap import validate_plan_documents as module

pytestmark = pytest.mark.unit

_NAME = "PLAN-format-fixture.yaml"


@pytest.fixture(autouse=True)
def _non_push_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    monkeypatch.setattr(_common, "status_diff_base", lambda root=None: sha)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(wiring._plan_rescout_barrier, "barrier", lambda *a, **k: wiring._plan_rescout_barrier.BarrierResult())


def _plan(version: int) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "schema_version": version,
        "handoff_policy": {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        "slug": "format-fixture",
        "intent": "Exercise the newest-version rule.",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "plan_path": f"docs/plans/{_NAME}",
        "phase": "test",
        "scope": [{"file": "tests/example.py", "action": "Modify", "purpose": "Change behavior."}],
        "acceptance_criteria": ["The behavior is covered."],
        "verification_plan": [
            {
                "step": 1,
                "phase": "pre-deploy",
                "action": "t",
                "command": "pytest tests/example.py",
                "expected": "ok",
                "fix_if": "r",
            }
        ],
        "execution_steps": ["Implement."],
        "gate_rounds": [
            {"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS"},
            {"gate": "plan-critique", "round": 1, "verdict": "PROCEED"},
        ],
    }
    if version >= 6:
        plan["cites"] = []
    return plan


def _validate(
    tmp_path: Path, plan: dict[str, Any], capsys: pytest.CaptureFixture[str], added: bool = True
) -> tuple[list[str], str]:
    (tmp_path / _NAME).write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    failed: list[str] = []
    module.validate_plan_documents(
        failed, plans_dir=tmp_path, added_plan_names={_NAME} if added else set(), modified_plan_names=set()
    )
    return failed, capsys.readouterr().out


def test_added_plan_below_newest_supported_version_is_refused(tmp_path: Path, capsys) -> None:
    newest = module._newest_schema_version()
    assert newest > module._MIN_NEW_PLAN_SCHEMA_VERSION
    failed, out = _validate(tmp_path, _plan(module._MIN_NEW_PLAN_SCHEMA_VERSION), capsys)
    assert failed == ["Plan document schema validation"]
    assert f"must declare schema_version {newest}" in out and f"got {module._MIN_NEW_PLAN_SCHEMA_VERSION}" in out
    assert "bump it, add cites, re-run the decision scout" in out


def test_added_plan_at_the_newest_version_passes_the_rule(tmp_path: Path, capsys) -> None:
    failed, out = _validate(tmp_path, _plan(module._newest_schema_version()), capsys)
    assert failed == [] and "must declare schema_version" not in out


def test_historical_plan_below_the_newest_is_not_refused(tmp_path: Path, capsys) -> None:
    failed, out = _validate(tmp_path, _plan(module._MIN_NEW_PLAN_SCHEMA_VERSION), capsys, added=False)
    assert failed == [] and "must declare schema_version" not in out


def test_added_plan_below_the_floor_still_fails_in_every_context(tmp_path: Path, capsys) -> None:
    failed, out = _validate(tmp_path, {**_plan(4), "gate_rounds": []}, capsys)
    assert failed and "newly-added plans must declare schema_version 5" in out


def test_modified_v5_plan_whose_sidecar_the_diff_changes_still_gets_the_wording_check_gap(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setattr(module, "_changed_sidecar_digest", lambda slug, base: "d" * 64)
    failed, out = _validate(tmp_path, _plan(5), capsys, added=False)
    assert failed and "wording-check rows with a PASS" in out and "must declare schema_version" not in out
