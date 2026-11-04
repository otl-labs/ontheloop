#!/usr/bin/env bash
# Environment wrapper of the trusted terraform/personal plan (Decision 238 cl.4).
# Usage: trusted_plan_env.sh [--scrub-only] -- CMD [ARGS...]
#
# Reads the tfvars through materialise_tfvars.sh --stdout into a shell variable (a pipe, never a file; that
# script's ::add-mask:: lines arrive on stderr, which the runner reads), masks owner_email, exports the four
# scrub variables redaction_check.py reads (_ACCT/_DEV/_ADM/_OWNER) and, unless --scrub-only, TF_VAR_<key>
# for every tfvars line, then execs CMD. --scrub-only is for the comment step: that process never holds a
# TF_VAR_* value. Writes nothing to disk, GITHUB_ENV or GITHUB_OUTPUT and prints no value; a line that is not
# "key = value" fails closed with a fixed message that never echoes the line.
set -euo pipefail

scrub_only=0
if [ "${1:-}" = "--scrub-only" ]; then
    scrub_only=1
    shift
fi
if [ "${1:-}" != "--" ] || [ $# -lt 2 ]; then
    echo "Usage: trusted_plan_env.sh [--scrub-only] -- CMD [ARGS...]" >&2
    exit 1
fi
shift

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tfvars=$(bash "$here/../../.github/actions/materialise-tfvars/materialise_tfvars.sh" --stdout) || exit 1

declare -A vars=()
while IFS= read -r line; do
    line=$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    case "$line" in
        "" | "#"*) continue ;;
    esac
    key=${line%%=*}
    value=${line#*=}
    if [ "$key" = "$line" ]; then
        echo "trusted-plan-env: BAD_TFVARS_LINE a tfvars line is not key = value; refusing (line not shown)" >&2
        exit 1
    fi
    key=$(printf '%s' "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    value=$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if ! [[ "$key" =~ ^[a-z_][a-z0-9_]*$ ]]; then
        echo "trusted-plan-env: BAD_TFVARS_LINE a tfvars key is outside ^[a-z_][a-z0-9_]*\$; refusing (line not shown)" >&2
        exit 1
    fi
    if [[ "$value" =~ ^\"([^\"\\]*)\"$ ]]; then
        value=${BASH_REMATCH[1]}
    elif ! [[ "$value" =~ ^[A-Za-z0-9._+-]+$ ]]; then
        echo "trusted-plan-env: BAD_TFVARS_LINE a tfvars value is outside the supported grammar (line not shown)" >&2
        exit 1
    fi
    vars["$key"]="$value"
    case "$key" in
        *_email) [ -z "$value" ] || echo "::add-mask::${value}" >&2 ;;
    esac
done <<<"$tfvars"
unset tfvars

_ACCT="${vars[account_id]:-}"
_DEV="${vars[platform_dev_external_id]:-}"
_ADM="${vars[platform_admin_external_id]:-}"
_OWNER="${vars[owner_email]:-}"
export _ACCT _DEV _ADM _OWNER

if [ "$scrub_only" -eq 0 ]; then
    for key in "${!vars[@]}"; do
        export "TF_VAR_${key}=${vars[$key]}"
    done
fi
unset vars
exec "$@"
