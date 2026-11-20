#!/usr/bin/env bash
# Fail-closed shim for handoff_evidence_gate.py -- a non-0/2 PreToolUse exit and a hook timeout
# are both NON-blocking to the harness, so this shim maps every failure mode of the adapter or
# its interpreter to exit 2 (deny) for push-shaped calls, and exit 0 for everything else without
# ever starting Python.
#
# No `set -e` and no `set -u`: a bare failure must reach the trap below, not abort silently.

PAYLOAD="$(cat)"

HAS_MCP_GITHUB=0
HAS_GIT=0
HAS_PUSH=0
case "$PAYLOAD" in *mcp__github*) HAS_MCP_GITHUB=1 ;; esac
case "$PAYLOAD" in *git*) HAS_GIT=1 ;; esac
case "$PAYLOAD" in *push*) HAS_PUSH=1 ;; esac

if [ "$HAS_MCP_GITHUB" -ne 1 ] && { [ "$HAS_GIT" -ne 1 ] || [ "$HAS_PUSH" -ne 1 ]; }; then
    exit 0
fi

STATUS=2
trap '[ "$STATUS" -eq 2 ] && echo "HOOK-BLOCK v1 hook=handoff_evidence_gate" >&2; exit $STATUS' EXIT

if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    echo "handoff-evidence-gate: carve-out (GITHUB_ACTIONS=true) -- not enforced in this environment" >&2
    STATUS=0
    exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$ROOT" ]; then
    ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi

OUTPUT="$(printf '%s' "$PAYLOAD" | timeout -k 5 45 "$ROOT/bin/venv-python" "$ROOT/.claude/hooks/handoff_evidence_gate.py" 2>/tmp/.handoff_gate_stderr.$$)"
RC=$?
STDERR="$(cat /tmp/.handoff_gate_stderr.$$ 2>/dev/null)"
rm -f /tmp/.handoff_gate_stderr.$$

if [ "$RC" -eq 0 ]; then
    STATUS=0
    if [ -n "$OUTPUT" ]; then
        printf '%s\n' "$OUTPUT"
    fi
    exit 0
elif [ "$RC" -eq 2 ]; then
    STATUS=2
    if [ -n "$STDERR" ]; then
        printf '%s\n' "$STDERR" >&2
    fi
    exit 2
else
    STATUS=2
    echo "handoff gate unavailable ($RC)" >&2
    exit 2
fi
