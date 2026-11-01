#!/usr/bin/env bash
# Innermost wrapper of the trusted terraform/personal plan (Decision 238 cl.4).
# Usage: quiet_run.sh LOG [--stdout FILE] -- CMD [ARGS...]
#
# Runs CMD with stderr appended to LOG and stdout to FILE (or to LOG too when --stdout is absent), then
# exits with CMD's status. Prints nothing itself. It must stay the innermost wrapper: the ::add-mask::
# lines the outer wrappers (trusted_plan_env.sh, neon_api_key.sh run) emit are written before they exec
# into this script, so they reach the runner, while only Terraform's own output lands in the never-printed
# log. LOG and FILE must live under $RUNNER_TEMP.
set -euo pipefail

under_runner_temp() {
    local resolved base
    base=$(realpath -m -- "${RUNNER_TEMP:-}") || return 1
    resolved=$(realpath -m -- "$1") || return 1
    [ -n "${RUNNER_TEMP:-}" ] && case "$resolved" in
        "$base"/*) return 0 ;;
        *) return 1 ;;
    esac
}

usage() {
    echo "Usage: quiet_run.sh LOG [--stdout FILE] -- CMD [ARGS...]" >&2
    exit 1
}

[ $# -ge 3 ] || usage
log="$1"
shift
out=""
if [ "${1:-}" = "--stdout" ]; then
    [ $# -ge 2 ] || usage
    out="$2"
    shift 2
fi
[ "${1:-}" = "--" ] && [ $# -ge 2 ] || usage
shift

if ! under_runner_temp "$log" || { [ -n "$out" ] && ! under_runner_temp "$out"; }; then
    echo "quiet_run: LOG_OUTSIDE_RUNNER_TEMP refusing a log or output path outside RUNNER_TEMP" >&2
    exit 1
fi

if [ -n "$out" ]; then
    exec "$@" >"$out" 2>>"$log"
fi
exec "$@" >>"$log" 2>&1
