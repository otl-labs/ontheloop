"""validate_persona_trigger_policy: red on each refused-branch fixture, green on the green fixtures and the live tree."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards import validate_persona_trigger_policy as v

FIXTURES = Path(__file__).parent / "fixtures" / "persona_trigger_policy"
LIVE = Path(_common.ROOT)
RED = sorted(p.name for p in (FIXTURES / "red").iterdir())
GREEN = sorted(p.name for p in (FIXTURES / "green").iterdir())

RED_EXPECT = {
    "trigger-schedule": "trigger 'schedule' is not allowed",
    "trigger-repository_dispatch": "trigger 'repository_dispatch' is not allowed",
    "trigger-pull_request_target": "trigger 'pull_request_target' is not allowed",
    "trigger-issue_comment": "trigger 'issue_comment' is not allowed",
    "trigger-workflow_call": "trigger 'workflow_call' is not allowed",
    "dispatch-ungated": "lacks a top-level persona_gate",
    "gate-behind-or": "has a || or a status function",
    "no-gate-step": "no earlier step persona_gate",
    "cancelled-status": "has a || or a status function",
    "continue-on-error-gate": "sets continue-on-error",
    "gate-in-other-job": "no earlier step persona_gate",
    "gate-if-differs": "differs from the LLM step's if:",
    "gate-after-llm": "no earlier step persona_gate",
    "gate-not-the-script": "does not run persona_dispatch_gate.py",
    "upstream-schedule-only-llm": "does not qualify",
    "upstream-failure-gated-llm-not-qualifying": "does not qualify",
    "upstream-dispatchable-deterministic-not-failure-gated": "non-failure-gated",
    "upstream-unresolved": "does not resolve to a workflow",
    "upstream-cycle": "cycle",
    "checks-write-top-level": "checks: write (top level)",
    "checks-write-job-level": "checks: write (job j)",
    "write-all": "checks: write (top level)",
    "ecs-run-task": "compute-plane",
    "compute-host-port": "compute-plane",
    "compute-persona-id": "compute-plane",
    "composite-ecs-one-hop": "compute-plane",
}


def _check(case: str, kind: str) -> list[str]:
    return v.check_tree(FIXTURES / kind / case, LIVE)[0]


def test_every_red_fixture_has_an_expectation() -> None:
    assert set(RED) == set(RED_EXPECT)


@pytest.mark.parametrize("case", RED)
def test_red_fixture_is_refused_for_its_branch(case: str) -> None:
    problems = _check(case, "red")
    assert any(RED_EXPECT[case] in p for p in problems), problems


@pytest.mark.parametrize("case", GREEN)
def test_green_fixture_passes(case: str) -> None:
    assert _check(case, "green") == []


def test_live_tree_is_green() -> None:
    problems, examined = v.check_tree(LIVE)
    assert problems == []
    assert examined >= 1


def test_live_floor_review_workflows_are_gated() -> None:
    workflows = v.load_workflows(LIVE / ".github" / "workflows")
    users = {
        f
        for f, d in workflows.items()
        if "workflow_dispatch" in v.triggers(d)
        and any("subagent-plan-review" in str(s.get("uses")) for _, _, s in v._steps(d))
    }
    assert users, "no dispatchable workflow uses subagent-plan-review"
    policy = v.Policy(LIVE, workflows, LIVE)
    for f in users:
        assert policy.gate_problems(f, workflows[f]) == [], f
        assert policy.llm_steps(workflows[f])


def test_registered_check_reports_failures_and_examined(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = v.check_tree
    monkeypatch.setattr(v, "check_tree", lambda root: orig(FIXTURES / "red" / "dispatch-ungated", LIVE))
    seen: list[tuple[int, str]] = []
    monkeypatch.setattr(v.registry, "examined", lambda n, unit="items": seen.append((n, unit)))
    failed: list[str] = []
    v.validate_persona_trigger_policy(failed)
    assert failed and "persona trigger policy" in failed[0]
    assert seen == [(1, "workflows")]


def test_registered_check_passes_on_green(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    orig = v.check_tree
    monkeypatch.setattr(v, "check_tree", lambda root: orig(FIXTURES / "green" / "gated-dispatch", LIVE))
    monkeypatch.setattr(v.registry, "examined", lambda n, unit="items": None)
    failed: list[str] = []
    v.validate_persona_trigger_policy(failed)
    assert failed == []
    assert "PASS" in capsys.readouterr().out


def test_check_is_registered() -> None:
    assert "validate_persona_trigger_policy" in registry.all_checks()


@pytest.mark.parametrize(
    ("expr", "terms"),
    [
        ("a && b", {"a", "b"}),
        ("${{ a && (b || c) }}", {"a", "b || c"}),
        ("(a && b)", {"a", "b"}),
        ("x == 'a && b' && y", {"x == 'a && b'", "y"}),
        (None, set()),
        (True, set()),
    ],
)
def test_and_terms(expr: object, terms: set[str]) -> None:
    assert v.and_terms(expr) == terms


def test_triggers_reads_the_true_key_spelling() -> None:
    assert v.triggers(yaml.safe_load("on: push\n")) == {"push"}
    assert v.triggers(yaml.safe_load("on: [push, workflow_dispatch]\n")) == {"push", "workflow_dispatch"}
    assert v.triggers({True: {"push": None}}) == {"push"}
    assert v.triggers({"jobs": {}}) == set()


def test_non_llm_workflow_run_upstream_is_followed(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "down.yml").write_text(
        "name: Down\non:\n  workflow_run:\n    workflows: [Mid]\njobs:\n  j:\n    runs-on: x\n    steps:\n"
        "      - id: review\n        env:\n          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}\n        run: echo\n",
        encoding="utf-8",
    )
    (wf / "mid.yml").write_text(
        "name: Mid\non:\n  workflow_run:\n    workflows: [Nowhere]\njobs:\n  j:\n    runs-on: x\n"
        "    steps:\n      - run: echo\n",
        encoding="utf-8",
    )
    problems, _ = v.check_tree(tmp_path, LIVE)
    assert any("Nowhere" in p for p in problems)


def test_load_workflows_tolerates_malformed_yaml(tmp_path: Path) -> None:
    (tmp_path / "bad.yml").write_text("a: [unclosed\n", encoding="utf-8")
    assert v.load_workflows(tmp_path) == {"bad.yml": {}}
