"""Drift guards for the AGENTS.md rules added by plan agent-instruction-rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
CANARY = "[session_start_"
_SCRIPT_RE = re.compile(r"session_start_\w+\.(?:sh|py)")


def _session_start_scripts(config: Path) -> set[str]:
    data = json.loads(config.read_text(encoding="utf-8"))
    names: set[str] = set()
    for group in data.get("hooks", {}).get("SessionStart", []):
        for hook in group.get("hooks", []):
            names.update(_SCRIPT_RE.findall(hook.get("command", "")))
    return names


def test_canary_literal_named_in_agents_md() -> None:
    assert CANARY in AGENTS


def test_registered_session_start_scripts_print_the_canary() -> None:
    for config in (ROOT / ".claude" / "settings.json", ROOT / ".claude" / "hooks" / "hooks.json"):
        names = _session_start_scripts(config)
        assert names, f"{config} registers no session_start_* SessionStart hook"
        for name in sorted(names):
            text = (ROOT / ".claude" / "hooks" / name).read_text(encoding="utf-8")
            assert CANARY in text, f"{name} does not contain {CANARY!r}"


def test_named_verdict_command_exists() -> None:
    assert "scripts.checks.validation_result --verify-head" in AGENTS
    source = (ROOT / "scripts" / "checks" / "validation_result.py").read_text(encoding="utf-8")
    assert '"--verify-head"' in source


def test_gh_not_claimed_uninstalled() -> None:
    assert "deliberately not installed" not in AGENTS
    assert "gh CLI may ship with the cloud image" in AGENTS
