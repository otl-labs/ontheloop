"""The hook canary evidence source: a UserPromptSubmit hook prints the line AGENTS.md tells agents to look for."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "hooks" / "hook_canary.sh"
LINE = "[hook_canary] repo hooks loaded"
PAYLOAD = '{"session_id":"s","hook_event_name":"UserPromptSubmit","prompt":"hi"}'


def _run(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)], input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def _registers_canary(config: Path) -> bool:
    data = json.loads(config.read_text(encoding="utf-8"))
    return any(
        "hook_canary.sh" in hook.get("command", "") and hook.get("type") == "command"
        for group in data.get("hooks", {}).get("UserPromptSubmit", [])
        for hook in group.get("hooks", [])
    )


def test_canary_reads_hook_evidence_not_context() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    bullet = next(b for b in agents.split("\n- ") if b.startswith("**Hook canary"))
    assert "`[hook_canary]`" in bullet and "[session_start_" not in bullet
    for config in (ROOT / ".claude" / "settings.json", ROOT / ".claude" / "hooks" / "hooks.json"):
        assert _registers_canary(config), f"{config} registers no UserPromptSubmit hook_canary.sh"
    printed = json.loads(_run(PAYLOAD).stdout)["hookSpecificOutput"]["additionalContext"]
    assert printed.startswith("[hook_canary]")


def test_hook_prints_one_user_prompt_submit_context_line() -> None:
    result = _run(PAYLOAD)
    assert result.returncode == 0
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": LINE}}


@pytest.mark.parametrize("stdin", ["", "not json {"])
def test_hook_ignores_its_input_and_still_exits_zero(stdin: str) -> None:
    result = _run(stdin)
    assert result.returncode == 0
    assert result.stdout == _run(PAYLOAD).stdout
