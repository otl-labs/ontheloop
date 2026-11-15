#!/usr/bin/env python3
"""PreToolUse hook: restrict mutation tools to permitted paths when running as a scheduled agent.

Activated when CC_SCHEDULED_AGENT_NAME env var is set. In normal interactive
sessions the env var is absent and the hook exits 0 (fully inert).

When active, permits mutation tools only to:
  logs/agents/{agent_name}/    -- findings output directory

Bash tool calls always exit 0. The --allowedTools list at the claude -p
invocation level enforces Bash restrictions.

Behaviour:
  exit 0: allow tool invocation
  exit 2: block tool invocation (stderr message shown to agent)

Testing:
  Set CC_HOOK_AGENT_OVERRIDE=1 to bypass the hook for local testing without
  unsetting CC_SCHEDULED_AGENT_NAME.

Rule HB-1: every block message ends with its own versioned marker line; src/turn_capture/markers.py holds the grammar.
"""

from __future__ import annotations

import json
import os
import sys

_MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _normalize(path: str) -> str:
    """Normalize path separators to forward slashes for cross-platform comparison."""
    return path.replace("\\", "/")


def main() -> int:
    agent_name = os.environ.get("CC_SCHEDULED_AGENT_NAME")
    if not agent_name:
        return 0

    if os.environ.get("CC_HOOK_AGENT_OVERRIDE"):
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool_name not in _MUTATING_TOOLS:
        return 0

    file_path_str = tool_input.get("file_path")
    if not file_path_str:
        return 0

    normalized = _normalize(str(file_path_str))
    permitted_prefixes = [
        f"logs/agents/{agent_name}/",
    ]

    for prefix in permitted_prefixes:
        if normalized.startswith(prefix):
            return 0

    sys.stderr.write(
        f"BLOCKED by .claude/hooks/scheduled_agent_log_only.py: "
        f"{tool_name} to '{file_path_str}' is not permitted for agent '{agent_name}'.\n"
        f"Permitted paths:\n"
        f"  - logs/agents/{agent_name}/\n"
        "HOOK-BLOCK v1 hook=scheduled_agent_log_only\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
