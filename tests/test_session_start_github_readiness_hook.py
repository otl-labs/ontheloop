from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_hook_order_and_advisory_execution() -> None:
    settings = json.loads(Path(".claude/settings.json").read_text(encoding="utf-8"))
    commands = [item["command"] for item in settings["hooks"]["SessionStart"][0]["hooks"]]
    assert "bash .claude/hooks/session_start_github_readiness.sh" in commands
    assert "bash .claude/hooks/session_start_ensure_github_mcp.sh" not in commands
    result = subprocess.run(
        ["bash", ".claude/hooks/session_start_github_readiness.sh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0
    assert "pr_write" in result.stdout
