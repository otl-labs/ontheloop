#!/usr/bin/env bash
# Fail-closed shim for merge_gate.py (handoff_evidence_gate.sh pattern). A payload not naming a merge tool exits 0
# without starting Python; every adapter failure mode other than a clean 0 or 2 maps to exit 2. No override, no
# GITHUB_ACTIONS carve-out. No `set -e`: a bare failure must reach the trap.

PAYLOAD="$(cat)"

case "$PAYLOAD" in
    *merge_pull_request* | *enable_pr_auto_merge*) ;;
    *) exit 0 ;;
esac

STATUS=2
trap 'exit $STATUS' EXIT

ROOT="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$ROOT" ]; then
    ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi

if [ ! -f "$ROOT/.claude/hooks/merge_gate.py" ]; then
    echo "merge-gate unavailable (missing adapter)" >&2
    exit 2
fi

ERRFILE="$(mktemp 2>/dev/null || echo /tmp/.merge_gate_stderr.$$)"
OUTPUT="$(printf '%s' "$PAYLOAD" | timeout -k 5 45 "$ROOT/bin/venv-python" -I "$ROOT/.claude/hooks/merge_gate.py" 2>"$ERRFILE")"
RC=$?
STDERR="$(cat "$ERRFILE" 2>/dev/null)"
rm -f "$ERRFILE"

if [ "$RC" -eq 0 ]; then
    STATUS=0
    [ -n "$OUTPUT" ] && printf '%s\n' "$OUTPUT"
    exit 0
elif [ "$RC" -eq 2 ]; then
    [ -n "$STDERR" ] && printf '%s\n' "$STDERR" >&2
    exit 2
else
    echo "merge-gate unavailable ($RC)" >&2
    exit 2
fi
