"""The public summary holds only rule-named signatures and counts; the sheet lists every group, repeat run and tree."""

from __future__ import annotations

from datetime import date

from scripts.feedback_loop import friction, render
from tests.fixtures import feedback_loop_rows as fx

FIRST, LAST = date(2026, 11, 14), date(2026, 11, 20)


def result() -> friction.Result:
    rows = fx.recurring(3, 6) + fx.repeat_run(3, 6) + [fx.generic_row(9, 100)]
    for r in rows:
        r["workflow"] = "planted-workflow-zq"
    return friction.detect(rows, FIRST, LAST, calls=1)


def test_summary_prints_rule_named_signatures_and_hides_the_rest() -> None:
    text = render.summary_markdown(result())
    assert "hook:never_on_main" in text and render.NOT_PUBLISHED in text
    assert "sha256:" not in text and "run|" not in text
    for planted in (fx.PLANTED_TOOL, fx.PLANTED_SESSION, fx.PLANTED_ROOT, fx.PLANTED_BRANCH, "planted-workflow-zq"):
        assert planted not in text


def test_summary_without_findings_says_so() -> None:
    text = render.summary_markdown(friction.detect([], FIRST, LAST))
    assert "No findings" in text and "unmeasurable" in text


def test_sheet_lists_groups_examples_repeat_runs_and_the_tree_index() -> None:
    res = result()
    text = render.sheet_markdown(res)
    assert fx.PLANTED_TOOL in text and "right / wrong / should be specific" in text
    assert text.count("| process_event |") + text.count("| tool_call |") == len(res.groups)
    repeats = text.split("## Repeat runs, per tree")[1].split("## Tree index")[0]
    assert len([line for line in repeats.splitlines() if line.startswith("- T")]) == 3
    assert "T1" in text and "## Tree index" in text
    example_cells = [line for line in text.splitlines() if "hook:never_on_main" in line and "| r01 |" in line]
    assert example_cells and fx.PLANTED_BRANCH in example_cells[0]


def test_sheet_is_deterministic() -> None:
    assert render.sheet_markdown(result()) == render.sheet_markdown(result())
