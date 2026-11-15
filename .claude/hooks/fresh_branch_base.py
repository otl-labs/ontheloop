#!/usr/bin/env python3
"""PreToolUse hook: keep new branches cut from a fresh origin/main.

Triggered for Bash. Detects branch-creation commands whose start-point
resolves to the local 'main' ref:

- git checkout -b/-B <name> [<start>]
- git switch -c/-C <name> [<start>]
- git branch <name> [<start>]

The check is keyed strictly on the resolved start-point (explicit, or
implicit == the current branch when omitted) -- never on the new branch's
name. A start-point of anything else (a feature branch, an explicit
'origin/main', a commit SHA) passes through untouched.

Behaviour when the start-point resolves to local 'main':
- Current branch != main (off main): local main can be safely
  force-updated (it isn't checked out), so this hook best-effort
  `git fetch origin main` + `git branch -f main origin/main`, then
  ALLOWS the tool call -- the branch-creation command that follows now
  cuts from a fresh local main.
- Current branch == main (on main): local main IS checked out, so it is
  not force-updatable (`git branch -f main ...` fails while checked
  out). BLOCKS with guidance to branch directly off origin/main instead.

This is a side-effecting guard (it mutates git state -- fetch + branch -f
-- before allowing), unlike the pure allow/block .claude/hooks/never_on_main.py.
It AUGMENTS never_on_main.py; it does not replace or modify it.

Exit codes:
- exit 0: allow tool invocation (refreshing local main first if applicable)
- exit 2: block tool invocation (stderr is shown to the agent)

Failure modes are handled defensively: malformed input JSON, an
unparseable Bash command, or a git error is a no-op pass-through --
this guard never halts work on a hook bug, and network calls are
best-effort so an offline remote never stalls or fails a branch cut.

## Testing
Set CLAUDE_HOOK_BRANCH_OVERRIDE to simulate the current branch without
actually switching (mirrors never_on_main.py). TEST-ONLY -- never set in
a shell rc.

Rule HB-1: every block message ends with its own versioned marker line; src/turn_capture/markers.py holds the grammar.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

# Split a Bash command on shell sequencing separators, same policy as
# never_on_main.py: '&&', '||', ';' are "then run" separators; a bare '|'
# is a data-flow operator and is left as a known-limitation bypass.
_SEPARATOR_RE = re.compile(r"\s*(?:&&|\|\||;)\s*")

# Strip leading 'NAME=value' env-var assignments (whitespace-separated).
_ENV_PREFIX_RE = re.compile(r"^\w+=\S+\s+")

_CHECKOUT_CREATE_FLAGS = {"-b", "-B"}
_SWITCH_CREATE_FLAGS = {"-c", "-C"}

# 'git branch' subcommand flags that mean this is NOT a creation/reset --
# it's a list, delete, rename, or inspection invocation. Presence of any
# of these disqualifies the command from the branch-creation pattern.
_BRANCH_DENY_FLAGS = {
    "-d",
    "-D",
    "--delete",
    "-m",
    "-M",
    "--move",
    "-c",
    "-C",
    "--copy",
    "-a",
    "--all",
    "-r",
    "--remotes",
    "-v",
    "-vv",
    "--verbose",
    "-l",
    "--list",
    "--show-current",
    "-u",
    "--set-upstream-to",
    "--unset-upstream",
    "--contains",
    "--no-contains",
    "--merged",
    "--no-merged",
    "--edit-description",
}

_BLOCK_MESSAGE = (
    "BLOCKED by .claude/hooks/fresh_branch_base.py: branching from local 'main' while "
    "'main' is the checked-out branch.\n"
    "Local main cannot be force-updated while it is checked out, so it may be stale.\n"
    "Branch directly off the remote ref instead:\n"
    "  git checkout -b <name> origin/main\n"
    "See docs/contracts/git-ops.yaml for the local-main-sync / branch-off-remote-main guard.\n"
    "HOOK-BLOCK v1 hook=fresh_branch_base\n"
)


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


def _refresh_local_main() -> None:
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            capture_output=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        subprocess.run(
            ["git", "branch", "-f", "main", "origin/main"],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


def _strip_env_prefix(segment: str) -> str:
    while True:
        match = _ENV_PREFIX_RE.match(segment)
        if not match:
            return segment
        segment = segment[match.end() :]


def _tokenize(segment: str) -> list[str] | None:
    try:
        return shlex.split(segment)
    except ValueError:
        return None


def _extract_checkout_switch(rest: list[str], create_flags: set[str]) -> tuple[str, str | None] | None:
    for i, tok in enumerate(rest):
        if tok in create_flags:
            positional = [t for t in rest[i + 1 :] if not t.startswith("-")]
            if not positional:
                return None
            name = positional[0]
            start = positional[1] if len(positional) > 1 else None
            return name, start
    return None


def _extract_branch(rest: list[str]) -> tuple[str, str | None] | None:
    if any(tok in _BRANCH_DENY_FLAGS for tok in rest):
        return None
    positional = [t for t in rest if not t.startswith("-")]
    if not positional or len(positional) > 2:
        return None
    name = positional[0]
    start = positional[1] if len(positional) > 1 else None
    return name, start


def extract_branch_creation(segment: str) -> tuple[str, str | None] | None:
    """Return (new_branch_name, explicit_start_point_or_None) for a
    branch-creation segment, else None. Public for unit testing."""
    tokens = _tokenize(_strip_env_prefix(segment.strip()))
    if not tokens or len(tokens) < 2 or tokens[0] != "git":
        return None
    verb, rest = tokens[1], tokens[2:]
    if verb == "checkout":
        return _extract_checkout_switch(rest, _CHECKOUT_CREATE_FLAGS)
    if verb == "switch":
        return _extract_checkout_switch(rest, _SWITCH_CREATE_FLAGS)
    if verb == "branch":
        return _extract_branch(rest)
    return None


def decide(command: str, current_branch: str | None) -> str:
    """Return 'block', 'refresh', or 'passthrough' for a full Bash command.
    Public for unit testing -- performs no subprocess calls itself."""
    if not command:
        return "passthrough"

    saw_refresh = False
    for raw_segment in _SEPARATOR_RE.split(command):
        segment = raw_segment.strip()
        if not segment:
            continue
        creation = extract_branch_creation(segment)
        if creation is None:
            continue
        _name, explicit_start = creation
        effective_start = explicit_start if explicit_start is not None else current_branch
        if effective_start != "main":
            continue
        if current_branch == "main":
            return "block"
        saw_refresh = True

    return "refresh" if saw_refresh else "passthrough"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    current_branch = _current_branch()

    outcome = decide(command, current_branch)
    if outcome == "block":
        sys.stderr.write(_BLOCK_MESSAGE)
        return 2
    if outcome == "refresh":
        _refresh_local_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
