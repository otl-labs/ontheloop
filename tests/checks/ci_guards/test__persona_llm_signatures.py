"""_persona_llm_signatures: each LLM and compute-plane signature matched, comment-only mentions not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.ci_guards import _persona_llm_signatures as sig

LIVE = Path(_common.ROOT)
VARS = frozenset({"DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"})
IDS = frozenset({"implement_agent", "plan_agent"})


def _llm(step: dict[str, Any], root: Path = LIVE) -> bool:
    return sig.is_llm_step(step, root, VARS)


def test_variables_come_from_the_run_persona_secret_rows() -> None:
    assert sig.llm_secret_variables(LIVE) == VARS


def test_compute_plane_ids_come_from_the_index() -> None:
    assert sig.compute_plane_ids(LIVE) >= IDS


@pytest.mark.parametrize(
    "step",
    [
        {"env": {"DEEPSEEK_API_KEY": "x"}},
        {"with": {"k": "${{ secrets.ANTHROPIC_API_KEY }}"}},
        {"run": 'echo "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" | tool'},
        {"run": 'claude -p "hi"'},
        {"run": "claude --print hi"},
        {"run": "scripts/ci/claude_p_retry.sh --x"},
        {"run": "python -m scripts.run_persona --persona code_reviewer"},
        {"uses": "anthropics/claude-code-action@v1"},
    ],
)
def test_each_llm_signature_matches(step: dict[str, Any]) -> None:
    assert _llm(step)


@pytest.mark.parametrize(
    "step",
    [
        {"run": "# secrets.DEEPSEEK_API_KEY and claude -p are only named here\necho hi"},
        {"run": "echo hi  # run_persona in a trailing comment"},
        {"run": "npm install -g @anthropic-ai/claude-code\ncommand -v claude\nclaude --version"},
        {"run": "echo plan_agent"},
        {"uses": "actions/checkout@v4"},
    ],
)
def test_non_matches(step: dict[str, Any]) -> None:
    assert not _llm(step)


def test_quoted_hash_is_not_a_comment() -> None:
    assert sig.strip_comments("echo 'a # b' # c") == "echo 'a # b' "


def test_local_composite_action_is_resolved_one_hop(tmp_path: Path) -> None:
    action = tmp_path / ".github" / "actions" / "x"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        yaml.safe_dump({"runs": {"using": "composite", "steps": [{"run": "claude -p hi", "shell": "bash"}]}})
    )
    assert _llm({"uses": "./.github/actions/x"}, tmp_path)
    assert _llm({"uses": "./.dispatch-tooling/.github/actions/x"}, tmp_path)
    assert not _llm({"uses": "./.github/actions/missing"}, tmp_path)
    assert not _llm({"uses": "owner/repo/.github/actions/x@v1"}, tmp_path)


def test_reusable_workflow_is_resolved_one_hop(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "r.yml").write_text(yaml.safe_dump({"jobs": {"j": {"steps": [{"run": "claude -p hi"}]}}}))
    assert _llm({"uses": "./.github/workflows/r.yml"}, tmp_path)


@pytest.mark.parametrize(
    "run",
    [
        "aws ecs run-task --cluster c",
        "python -m x.persona_compute_host launch",
        "python -m scripts.run_persona --persona implement_agent",
        "python -m scripts.run_persona \\\n  --persona plan_agent",
    ],
)
def test_each_compute_plane_signature_matches(run: str) -> None:
    assert sig.starts_compute_plane({"run": run}, LIVE, IDS)


@pytest.mark.parametrize(
    "run",
    [
        "# aws ecs run-task\necho hi",
        "echo plan_agent is a persona",
        "python -m scripts.run_persona --persona code_reviewer",
        "aws ecs describe-tasks",
    ],
)
def test_compute_plane_non_matches(run: str) -> None:
    assert not sig.starts_compute_plane({"run": run}, LIVE, IDS)


def test_kind_branch_over_an_injected_index() -> None:
    injected = frozenset({"future_agent"})
    run = {"run": "python -m scripts.run_persona --persona future_agent"}
    assert sig.starts_compute_plane(run, LIVE, injected)
    assert not sig.starts_compute_plane(run, LIVE, IDS)


def test_kind_branch_matches_the_persona_id_not_the_registry_key() -> None:
    assert not sig.starts_compute_plane({"run": "python -m scripts.run_persona --persona planning"}, LIVE, IDS)


def test_compute_plane_one_hop(tmp_path: Path) -> None:
    action = tmp_path / ".github" / "actions" / "x"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        yaml.safe_dump({"runs": {"using": "composite", "steps": [{"run": "aws ecs run-task --c c", "shell": "bash"}]}})
    )
    assert sig.starts_compute_plane({"uses": "./.github/actions/x"}, tmp_path, IDS)


def test_unreadable_contract_yields_empty_sets(tmp_path: Path) -> None:
    assert sig.llm_secret_variables(tmp_path) == frozenset()
    assert sig.compute_plane_ids(tmp_path) == frozenset()


def test_live_tree_every_flagged_step_of_the_agent_safety_scan_is_an_llm_step() -> None:
    import scripts.check_workflow_agent_safety as safety

    for path in sorted(safety.WORKFLOWS_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in safety._iter_run_steps(doc) if isinstance(doc, dict) else []:
            if safety._CLAUDE_INVOCATION.search(step["run"]):
                assert _llm(step), f"{path.name}: {step.get('name')}"
