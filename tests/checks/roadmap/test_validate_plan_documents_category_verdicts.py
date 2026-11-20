from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.roadmap import _plan_sidecar_approval
from scripts.checks.roadmap import validate_plan_documents as module
from scripts.checks.roadmap.validate_plan_documents import validate_plan_documents

_NAME = "PLAN-obligation-fixture.yaml"
_ROWS = [
    {"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS"},
    {"gate": "plan-critique", "round": 1, "verdict": "PROCEED"},
]


def _draft(title: str) -> str:
    env = "significance:\n  value: numbered_decision\n  justification: r.\nkind:\n  value: direction\n  justification: j.\n"
    return f"## Decision {{{{N}}}}: {title}\n\n```yaml\nnumber: {{{{N}}}}\n{env}```\n\n**Status:** Decided\n"


def _plan_with_linked_obligation() -> dict:
    return {
        "schema_version": 5,
        "handoff_policy": {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        "slug": "obligation-fixture",
        "intent": "Exercise the 12p category verdict leg.",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "plan_path": "docs/plans/PLAN-obligation-fixture.yaml",
        "phase": "test",
        "scope": [{"file": "docs/example.md", "action": "Modify", "purpose": "Docs."}],
        "acceptance_criteria": ["Covered."],
        "verification_plan": [
            {"step": 1, "phase": "pre-deploy", "action": "t", "command": "pytest x", "expected": "ok", "fix_if": "fix"}
        ],
        "execution_steps": ["Implement."],
        "gate_rounds": [dict(row) for row in _ROWS],
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    from scripts.roadmap import approved_text_provenance as prov

    approved = prov.Verdict(prov.APPROVED, "run 1 approver operator-fixture")
    monkeypatch.setattr(
        _plan_sidecar_approval, "sidecar_provenance", lambda slug, root, in_push, deadline: (approved, "enforce", None)
    )
    monkeypatch.setattr(module._plan_rescout_wiring, "run", lambda **_: ([], [], " re-scout barrier: stubbed"))


def _run(tmp_path: Path, capsys, *, added: set[str], sidecars: set[str] | None = None, named: bool = False):
    data = _plan_with_linked_obligation()
    data["context"] = [_draft(title="Drafted")]
    if named:
        data["gate_rounds"] = [
            *data["gate_rounds"][:-1],
            {**data["gate_rounds"][-1], "category_consistency": [{"entry": "Drafted", "verdict": "PASS"}]},
        ]
    (tmp_path / _NAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    failed: list[str] = []
    validate_plan_documents(failed, plans_dir=tmp_path, added_plan_names=added, changed_sidecar_slugs=sidecars)
    return failed, capsys.readouterr().out


def test_gap_fails_on_the_plan_pr_and_warns_elsewhere(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    failed, out = _run(tmp_path, capsys, added={_NAME})
    assert failed and "FAIL:" in out and "category_consistency" in out
    monkeypatch.delenv("GITHUB_EVENT_NAME")
    failed, out = _run(tmp_path, capsys, added={_NAME})
    assert not failed and "WARN:" in out and "category_consistency" in out


def test_a_named_title_is_clean(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    failed, out = _run(tmp_path, capsys, added={_NAME}, named=True)
    assert not failed and "FAIL" not in out


def test_changed_sidecar_triggers_a_modified_plan_and_an_untouched_plan_is_not_examined(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    failed, out = _run(tmp_path, capsys, added=set(), sidecars={"obligation-fixture"})
    assert failed and "category_consistency" in out
    failed, out = _run(tmp_path, capsys, added=set())
    assert "category_consistency" not in out
