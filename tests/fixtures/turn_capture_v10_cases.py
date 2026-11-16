"""Builders for the two parser-v10 golden cases (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case. Timestamps are the seconds after the corpus epoch.
The denied tool_result shape is copied from the 2026-11-12 probe of Claude Code 2.1.322 (PLAN-telemetry-hook-denial-names).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    assistant,
    hook,
    make_files,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
    witness,
)

PLUGIN_LINE = "This hook comes from the ontheloop@synced plugin."
MARKER_NEVER_ON_MAIN = "HOOK-BLOCK v1 hook=never_on_main"
PERMISSION_HOOK = {"decision": "reject", "source": "hook", "reasonType": "hook", "reasonCode": "hook:PreToolUse"}


def denial_text(tool: str, stem: str, marker: str | None) -> str:
    lines = [f"PreToolUse:{tool} hook error: [command hook 3]: BLOCKED by .claude/hooks/{stem}.py: synthetic block text."]
    if marker is not None:
        lines.append(marker)
    return "\n".join([*lines, "", PLUGIN_LINE])


def denied_result(uuid: str, t: float, tool_use_id: str, text: str, pid: str = "p1", **extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"toolDenialKind": "permission-rule", "permissionDecision": dict(PERMISSION_HOOK)}
    fields.update(extra)
    return tool_result(uuid, t, tool_use_id, text, pid=pid, is_error=True, **fields)


def hook_denial_cloud_shape_files() -> dict[str, str]:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Write", {"file_path": "x.txt"})]),
        denied_result("r1", 3, "tu1", denial_text("Write", "never_on_main", MARKER_NEVER_ON_MAIN)),
        assistant("a2", 4, "m2", [tool_use_block("tu2", "Write", {"file_path": "y.txt"})]),
        denied_result("r2", 5, "tu2", denial_text("Write", "edit_scope_guard", None)),
        assistant("a3", 6, "m3", [tool_use_block("tu3", "Bash", {"command": "ls"})]),
        denied_result(
            "r3",
            7,
            "tu3",
            "PreToolUse:Bash hook error: [command hook 4]: no evidence recorded for this handoff.\n\n" + PLUGIN_LINE,
        ),
        assistant("a4", 8, "m4", [text_block("done")]),
        witness("w1", 9),
    ]
    return make_files(root)


def hook_denial_attachment_twin_files() -> dict[str, str]:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "git commit"})]),
        hook(
            "h1",
            2.2,
            "PreToolUse:Bash",
            tool_use_id="tu1",
            exit_code=2,
            command="python .claude/hooks/never_on_main.py",
            a_type="hook_blocking_error",
            stderr=denial_text("Bash", "never_on_main", MARKER_NEVER_ON_MAIN),
        ),
        denied_result("r1", 3, "tu1", denial_text("Bash", "never_on_main", MARKER_NEVER_ON_MAIN)),
        assistant("a2", 4, "m2", [text_block("done")]),
        witness("w1", 5),
    ]
    return make_files(root)


V10_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "hook_denial_cloud_shape.json": hook_denial_cloud_shape_files,
    "hook_denial_attachment_twin.json": hook_denial_attachment_twin_files,
}
