#!/usr/bin/env bash
# The delegate of the telemetry-ci-capture composite action. TOTAL: exits 0 on every path and writes no GitHub output, env or
# summary file. It is the only place GITHUB_* is read (Decision 212); ci-pass takes every host fact as an argument.
# Repo root = three levels above this script (github.action_path), so a caller may run a main-tooling copy of the action while
# its checked-out workspace is another commit.
set +u
set +e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd)"
PY="${ROOT}/bin/venv-python"
if [ -z "$ROOT" ] || [ ! -x "$PY" ]; then
    echo "CI_CAPTURE skipped reason=no_python"
    exit 0
fi

WORKFLOW_PATH="${GITHUB_WORKFLOW_REF#"${GITHUB_REPOSITORY}/"}"
WORKFLOW_PATH="${WORKFLOW_PATH%%@*}"

ARGS=(-m scripts.telemetry_capture ci-pass
    --workflow-path "$WORKFLOW_PATH"
    --ref "$GITHUB_REF"
    --repository "$GITHUB_REPOSITORY"
    --run-id "$GITHUB_RUN_ID"
    --run-attempt "$GITHUB_RUN_ATTEMPT"
    --job "$GITHUB_JOB"
    --event-name "$GITHUB_EVENT_NAME"
    --sha "$GITHUB_SHA")

CHECKOUT_SHA="$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD 2>/dev/null)"
if [ -n "$CHECKOUT_SHA" ]; then
    ARGS+=(--checkout-sha "$CHECKOUT_SHA")
fi

while IFS= read -r line; do
    if [ -n "$line" ]; then
        ARGS+=(--manifest "$line")
    fi
done <<< "$CAPTURE_MANIFESTS"

ENV_CMD=(env)
if [ -n "$CAPTURE_AWS_ACCESS_KEY_ID" ] && [ -n "$CAPTURE_AWS_SECRET_ACCESS_KEY" ] && [ -n "$CAPTURE_AWS_SESSION_TOKEN" ]; then
    while IFS= read -r name; do
        case "$name" in
            AWS_REGION|AWS_DEFAULT_REGION) ;;
            AWS_*) ENV_CMD+=(-u "$name") ;;
        esac
    done < <(env | cut -d= -f1)
    ENV_CMD+=("AWS_ACCESS_KEY_ID=${CAPTURE_AWS_ACCESS_KEY_ID}" "AWS_SECRET_ACCESS_KEY=${CAPTURE_AWS_SECRET_ACCESS_KEY}"
        "AWS_SESSION_TOKEN=${CAPTURE_AWS_SESSION_TOKEN}")
fi

OUTPUT="$(cd "$ROOT" && timeout 240 "${ENV_CMD[@]}" "$PY" "${ARGS[@]}" 2>/dev/null)"
STATUS=$?
if [ "$STATUS" -eq 124 ]; then
    echo "CI_CAPTURE skipped reason=timeout"
    exit 0
fi
LINES="$(printf '%s\n' "$OUTPUT" | grep '^CI_CAPTURE ')"
if [ "$STATUS" -ne 0 ] || [ -z "$LINES" ]; then
    echo "CI_CAPTURE skipped reason=ci_pass_failed"
    exit 0
fi
printf '%s\n' "$LINES"
exit 0
