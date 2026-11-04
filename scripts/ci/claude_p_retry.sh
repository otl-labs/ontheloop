#!/usr/bin/env bash
# Bounded transient-5xx retry wrapper for `claude -p`.
# Usage: claude_p_retry.sh <output_file> -- <claude args...>
#
# Retries up to MAX_ATTEMPTS=3 on transient Claude API errors.
# Never retries on substantive verdicts -- caller owns verdict semantics (Decision 55).
# Backoff: CLAUDE_P_RETRY_BACKOFF_BASE (default 4) * 2^attempt seconds.
# Parity with _TRANSIENT_CLAUDE_SIGNATURES in scripts/validate.py (Decision 73, Decision 92).
#
# Capture manifest: each attempt runs under its own fresh --session-id and appends two compact JSON lines to
# "<output_file>.sessions.jsonl" (never truncated; two invocations in one job share the file): a start line before claude
# runs {attempt, session_id, started_at} and a result line after {session_id, exit, transient}. A manifest write failure
# is ignored. Callers may not pass flags that change the session identity (usage exit 1).
#
# Distinct from the terraform-registry retry loop (_TRANSIENT_INIT_SIGNATURES): Claude API
# transient signatures differ from registry 5xx patterns and are defined separately here.
set -euo pipefail

MAX_ATTEMPTS=3
BACKOFF_BASE="${CLAUDE_P_RETRY_BACKOFF_BASE:-4}"

if [ $# -lt 2 ]; then
    echo "Usage: claude_p_retry.sh <output_file> -- <claude args...>" >&2
    exit 1
fi

OUTPUT_FILE="$1"
shift

if [ "$1" != "--" ]; then
    echo "claude_p_retry.sh: expected '--' separator after output_file; got: $1" >&2
    exit 1
fi
shift

# Read stdin once; replayed on each attempt to support both piped-prompt and positional-arg callers.
STDIN_CONTENT=$(cat)

for arg in "$@"; do
    case "$arg" in
        --session-id|--session-id=*|--resume|--resume=*|--continue|--continue=*|--fork-session|--fork-session=*|\
        --no-session-persistence|--no-session-persistence=*|-r|-c)
            echo "claude_p_retry.sh: capture needs one fresh persisted session per attempt; refusing ${arg%%=*}" >&2
            exit 1
            ;;
    esac
done

MANIFEST="${OUTPUT_FILE}.sessions.jsonl"

_new_session_id() {
    if [ -r /proc/sys/kernel/random/uuid ]; then
        cat /proc/sys/kernel/random/uuid
    else
        python3 -c 'import uuid;print(uuid.uuid4())'
    fi
}

_manifest_append() {
    { printf '%s\n' "$1" >> "$MANIFEST"; } 2>/dev/null || true
}

_is_transient() {
    local content="$1"
    if [[ "$content" == *"500"* ]] || \
       [[ "$content" == *"502"* ]] || \
       [[ "$content" == *"503"* ]] || \
       [[ "$content" == *"API Error: 5"* ]] || \
       [[ "$content" == *"Internal server error"* ]] || \
       [[ "$content" == *"overloaded"* ]]; then
        return 0
    fi
    return 1
}

LAST_EXIT=0
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    LAST_EXIT=0
    SESSION_ID=$(_new_session_id 2>/dev/null || true)
    SESSION_ID_ARGS=()
    if [ -n "$SESSION_ID" ]; then
        SESSION_ID_ARGS=(--session-id "$SESSION_ID")
        STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || true)
        _manifest_append "{\"attempt\":${attempt},\"session_id\":\"${SESSION_ID}\",\"started_at\":\"${STARTED_AT}\"}"
    fi
    printf '%s' "$STDIN_CONTENT" | claude -p ${SESSION_ID_ARGS[@]+"${SESSION_ID_ARGS[@]}"} "$@" > "$OUTPUT_FILE" 2>&1 || LAST_EXIT=$?
    TRANSIENT=false
    if [ "$LAST_EXIT" -ne 0 ]; then
        OUTPUT_CONTENT=$(cat "$OUTPUT_FILE")
        if _is_transient "$OUTPUT_CONTENT"; then
            TRANSIENT=true
        fi
    fi
    if [ -n "$SESSION_ID" ]; then
        _manifest_append "{\"session_id\":\"${SESSION_ID}\",\"exit\":${LAST_EXIT},\"transient\":${TRANSIENT}}"
    fi
    if [ "$LAST_EXIT" -eq 0 ]; then
        exit 0
    fi
    if [ "$TRANSIENT" = true ] && [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
        DELAY=$(( BACKOFF_BASE * (2 ** attempt) ))
        echo "claude_p_retry: transient error on attempt ${attempt}/${MAX_ATTEMPTS}; retrying in ${DELAY}s..." >&2
        sleep "$DELAY"
    else
        break
    fi
done

exit "$LAST_EXIT"
