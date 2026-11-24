"""Pins the Step 9 critique-loop rules: first PROCEED ends the loop, build notes, serial-discovery escalation (rec-5528)."""

from pathlib import Path

_PLANNING = Path(".claude/skills/planning/SKILL.md")
_PLAN_CMD = Path(".claude/commands/plan.md")
_IMPLEMENT_CMD = Path(".claude/commands/implement.md")
_DISPATCH = Path("docs/contracts/overseer-dispatch.yaml")


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _step9() -> str:
    text = _flat(_PLANNING)
    start = text.index("**Convergence rule -- findings, not rounds:** Every round")
    return text[start : text.index("## Report Critique Gate")]


def test_first_proceed_ends_the_loop_with_nit_dispositions() -> None:
    rule = _step9()
    assert "The first PROCEED ends the loop, no confirming round" in rule
    assert "(applied-wording)" in rule
    assert (
        "every other nit (a scope file, a command, a selector, an expected literal, a number or more) is never "
        "edited in unread but recorded build-note, its subject the instruction /implement reads"
    ) in rule
    assert rule.replace("no confirming round", "").count("confirming round") == 0


def test_serial_discovery_and_round_six_escalate() -> None:
    rule = _step9()
    assert (
        "a third blocking finding on one plan row (the anchor up to its first `]`) or sidecar block, or a REVISE "
        "at the sixth round, in one series"
    ) in rule
    assert "(one more round when plan-critique 5c applies)" in rule


def test_revisit_clause_counts_finding_lists() -> None:
    rule = _step9()
    assert (
        "Revisit when 20 plans carry findings lists: a 5c-class finding first raised at round 4 or later, or a plan "
        "amendment caused by a build-note nit, reopens this rule and plan-critique's blocking rule."
    ) in rule
    assert "did a confirming round ever find" not in _flat(_PLANNING)


def test_critique_dispatch_asks_for_finding_ids() -> None:
    text = _flat(_PLANNING)
    start = text.index("## Critique Gate (Workflow Step 9)")
    body = text[start : text.index("**Convergence rule -- findings", start)]
    assert "Give each finding a short unique id. Return the skill's structured critique output verbatim" in body


def test_plan_command_and_dispatch_contract_mirror_the_first_proceed_rule() -> None:
    plan_cmd = _flat(_PLAN_CMD)
    assert (
        "the first PROCEED ends the loop with no confirming round, its wording nits applied and its other nits "
        "recorded as build notes; after applying them, recompute `plan_fingerprint` and re-run the Decision Scout "
        "if it moved"
    ) in plan_cmd
    assert "a third blocking finding on one plan row (the anchor up to its first `]`)" in plan_cmd
    assert "ONE fresh confirming round" not in plan_cmd
    dispatch = _flat(_DISPATCH)
    assert "Step 9 loop (the first PROCEED, its nits disposed)" in dispatch
    assert "On REVISE: the subagent fixes and re-requests" in dispatch
    assert "or the confirming round's" not in dispatch


def test_implement_reads_build_notes() -> None:
    text = _flat(_IMPLEMENT_CMD)
    assert "`disposition: build-note`" in text
    assert "any `gate: plan-critique` row" in text
    assert "skipped and named in the handoff" in text
    assert "only when the build cannot finish without it" in text
