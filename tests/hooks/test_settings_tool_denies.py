"""Startup-context trim: blanket denies for unused harness tools, and the guard that no workflow needs them."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
REMOTE = "mcp__claude-code-remote__"
REMOTE_TOOLS = (
    "create_environment",
    "update_environment",
    "get_environment",
    "switch_environment",
    "request_credential",
    "list_computer_folders",
    "request_computer_folder",
)
REQUIRED = frozenset(
    {"Artifact", "ArtifactComments", "ArtifactData", "mcp__Claude_Docs", *(REMOTE + name for name in REMOTE_TOOLS)}
)
SCAN_ROOTS = (
    ".claude/skills",
    ".claude/commands",
    ".claude/agents",
    ".claude/hooks",
    "docs/contracts",
    ".github/prompts",
    ".github/agents",
    "config/agent",
    "AGENTS.md",
    "CLAUDE.md",
    "**/CLAUDE.md",
)
ARTIFACT_REFERENCE = re.compile(r"`Artifact`|\bArtifact tool\b|\bArtifact\(")


def _permissions() -> dict[str, Any]:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]


def _tracked(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCAN_ROOTS],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    return [root / name for name in out.split("\0") if name]


def _patterns(denied: frozenset[str]) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for entry in sorted(denied):
        if entry == "Artifact":
            patterns.append(ARTIFACT_REFERENCE)
        elif entry == "mcp__Claude_Docs":
            patterns.append(re.compile(r"mcp__Claude_Docs(?![A-Za-z])"))
        elif entry.startswith(REMOTE):
            patterns.append(re.compile(rf"(?<![A-Za-z0-9_]){re.escape(entry)}(?![A-Za-z0-9_])"))
            patterns.append(re.compile(rf"(?<![A-Za-z0-9_]){re.escape(entry.removeprefix(REMOTE))}(?![A-Za-z0-9_])"))
        else:
            patterns.append(re.compile(rf"\b{re.escape(entry)}\b"))
    return patterns


def _references(root: Path, denied: frozenset[str]) -> list[str]:
    patterns = _patterns(denied)
    hits: list[str] = []
    for path in _tracked(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits.extend(f"{path.relative_to(root)}: {m.group(0)}" for p in patterns for m in p.finditer(text))
    return hits


class TestContextTrimDenies:
    def test_the_required_entries_are_denied_and_blanket(self) -> None:
        deny = _permissions()["deny"]
        assert REQUIRED <= set(deny)
        assert not [entry for entry in deny if entry in REQUIRED and "(" in entry]

    def test_no_workflow_surface_names_a_denied_tool(self) -> None:
        assert _references(ROOT, REQUIRED) == []

    def test_no_allow_entry_is_shadowed_by_a_deny_entry(self) -> None:
        permissions = _permissions()
        deny = permissions["deny"]
        shadowed = [
            allow
            for allow in permissions["allow"]
            for entry in deny
            if allow == entry or allow.startswith((entry + "__", entry + "("))
        ]
        assert shadowed == []

    def test_the_reference_scan_flags_a_workflow_that_names_a_denied_tool(self, tmp_path: Path) -> None:
        skill = tmp_path / ".claude" / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Call `Artifact` then mcp__claude-code-remote__get_environment.\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        hits = _references(tmp_path, REQUIRED)
        assert any("`Artifact`" in hit for hit in hits)
        assert any("get_environment" in hit for hit in hits)
