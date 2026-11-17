"""Structural pins on ci-rca.yml's range step (Decision 254 cl.4, Decision 162)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci-rca.yml"
STEPS: list[dict[str, Any]] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["rca"]["steps"]
MODULE_GUARD = "hashFiles('scripts/ci_rca/range_context.py')"


def step(step_id: str) -> dict[str, Any]:
    return next(s for s in STEPS if s.get("id") == step_id)


def index(step_id: str) -> int:
    return STEPS.index(step(step_id))


def test_range_step_runs_resolve_between_meta_and_evidence() -> None:
    assert index("meta") < index("range") < index("evidence")
    run = step("range")["run"]
    resolve = next(line for line in run.splitlines() if "range_context resolve" in line)
    assert "scripts.ci_rca.range_context resolve" in resolve
    assert ">>" not in resolve and "GITHUB_OUTPUT" not in resolve and "> /tmp/ci-rca-range.json" not in resolve


def test_range_step_guards_and_failure_containment() -> None:
    s = step("range")
    assert "workflow_slug == 'ci'" in s["if"] and f"{MODULE_GUARD} != ''" in s["if"]
    assert s["continue-on-error"] is True and s["timeout-minutes"] == 3
    assert "GH_TOKEN" in s["env"]
    assert "if " not in s["run"] and "$(" not in s["run"]


def test_range_fallback_is_the_exact_complement_and_writes_head_caret() -> None:
    s = step("range_fallback")
    assert s["if"] == f"steps.meta.outputs.workflow_slug != 'ci' || {MODULE_GUARD} == ''"
    assert "diff_base=HEAD^" in s["run"]


def test_evidence_step_reads_range_prev() -> None:
    expr = "${{ steps.range.outputs.diff_base || steps.range_fallback.outputs.diff_base }}"
    assert step("evidence")["env"]["CI_RCA_RANGE_PREV"] == expr


def test_agent_prompt_ends_with_range_note() -> None:
    s = step("rca_agent")
    assert s["env"]["RANGE_NOTE"] == "${{ steps.range.outputs.prompt_note }}"
    prompt = next(line for line in s["run"].splitlines() if line.strip().startswith("PROMPT="))
    assert prompt.rstrip().endswith('${RANGE_NOTE}"')


def test_manifest_and_dedup_anchors_unchanged() -> None:
    manifest = next(s for s in STEPS if "selection-manifest" in s.get("name", ""))
    assert "anchor_sha" in manifest["run"] or "anchor_sha" in str(manifest.get("env", ""))
    assert "steps.meta.outputs.anchor_sha" in step("dedup")["run"]
