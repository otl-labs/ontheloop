"""Workflow-skill contract for the implementation-retrospective tools."""

from pathlib import Path


def test_implement_skill_documents_executable_ordered_closure() -> None:
    skill = Path(".claude/skills/implement/SKILL.md").read_text(encoding="utf-8")
    closure = skill[skill.index("### Run the full gate locally first") :]
    commands = (
        "bin/venv-python -m scripts.session.github_readiness",
        "bin/venv-python -m scripts.test_coverage_checker --check-tests --check-coverage",
        "bin/venv-python -m scripts.validate --pre",
        "bin/venv-python -m scripts.validate",
        "bin/venv-python -m scripts.session.postflight --evidence /tmp/implementation-retrospective.json",
    )
    positions = [closure.index(f"`{command}`") for command in commands]
    assert positions == sorted(positions)
    for required in (
        "unknown` is unproven, never PASS",
        "Validation Summary (scope: all)",
        "scripts.session.postflight_evidence.CATEGORIES",
        "evidence_failed",
        "real push/PR outcome",
    ):
        assert required in closure


def test_planning_and_critique_lock_handoff_semantics() -> None:
    planning = Path(".claude/skills/planning/SKILL.md").read_text(encoding="utf-8")
    critique = Path(".claude/skills/plan-critique/SKILL.md").read_text(encoding="utf-8")
    assert "full_validation_required_before_commit: true" in planning
    assert "timeout_disposition: blocked" in planning
    assert "mapped coverage or readiness as authoritative" in critique
    assert "real push/PR outcomes" not in critique
