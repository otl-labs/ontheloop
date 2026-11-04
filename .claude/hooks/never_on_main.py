#!/usr/bin/env python3
"""PreToolUse hook: block mutation tools while on the 'main' branch.

Triggered for Edit, Write, MultiEdit, NotebookEdit, and Bash. Allows
read-only Bash commands (git status, ls, etc.) but blocks any Bash
command that runs 'git commit' or 'git push' - including:

- Simple form: 'git commit -m ...'
- Compound: 'cd /tmp && git commit ...'  or  'echo done; git commit'
- Env-prefixed: 'GIT_DIR=foo git commit'
- Working-dir override: 'git -C /path commit'  or  'git --git-dir=foo commit'

Edit/Write/MultiEdit/NotebookEdit are blocked on main only when the target
(file_path, or notebook_path) resolves inside the repo top-level. Writes that
resolve outside the repo (memory, scratchpad) pass. A missing target key or an
undeterminable top-level keeps blocking.

Behaviour:
- exit 0: allow tool invocation
- exit 2: block tool invocation (stderr is shown to the agent)

Failure modes are handled defensively: if the input JSON is malformed or
git is unavailable, the hook allows the tool to proceed rather than
blocking. A buggy hook should never halt all work for a sole developer.

## Known limitations (deliberate)
This is a sole-developer guardrail against accidental commits, not a
security perimeter. The following bypass paths are NOT detected:
- Subshells:                      (git commit)
- Command substitution / eval:    eval "git commit"
- Sneaky refspec push:            'git push origin agent/foo:main'
                                  while on agent/foo (pushes to remote
                                  main from a non-main local branch).
                                  Detecting this requires refspec parsing.
- Aliases / shell functions:      alias gc='git commit'

If a real incident occurs from any of these, harden then.

## Testing
Set CLAUDE_HOOK_BRANCH_OVERRIDE=main to simulate being on main without
actually switching branches. This is a TEST-ONLY escape hatch - never
set this env var in your shell rc.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Split a Bash command on shell sequencing separators. Single '|' (pipe)
# is intentionally excluded - it's a data-flow operator, not a "then run"
# separator, and chaining a pipe into 'git commit' is exotic enough to
# leave as a known-limitation bypass.
_SEPARATOR_RE = re.compile(r"\s*(?:&&|\|\||;)\s*")

# Strip leading 'NAME=value' env-var assignments (whitespace-separated).
_ENV_PREFIX_RE = re.compile(r"^\w+=\S+\s+")

# Match 'git <option-with-arg> commit|push' for options that take a path:
#   git -C <path> commit
#   git --git-dir <path> commit       or   --git-dir=<path>
#   git --work-tree <path> commit     or   --work-tree=<path>
_GIT_OPT_VERB_RE = re.compile(r"^git\s+(?:-C\s+\S+|--git-dir(?:=\S+|\s+\S+)|--work-tree(?:=\S+|\s+\S+))\s+(?:commit|push)\b")


def _current_branch() -> str | None:
    override = os.environ.get("CLAUDE_HOOK_BRANCH_OVERRIDE")
    if override is not None:
        return override
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _segment_blocks(segment: str) -> bool:
    stripped = segment.strip()
    while True:
        match = _ENV_PREFIX_RE.match(stripped)
        if not match:
            break
        stripped = stripped[match.end() :]
    if stripped.startswith(("git commit", "git push")):
        return True
    return bool(_GIT_OPT_VERB_RE.match(stripped))


def _bash_blocks_on_main(command: str) -> bool:
    if not command:
        return False
    for segment in _SEPARATOR_RE.split(command):
        if _segment_blocks(segment):
            return True
    return False


def _repo_toplevel() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    top = result.stdout.strip()
    return os.path.realpath(top) if result.returncode == 0 and top else None


def _target_inside_repo(tool_input: dict) -> bool:
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    top = _repo_toplevel()
    if not isinstance(target, str) or not target or top is None:
        return True
    resolved = os.path.realpath(target)
    return resolved == top or resolved.startswith(top + os.sep)


def _should_block(tool_name: str, tool_input: dict) -> bool:
    if tool_name in _MUTATING_TOOLS:
        return True
    if tool_name == "Bash":
        return _bash_blocks_on_main(tool_input.get("command") or "")
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if not _should_block(tool_name, tool_input):
        return 0

    branch = _current_branch()
    if branch != "main":
        return 0

    if tool_name in _MUTATING_TOOLS and not _target_inside_repo(tool_input):
        return 0

    sys.stderr.write(
        f"BLOCKED by .claude/hooks/never_on_main.py: cannot use {tool_name} while on 'main' branch.\n"
        "On Claude Code on the web you are already on the harness-assigned session branch --\n"
        "verify with: git branch --show-current\n"
        "If the result is still 'main', stop and tell the operator; agents never create their own branch.\n"
        "See docs/contracts/git-ops.yaml for the full branching topology.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
