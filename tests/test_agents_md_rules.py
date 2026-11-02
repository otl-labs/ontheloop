"""Drift guards for the AGENTS.md rules added by plan agent-instruction-rules."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
CANARY = "[hook_canary]"


def test_canary_literal_named_in_agents_md() -> None:
    assert CANARY in AGENTS


def test_named_verdict_command_exists() -> None:
    assert "scripts.checks.validation_result --verify-head" in AGENTS
    source = (ROOT / "scripts" / "checks" / "validation_result.py").read_text(encoding="utf-8")
    assert '"--verify-head"' in source


def test_gh_not_claimed_uninstalled() -> None:
    assert "deliberately not installed" not in AGENTS
    assert "gh CLI may ship with the cloud image" in AGENTS
