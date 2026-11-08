#!/usr/bin/env bash
# Fail-closed shim for protected_action_gate.py (handoff_evidence_gate.sh pattern; rec-4518). A hook timeout and any
# exit other than 0 and 2 are non-blocking to the harness, so this shim maps a timeout or interpreter failure to a deny
# when the payload names a coarse keyword, and to an allow otherwise. No `set -e`, no `set -u`: a bare failure must
# reach the mapping below. Deny-only: it never prints a permission decision.

PAYLOAD="$(cat)"

ROOT="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$ROOT" ]; then
    ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fi

COARSE="terraform secretsmanager ssm get_secret_value WithDecryption materialise_tfvars sync-terraform-assets neon_api_key agent_platform_admin"

fail() {
    for word in $COARSE; do
        case "$PAYLOAD" in *"$word"*)
            echo "protected-action-gate unavailable ($1): refusing a command that names $word" >&2
            exit 2
            ;;
        esac
    done
    if [ "${AWS_PROFILE:-}" = "agent_platform_admin" ]; then
        case "$PAYLOAD" in *aws*)
            echo "protected-action-gate unavailable ($1): refusing an aws call under an admin AWS_PROFILE" >&2
            exit 2
            ;;
        esac
    fi
    exit 0
}

if [ ! -f "$ROOT/.claude/hooks/protected_action_gate.py" ]; then
    fail "missing adapter"
fi

ERRFILE="$(mktemp 2>/dev/null || echo /tmp/.protected_action_gate_stderr.$$)"
OUTPUT="$(printf '%s' "$PAYLOAD" | timeout -k 5 20 "$ROOT/bin/venv-python" -I "$ROOT/.claude/hooks/protected_action_gate.py" 2>"$ERRFILE")"
RC=$?
STDERR="$(cat "$ERRFILE" 2>/dev/null)"
rm -f "$ERRFILE"

if [ "$RC" -eq 0 ]; then
    [ -n "$OUTPUT" ] && printf '%s\n' "$OUTPUT"
    exit 0
elif [ "$RC" -eq 2 ]; then
    [ -n "$STDERR" ] && printf '%s\n' "$STDERR" >&2
    exit 2
fi
fail "exit $RC"
