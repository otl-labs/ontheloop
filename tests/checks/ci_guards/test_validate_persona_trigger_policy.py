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


TERM = "github.event.workflow_run.event == 'pull_request'"


def _wf(name: str, on: str, steps: str = "      - run: echo\n", job_if: str = "") -> str:
    cond = f"    if: {job_if}\n" if job_if else ""
    return f"name: {name}\non:\n{on}\njobs:\n  j:\n{cond}    runs-on: x\n    steps:\n{steps}"


def _llm(step_if: str = "") -> str:
    cond = f"        if: {step_if}\n" if step_if else ""
    key = "        env:\n          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}\n        run: echo\n"
    return f"      - id: review\n{cond}{key}"


def _runs(upstream: str) -> str:
    return f"  workflow_run:\n    workflows: [{upstream}]"


DISPATCH = "  workflow_dispatch:"


def _problems(tmp_path: Path, files: dict[str, str]) -> list[str]:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, text in files.items():
        (wf / f"{name}.yml").write_text(text, encoding="utf-8")
    return v.check_tree(tmp_path, LIVE)[0]


def _chain(down: str, mid_on: str = DISPATCH, extra: dict[str, str] | None = None) -> dict[str, str]:
    files = {"down": down, "mid": _wf("Mid", mid_on)}
    files.update(extra or {})
    return files


def test_dispatch_upstream_admitted_when_every_llm_step_carries_the_term(tmp_path: Path) -> None:
    job = _wf("Down", _runs("Mid"), _llm(), job_if=f"${{{{ {TERM} }}}}")
    step = _wf("Down", _runs("Mid"), _llm(f"${{{{ {TERM} }}}}"))
    assert _problems(tmp_path / "a", _chain(job)) == []
    assert _problems(tmp_path / "b", _chain(step)) == []


def test_the_pinned_run_job_condition_with_cancelled_is_accepted(tmp_path: Path) -> None:
    cond = f"${{{{ !cancelled() && needs.prepare.result == 'success' && {TERM} }}}}"
    assert _problems(tmp_path, _chain(_wf("Down", _runs("Mid"), _llm(), job_if=cond))) == []


@pytest.mark.parametrize(
    "cond",
    [
        "",
        "github.event.workflow_run.event == 'push'",
        f"{TERM} || github.event.workflow_run.event == 'push'",
        f"always() && {TERM}",
        f"cancelled() && {TERM}",
    ],
)
def test_dispatch_upstream_refused_without_a_proper_term(tmp_path: Path, cond: str) -> None:
    down = _wf("Down", _runs("Mid"), _llm(), job_if=f"${{{{ {cond} }}}}" if cond else "")
    assert any("does not qualify" in p for p in _problems(tmp_path, _chain(down)))


def test_dispatch_upstream_refused_when_one_llm_step_lacks_the_term(tmp_path: Path) -> None:
    steps = _llm(f"${{{{ {TERM} }}}}") + _llm().replace("id: review", "id: other")
    assert any("does not qualify" in p for p in _problems(tmp_path, _chain(_wf("Down", _runs("Mid"), steps))))


def test_the_term_on_a_non_llm_step_does_not_count(tmp_path: Path) -> None:
    steps = _llm() + f"      - if: ${{{{ {TERM} }}}}\n        run: echo\n"
    assert any("does not qualify" in p for p in _problems(tmp_path, _chain(_wf("Down", _runs("Mid"), steps))))


def test_llm_down_non_llm_middle_dispatch_top_is_refused_then_admitted(tmp_path: Path) -> None:
    files = {"top": _wf("Top", DISPATCH), "mid": _wf("Mid", _runs("Top"))}
    bare = _wf("Down", _runs("Mid"), _llm())
    assert any("Top" in p or "top" in p for p in _problems(tmp_path / "a", {**files, "down": bare}))
    gated = _wf("Down", _runs("Mid"), _llm(f"${{{{ {TERM} }}}}"))
    assert _problems(tmp_path / "b", {**files, "down": gated}) == []


def test_a_three_hop_non_llm_chain_to_a_dispatch_is_refused(tmp_path: Path) -> None:
    files = {"top": _wf("Top", DISPATCH), "m1": _wf("M1", _runs("Top")), "mid": _wf("Mid", _runs("M1"))}
    assert _problems(tmp_path, {**files, "down": _wf("Down", _runs("Mid"), _llm())})


def test_an_llm_middle_cannot_pass_a_dispatch_to_an_ungated_llm_downstream(tmp_path: Path) -> None:
    mid = _wf("Mid", _runs("Top"), _llm(f"${{{{ {TERM} }}}}"))
    files = {"top": _wf("Top", DISPATCH), "mid": mid, "down": _wf("Down", _runs("Mid"), _llm())}
    problems = _problems(tmp_path, files)
    assert problems and any("down.yml" in p for p in problems)


def test_the_problem_list_is_deduplicated(tmp_path: Path) -> None:
    mid = _wf("Mid", _runs("Top"), _llm())
    files = {"top": _wf("Top", DISPATCH), "mid": mid, "down": _wf("Down", _runs("Mid"), _llm())}
    problems = _problems(tmp_path, files)
    assert len(problems) == len(set(problems))
    needle = "down.yml: upstream mid.yml does not qualify: mid.yml: upstream top.yml does not qualify"
    assert sum(p.startswith(needle) for p in problems) == 1
