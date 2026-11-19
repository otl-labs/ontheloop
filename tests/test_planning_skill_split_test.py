"""rec-5390: the planning surfaces state one verifiable, revertible outcome per plan and split into child recs."""

import re
from pathlib import Path

from scripts.checks.deps.selection_budget import _DECLARE_OR_SPLIT

_AGENTS = Path("AGENTS.md")
_PLANNING = Path(".claude/skills/planning/SKILL.md")
_PLAN_CMD = Path(".claude/commands/plan.md")
_CRITIQUE = Path(".claude/skills/plan-critique/SKILL.md")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading) :], re.MULTILINE)
    end = start + len(heading) + nxt.start() if nxt else len(text)
    return _norm(text[start:end])


def _step4_item5() -> str:
    section = _section(_PLAN_CMD, "## Step 4:")
    start = section.index("5. Conduct a Complexity Assessment")
    return section[start:]


def _step8_split() -> str:
    section = _section(_PLAN_CMD, "## Step 8:")
    return section[section.index("**Planning-time split.**") :]


def test_complexity_assessment_names_the_outcome_rule() -> None:
    text = _section(_PLANNING, "## Complexity Assessment")
    for phrase in (
        "one outcome that can be verified and reverted on its own",
        "Never split on a file count",
        "never nested plan names",
        "Decision 247 cl.5",
    ):
        assert phrase in text, phrase
    assert "Scope files > 5" not in text


def test_step4_split_test_routes_mechanics_to_prs() -> None:
    text = _step4_item5()
    for phrase in (
        "at most three PRs merged in linear order",
        "until rec-5411 gives every PR the build gates",
        "child recs with an order edge",
        "A design fork is not a split",
        "differs in risk class",
        "another project's files",
        "is a guardrail, not the trigger",
    ):
        assert phrase in text, phrase


def test_named_verdicts_are_selection_budget_outcomes() -> None:
    text = _step4_item5()
    names = set(re.findall(r"`(within_budget|breadth_waived|non_test_breach|breach)`", text))
    assert {"breadth_waived", "breach"} <= names
    assert names <= set(_DECLARE_OR_SPLIT)


def test_step8_files_child_recs_and_keeps_the_parent_open() -> None:
    text = _step8_split()
    for phrase in (
        "--tags split-of-rec-NNNN",
        "--dependencies <its existing dependencies and every child>",
        "recs_by_ids",
        "Decision 247 cl.5",
        "plan no child",
        "chose split at a Step 9 escalation",
    ):
        assert phrase in text, phrase
    assert text.index("--file-rec") < text.index("--update-rec")
    assert "--status superseded" not in text


def test_step3_files_a_rec_for_work_with_no_rec() -> None:
    text = _section(_PLAN_CMD, "## Step 3:")
    for phrase in ("Work with no rec", "--file-rec", "the plan's primary rec"):
        assert phrase in text, phrase


def test_bundling_is_capped_at_two_beside_the_primary() -> None:
    text = _section(_PLANNING, "## Suggest Aligned Recommendations")
    assert "Bundle at most two beside the primary rec" in text
    assert "Decision 201" in text
    assert "include rec-XXX" not in text


def test_critique_template_offers_more_prs_or_child_recs() -> None:
    assert "more PRs, or child recs" in _norm(_CRITIQUE.read_text(encoding="utf-8"))


def test_no_live_planning_surface_offers_atomic_plans() -> None:
    for path in (_AGENTS, _PLANNING, _PLAN_CMD, _CRITIQUE):
        text = _norm(path.read_text(encoding="utf-8"))
        assert "atomic plans" not in text, path
        assert "atomic IMPLEMENTATION plans" not in text, path
    assert "one verifiable, revertible outcome" in _norm(_AGENTS.read_text(encoding="utf-8"))
