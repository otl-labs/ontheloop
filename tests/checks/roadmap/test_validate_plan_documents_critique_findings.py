"""rec-5527: the plan-PR gate ends a v7 critique series on a PROCEED whose nits all carry a disposition."""

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

_NAME = "PLAN-findings-fixture.yaml"


@pytest.fixture(autouse=True)
def _pull_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    monkeypatch.setattr(_common, "status_diff_base", lambda root=None: sha)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setattr(wiring._plan_rescout_barrier, "barrier", lambda *a, **k: wiring._plan_rescout_barrier.BarrierResult())


def _finding(id_: str, severity: str, disposition: str) -> dict[str, str]:
    return {
        "id": id_,
        "severity": severity,
        "origin": "judgement",
        "anchor": "scope",
        "subject": "s",
        "disposition": disposition,
    }


def _plan(version: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "schema_version": version,
        "handoff_policy": {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        "slug": "findings-fixture",
        "intent": "Exercise the critique-ended rule.",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "plan_path": f"docs/plans/{_NAME}",
        "phase": "test",
        "cites": [],
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
        "gate_rounds": [{"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS"}, *rows],
    }
    return plan


def _validate(tmp_path: Path, plan: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> tuple[list[str], str]:
    (tmp_path / _NAME).write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    failed: list[str] = []
    module.validate_plan_documents(failed, plans_dir=tmp_path, added_plan_names={_NAME}, modified_plan_names=set())
    return failed, capsys.readouterr().out


def _proceed(*dispositions: str, round_: int = 1) -> dict[str, Any]:
    return {
        "gate": "plan-critique",
        "round": round_,
        "verdict": "PROCEED",
        "nits": len(dispositions),
        "findings": [_finding(f"N{i}", "nit", d) for i, d in enumerate(dispositions)],
    }


def test_v7_proceed_with_disposed_nits_ends_series(tmp_path: Path, capsys) -> None:
    failed, out = _validate(tmp_path, _plan(7, [_proceed("build-note", "applied-wording")]), capsys)
    assert failed == [], out


@pytest.mark.parametrize("bad", ["fixed", "disputed"])
def test_v7_proceed_with_a_fixed_or_disputed_nit_fails(tmp_path: Path, capsys, bad: str) -> None:
    failed, out = _validate(tmp_path, _plan(7, [_proceed("build-note", bad)]), capsys)
    assert failed and "must end with a plan-critique PROCEED" in out


def test_v7_confirming_round_after_a_proceed_with_nits_passes(tmp_path: Path, capsys) -> None:
    rows = [_proceed("fixed"), {"gate": "plan-critique", "round": 2, "verdict": "PROCEED", "findings": []}]
    failed, out = _validate(tmp_path, _plan(7, rows), capsys)
    assert failed == [], out


def test_v7_last_revise_fails_and_escalated_deferral_passes(tmp_path: Path, capsys) -> None:
    revise = {
        "gate": "plan-critique",
        "round": 1,
        "verdict": "REVISE",
        "blocking": 1,
        "findings": [_finding("B1", "blocking", "fixed")],
    }
    failed, out = _validate(tmp_path, _plan(7, [revise]), capsys)
    assert failed and "must end with a plan-critique PROCEED" in out
    deferred = {
        **revise,
        "escalated": True,
        "escalation_choice": "accept-with-deferral",
        "findings": [_finding("B1", "blocking", "deferred")],
    }
    failed, out = _validate(tmp_path, _plan(7, [deferred]), capsys)
    assert failed == [], out


def test_v6_keeps_the_old_rule(tmp_path: Path, capsys) -> None:
    row = {"gate": "plan-critique", "round": 1, "verdict": "PROCEED", "nits": 1}
    failed, out = _validate(tmp_path, _plan(6, [row]), capsys)
    assert failed
    assert "or the confirming round after a PROCEED listing nits" in out
