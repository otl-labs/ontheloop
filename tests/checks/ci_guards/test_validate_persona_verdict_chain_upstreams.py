"""The verdict chain upstreams and the pinned review-context step (sibling of test_validate_persona_verdict_chain.py)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.ci_guards import validate_persona_verdict_chain as v

LIVE = Path(_common.ROOT)
STEP_NAME = "Fetch the review context"


def _doc() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load((LIVE / v.VERDICT).read_text(encoding="utf-8")))


def _problems(edit: Callable[[dict[str, Any]], None] | None) -> list[str]:
    doc = _doc()
    if edit:
        edit(doc)
    evidence = yaml.safe_load((LIVE / v.EVIDENCE).read_text(encoding="utf-8"))
    return v.verdict_problems(doc, evidence)


def _on(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("on") or doc[True]


def _steps(doc: dict[str, Any], job: str = "prepare") -> list[dict[str, Any]]:
    return doc["jobs"][job]["steps"]


def _step(doc: dict[str, Any]) -> dict[str, Any]:
    (found,) = [s for s in _steps(doc) if s.get("name") == STEP_NAME]
    return found


def test_the_live_file_passes_with_three_upstreams() -> None:
    assert _problems(None) == []
    assert v.UPSTREAMS == ["persona-verdict-trigger", "persona-verdict-override", "CI"]
    assert _on(_doc())["workflow_run"]["workflows"] == v.UPSTREAMS
    assert "workflow_run.path" in v.VERDICT_GROUP


def test_three_upstreams_and_path_group_required() -> None:
    def drop_ci(doc: dict[str, Any]) -> None:
        _on(doc)["workflow_run"]["workflows"] = v.UPSTREAMS[:2]

    assert any("workflow_run does not name exactly" in p for p in _problems(drop_ci))

    def old_group(doc: dict[str, Any]) -> None:
        doc["concurrency"]["group"] = doc["concurrency"]["group"].replace("${{ github.event.workflow_run.path }}-", "")

    assert any("the concurrency group is not" in p for p in _problems(old_group))


def _move_after_materialise(doc: dict[str, Any]) -> None:
    steps = _steps(doc)
    step = steps.pop(steps.index(_step(doc)))
    steps.append(step)


def _move_before_route(doc: dict[str, Any]) -> None:
    steps = _steps(doc)
    step = steps.pop(steps.index(_step(doc)))
    steps.insert(0, step)


def _second_copy(doc: dict[str, Any]) -> None:
    _steps(doc).append(copy.deepcopy(_step(doc)))


def _other_job(doc: dict[str, Any]) -> None:
    step = _steps(doc).pop(_steps(doc).index(_step(doc)))
    _steps(doc, "wake").append(step)


def _no_condition(doc: dict[str, Any]) -> None:
    _step(doc).pop("if")


def _weaker_condition(doc: dict[str, Any]) -> None:
    _step(doc)["if"] = "steps.resolve.outputs.allowlisted == 'true'"


def _absent(doc: dict[str, Any]) -> None:
    _steps(doc).remove(_step(doc))


@pytest.mark.parametrize(
    ("name", "expect", "edit"),
    [
        ("absent", "exactly one review-context step", _absent),
        ("two", "exactly one review-context step", _second_copy),
        ("after-materialise", "not after route and before materialise", _move_after_materialise),
        ("before-route", "not after route and before materialise", _move_before_route),
        ("other-job", "runs in job wake", _other_job),
        ("no-condition", "step condition is not", _no_condition),
        ("weaker-condition", "step condition is not", _weaker_condition),
    ],
)
def test_review_context_step_is_pinned(name: str, expect: str, edit: Callable[[dict[str, Any]], None]) -> None:
    assert any(expect in p for p in _problems(edit)), (name, _problems(edit))
