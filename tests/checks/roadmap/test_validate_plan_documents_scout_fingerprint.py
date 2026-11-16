"""validate_plan_documents with the re-scout barrier running for real: only git state is pinned (rec-4490).

Sibling of test_validate_plan_documents.py, which sits at its SLOC cap (Decision 130) and stubs the barrier by name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.roadmap.validate_plan_documents import validate_plan_documents
from scripts.roadmap import plan_fingerprint as pf

pytestmark = pytest.mark.unit

_NAME = "PLAN-scout-fixture.yaml"
_STALE = "v1:" + "0" * 64
_CRITIQUED = (
    {"gate": "plan-critique", "round": 1, "verdict": "REVISE", "blocking": 1},
    {"gate": "plan-critique", "round": 2, "verdict": "PROCEED"},
)


def _plan(version: int = 6, value: str | None = _STALE) -> dict[str, Any]:
    scout: dict[str, Any] = {"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS"}
    if value is not None:
        scout["scout_input_fingerprint"] = value
    plan: dict[str, Any] = {
        "schema_version": version,
        "handoff_policy": {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        "slug": "scout-fixture",
        "intent": "Exercise the re-scout barrier through the plan check.",
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
        "gate_rounds": [scout, *_CRITIQUED],
    }
    if version >= 6:
        plan["cites"] = []
    return plan


@pytest.fixture
def head_sha(monkeypatch: pytest.MonkeyPatch) -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    monkeypatch.setattr(_common, "status_diff_base", lambda root=None: sha)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    return sha


def _validate(tmp_path: Path, plan: dict[str, Any], capsys: pytest.CaptureFixture[str], **kw: Any) -> tuple[list[str], str]:
    (tmp_path / _NAME).write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    failed: list[str] = []
    validate_plan_documents(
        failed, plans_dir=tmp_path, added_plan_names=kw.pop("added", {_NAME}), modified_plan_names=kw.pop("modified", set())
    )
    return failed, capsys.readouterr().out


def _matching(tmp_path: Path) -> str:
    (tmp_path / _NAME).write_text(yaml.safe_dump(_plan(), sort_keys=False), encoding="utf-8")
    plan = yaml.safe_load((tmp_path / _NAME).read_text(encoding="utf-8"))
    return pf.fingerprint(pf.projection(plan, pf.load_tree(_common.ROOT), None))


def test_stale_row_fails_on_a_pull_request_and_locally(
    tmp_path: Path, capsys, head_sha: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed, out = _validate(tmp_path, _plan(), capsys)
    assert failed == ["Plan document schema validation"] and "FAIL: re-scout required" not in out
    assert "re-scout required" in out and _NAME in out
    assert " re-scout barrier: 1 plan(s) examined, 1 compared, 1 failing" in out
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    failed, out = _validate(tmp_path, _plan(), capsys)
    assert failed and "re-scout required" in out


def test_stale_row_is_a_warning_in_push_context(
    tmp_path: Path, capsys, head_sha: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: head_sha)
    failed, out = _validate(tmp_path, _plan(), capsys)
    assert failed == [] and f"WARN: {_NAME}: re-scout required" in out


def test_matching_row_passes_and_prints_the_counts(tmp_path: Path, capsys, head_sha: str) -> None:
    value = _matching(tmp_path)
    failed, out = _validate(tmp_path, _plan(value=value), capsys)
    assert failed == [] and "re-scout required" not in out
    assert " re-scout barrier: 1 plan(s) examined, 1 compared, 0 failing" in out


def test_modified_plan_without_a_base_copy_is_compared_as_added(tmp_path: Path, capsys, head_sha: str) -> None:
    failed, out = _validate(tmp_path, _plan(), capsys, added=set(), modified={_NAME})
    assert failed and "re-scout required" in out


def test_plan_names_not_given_never_reach_git(tmp_path: Path, capsys, head_sha: str) -> None:
    failed, out = _validate(tmp_path, _plan(version=5, value=None), capsys, added=set(), modified=set())
    assert failed == [] and " re-scout barrier: no added or modified plan in this diff" in out


def test_empty_whole_diff_on_a_pull_request_fails(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "PLAN-x.yaml").write_text(
        yaml.safe_dump({**_plan(version=5, value=None), "slug": "x", "plan_path": "docs/plans/PLAN-x.yaml"}), encoding="utf-8"
    )
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    monkeypatch.setattr(_common, "get_status_aware_diff", lambda root=None: [])
    monkeypatch.setattr(_common, "status_diff_base", lambda root=None: "a" * 40)
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    failed: list[str] = []
    validate_plan_documents(failed)
    out = capsys.readouterr().out
    assert failed and "the PR diff lists no file" in out
