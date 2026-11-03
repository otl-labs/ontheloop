#!/usr/bin/env bash
# Fetch the Terraform assets a CC-web ADMIN session needs (rec-4747, Decision 120).
#
#   (no argument)  Sync the provider mirror routed through config/terraform/cc-web.tfrc's filesystem_mirror block from
#                  s3://agent-platform-data-lake/tf-provider-mirror/ to $HOME/.terraform-mirror as agent_platform
#                  (PlatformDev; the mirror holds public provider binaries). The resolved tfrc,
#                  $HOME/.terraform-mirror.tfrc, is written only after a successful non-empty sync and removed after a
#                  failed or empty one, so a resolved config never excludes a namespace from `direct` with nothing in
#                  the mirror to serve. Run by .claude/hooks/session_start_terraform_assets.sh every ADMIN session.
#   --tfvars       Run the shared materialise_tfvars.sh delegate as agent_platform_admin (a PlatformAdmin secret read).
#                  Never run automatically: it is the deliberate operator act Decision 126 asks for.
#
# bin/setup-cloud-env.sh cannot do either: it runs once per snapshot, before environment variables are set, and the
# agent proxy never signs its requests.
#
# Output is one line per run (`mirror-sync: OK (<n>s)` or `mirror-sync: WARNING <CLASS> (<n>s)`; `tfvars-fetch:` in
# --tfvars mode) plus, on failure, one scrubbed detail line. Default mode overwrites $HOME/.terraform-mirror.log with the
# same lines. Exit 0 on success, 1 on failure, 2 on a bad argument. No errexit assumption (Decision 162).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

MODE=mirror
case "${1:-}" in
    "") ;;
    --tfvars) MODE=tfvars ;;
    *)
        echo "sync-terraform-assets: usage: sync-terraform-assets.sh [--tfvars]" >&2
        exit 2
        ;;
esac

BACKOFF="${SYNC_TF_ASSETS_BACKOFF_SECONDS:-3}"
ATTEMPT_TIMEOUT="${SYNC_TF_ASSETS_ATTEMPT_TIMEOUT:-25}"
MIRROR_DIR="$HOME/.terraform-mirror"
RESOLVED_TFRC="$HOME/.terraform-mirror.tfrc"
LOG_FILE="$HOME/.terraform-mirror.log"
TFRC_TEMPLATE="$REPO_ROOT/config/terraform/cc-web.tfrc"
DELEGATE="$REPO_ROOT/.github/actions/materialise-tfvars/materialise_tfvars.sh"

if [ "$MODE" = mirror ]; then LABEL=mirror-sync; else LABEL=tfvars-fetch; fi

ERR_FILE="$(mktemp)"
trap 'rm -f "$ERR_FILE"' EXIT

classify() {
    local rc="$1" text
    text="$(cat "$ERR_FILE" 2>/dev/null)"
    if [ "$rc" -eq 124 ]; then echo TIMEOUT
    elif grep -qi 'unable to locate credentials' <<<"$text"; then echo NO_CREDENTIALS
    elif grep -qi 'you must specify a region' <<<"$text"; then echo NO_REGION
    elif grep -qiE 'ExpiredToken|token.*expired' <<<"$text"; then echo EXPIRED_TOKEN
    elif grep -qiE 'InvalidClientTokenId|InvalidToken|SignatureDoesNotMatch' <<<"$text"; then echo INVALID_TOKEN
    elif grep -qiE 'AccessDenied|Access Denied|not authorized|\(403\)' <<<"$text"; then echo ACCESS_DENIED
    elif grep -qiE 'could not connect|connection|name or service|temporary failure|endpoint ?url|ssl|read timeout' <<<"$text"; then echo NETWORK
    else echo OTHER
    fi
}

error_line() {
    local line prefix_only=""
    while IFS= read -r line; do
        [ -n "${line//[[:space:]]/}" ] || continue
        case "$line" in
            materialise-tfvars:*) [ -n "$prefix_only" ] || prefix_only="$line" ;;
            *) printf '%s' "$line"; return 0 ;;
        esac
    done <"$ERR_FILE"
    printf '%s' "$prefix_only"
}

scrub_line() {
    local masked
    if masked="$(printf '%s' "$1" \
        | sed -E 's/arn:[^[:space:]]*/[ARN]/g; s/(AKIA|ASIA)[A-Z0-9]{12,}/[KEY_ID]/g' \
        | "$REPO_ROOT/bin/venv-python" -c \
            'import sys; from scripts.ci.redaction_check import mask_account_ids; sys.stdout.write(mask_account_ids(sys.stdin.read()))' \
            2>/dev/null)"; then
        printf '%s' "${masked:0:200}"
    else
        printf 'detail withheld: mask unavailable'
    fi
}

emit() {
    printf '%s\n' "$1"
    [ "$MODE" = mirror ] && printf '%s\n' "$1" >>"$LOG_FILE.new"
    return 0
}

finish_failure() {
    local class="$1" elapsed="$2" detail=""
    [ "$MODE" = mirror ] && rm -f "$RESOLVED_TFRC"
    emit "$LABEL: WARNING $class (${elapsed}s)"
    if [ "$class" != EMPTY_MIRROR ] && [ -s "$ERR_FILE" ]; then
        detail="$(error_line)"
        [ -z "$detail" ] || emit "$LABEL: detail: $(scrub_line "$detail")"
    fi
    [ "$MODE" = mirror ] && mv -f "$LOG_FILE.new" "$LOG_FILE"
    exit 1
}

run_attempts() {
    local attempt rc=0 class=OTHER
    for attempt in 1 2; do
        : >"$ERR_FILE"
        "$@" 2>"$ERR_FILE"
        rc=$?
        if [ "$rc" -eq 0 ]; then return 0; fi
        class="$(classify "$rc")"
        case "$class" in NO_CREDENTIALS | NO_REGION) break ;; esac
        [ "$attempt" -lt 2 ] && sleep "$BACKOFF"
    done
    ATTEMPT_CLASS="$class"
    return 1
}

[ "$MODE" = mirror ] && : >"$LOG_FILE.new"
t0=$SECONDS
ATTEMPT_CLASS=OTHER

if [ "$MODE" = tfvars ]; then
    if run_attempts env -u GITHUB_ACTIONS AWS_PROFILE=agent_platform_admin timeout "$ATTEMPT_TIMEOUT" bash "$DELEGATE" >/dev/null; then
        emit "$LABEL: OK ($((SECONDS - t0))s)"
        exit 0
    fi
    finish_failure "$ATTEMPT_CLASS" "$((SECONDS - t0))"
fi

NAMESPACES=()
if [ -f "$TFRC_TEMPLATE" ]; then
    while IFS= read -r ns; do
        [ -n "$ns" ] && NAMESPACES+=("$ns")
    done < <(grep -E '^[[:space:]]*include[[:space:]]*=' "$TFRC_TEMPLATE" | grep -oE '"[^"]+"' | tr -d '"')
fi
if [ "${#NAMESPACES[@]}" -eq 0 ]; then
    : >"$ERR_FILE"
    finish_failure OTHER "$((SECONDS - t0))"
fi
INCLUDES=()
for ns in "${NAMESPACES[@]}"; do INCLUDES+=(--include "$ns"); done

mkdir -p "$MIRROR_DIR"
sync_mirror() {
    timeout "$ATTEMPT_TIMEOUT" aws s3 sync "s3://agent-platform-data-lake/tf-provider-mirror/" "$MIRROR_DIR" --exclude "*" "${INCLUDES[@]}" --only-show-errors --delete --profile agent_platform
}
if ! run_attempts sync_mirror; then
    finish_failure "$ATTEMPT_CLASS" "$((SECONDS - t0))"
fi

for ns in "${NAMESPACES[@]}"; do
    if [ -z "$(find "$MIRROR_DIR/${ns%/\*}" -type f -print -quit 2>/dev/null)" ]; then
        : >"$ERR_FILE"
        finish_failure EMPTY_MIRROR "$((SECONDS - t0))"
    fi
done

if sed "s|__TF_MIRROR_DIR__|${MIRROR_DIR}|g" "$TFRC_TEMPLATE" >"$RESOLVED_TFRC.new" && mv -f "$RESOLVED_TFRC.new" "$RESOLVED_TFRC"; then
    emit "$LABEL: OK ($((SECONDS - t0))s)"
    mv -f "$LOG_FILE.new" "$LOG_FILE"
    exit 0
fi
rm -f "$RESOLVED_TFRC.new"
: >"$ERR_FILE"
finish_failure OTHER "$((SECONDS - t0))"
