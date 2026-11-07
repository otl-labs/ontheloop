#!/usr/bin/env bash
# The apply job of .github/workflows/terraform-github-apply.yml, as one process (Decision 248 cl.3).
#
# The apply App key and the installation token minted from it live only in shell variables of this process
# (secret-material-handling.yaml provider_credential_ephemeral_input): the key is read from Secrets Manager into a
# variable and reaches openssl through a process substitution; every GitHub call reads its Authorization header from
# stdin (curl -H @-), never argv; the token reaches Terraform only as TF_VAR_github_token and is revoked on every exit.
# Terraform output goes to never-printed logs under RUNNER_TEMP (quiet_run.sh). Prints machine lines, counts and
# allowlisted values only. The EXIT trap also runs on SIGTERM; a runner SIGKILL can pre-empt it, leaving the
# token to its one-hour expiry.
#
# Inputs (env): REVIEWED (the preview's reviewed set), GATE_CHANGE, PROBE_RUN_ID (optional), REVIEWER_LOGINS,
# GITHUB_REPOSITORY, RUNNER_TEMP, GITHUB_STEP_SUMMARY, and AWS credentials for github-settings-apply.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
GATE="$HERE/terraform_github_apply_gate.py"
QUIET="$HERE/quiet_run.sh"
TF_DIR="$ROOT/terraform/github"
APP_ENTRY="agent-platform-terraform-github-apply-app" # pragma: allowlist secret -- an entry name, not a value
API="${GITHUB_API_URL:-https://api.github.com}"
LOG="$RUNNER_TEMP/terraform-github-apply.log"
PLAN_BIN="$TF_DIR/plan.bin"
PLAN_JSON="$RUNNER_TEMP/terraform-github-apply-plan.json"
BODY="$RUNNER_TEMP/gh-body.json"
HEADERS="$RUNNER_TEMP/gh-headers.txt"
TOKEN=""

PROBE_RUN_ID="${PROBE_RUN_ID:-}"
if [ -n "$PROBE_RUN_ID" ] && ! [[ "$PROBE_RUN_ID" =~ ^[0-9]+$ ]]; then
    echo "APPLY_APP_PROBE_INCONCLUSIVE approval_probe_run_id is not a run id"
    exit 1
fi

cleanup() {
    local rc=$?
    if [ -n "$TOKEN" ]; then
        printf 'Authorization: Bearer %s\nAccept: application/vnd.github+json\n' "$TOKEN" |
            curl -sS -o /dev/null -X DELETE -H @- "$API/installation/token" || true
        TOKEN=""
    fi
    rm -f "$PLAN_BIN" "$PLAN_JSON" "$BODY" "$HEADERS"
    exit "$rc"
}
trap cleanup EXIT

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# api METHOD PATH [JSON]: the status goes to stdout, the body to $BODY and the headers to $HEADERS.
api() {
    local data=()
    [ $# -ge 3 ] && data=(--data "$3")
    printf 'Authorization: Bearer %s\nAccept: application/vnd.github+json\nX-GitHub-Api-Version: 2022-11-28\n' "$TOKEN" |
        curl -sS -o "$BODY" -D "$HEADERS" -w '%{http_code}' -X "$1" -H @- "${data[@]}" "$API$2" || echo 000
}

header() { tr -d '\r' < "$HEADERS" | awk -v k="$1" 'index(tolower($0), k ":") == 1 {sub(/^[^:]*:[ \t]*/, ""); print; exit}'; }

terraform_step() {
    local step="$1"
    shift
    if ! "$QUIET" "$LOG" -- "$@"; then
        echo "TF_GITHUB_APPLY_TERRAFORM_FAILED step=$step"
        exit 1
    fi
}

# Mint a narrowed installation token: this repository only, Administration write, Metadata and Actions read.
entry=$(aws secretsmanager get-secret-value --secret-id "$APP_ENTRY" --query SecretString --output text 2> /dev/null) || {
    echo "APPLY_APP_KEY_READ_FAILED"
    exit 1
}
app_id=$(printf '%s' "$entry" | jq -r .app_id)
installation_id=$(printf '%s' "$entry" | jq -r .installation_id)
key=$(printf '%s' "$entry" | jq -r .private_key)
unset entry
now=$(date +%s)
unsigned="$(printf '{"alg":"RS256","typ":"JWT"}' | b64url).$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$app_id" | b64url)"
jwt="$unsigned.$(printf '%s' "$unsigned" | openssl dgst -sha256 -sign <(printf '%s\n' "$key") | b64url)"
unset key
repo_name="${GITHUB_REPOSITORY#*/}"
status=$(printf 'Authorization: Bearer %s\nAccept: application/vnd.github+json\nX-GitHub-Api-Version: 2022-11-28\n' "$jwt" |
    curl -sS -o "$BODY" -w '%{http_code}' -X POST -H @- \
        --data "{\"repositories\":[\"$repo_name\"],\"permissions\":{\"administration\":\"write\",\"metadata\":\"read\",\"actions\":\"read\"}}" \
        "$API/app/installations/$installation_id/access_tokens") || status=000
unset jwt
if [ "$status" != 201 ]; then
    echo "APPLY_APP_TOKEN_FAILED status=$status"
    exit 1
fi
TOKEN=$(jq -r .token < "$BODY")
rm -f "$BODY"
echo "::add-mask::$TOKEN"
echo "APPLY_APP_TOKEN_MINTED"

# Probe leg (Decision 248 cl.8): the apply App's token must not approve a pending deployment.
if [ -n "$PROBE_RUN_ID" ]; then
    pending="/repos/$GITHUB_REPOSITORY/actions/runs/$PROBE_RUN_ID/pending_deployments"
    status=$(api GET "$pending")
    env_id=$(jq -r 'if type == "array" and length == 1 and .[0].environment.name == "deployment-approval-probe" then .[0].environment.id else empty end' < "$BODY" 2> /dev/null || true)
    can_approve=$(jq -r 'if .[0] | has("current_user_can_approve") then .[0].current_user_can_approve else "unknown" end' < "$BODY" 2> /dev/null || echo unknown)
    if [ "$status" != 200 ] || ! [[ "$env_id" =~ ^[0-9]+$ ]]; then
        echo "APPLY_APP_PROBE_INCONCLUSIVE pending read status=$status"
        exit 1
    fi
    status=$(api POST "$pending" "{\"environment_ids\":[$env_id],\"state\":\"approved\",\"comment\":\"Decision 248 apply App probe\"}")
    message=$(jq -r '.message // ""' < "$BODY" 2> /dev/null || true)
    ratelimit=$(header x-ratelimit-remaining)
    accepted=$(header x-accepted-github-permissions)
    request_id=$(header x-github-request-id)
    after=$(api GET "$pending")
    pending_after=unknown
    if [ "$after" = 200 ]; then
        pending_after=$(jq -r 'type == "array" and any(.[]; .environment.name == "deployment-approval-probe")' < "$BODY" 2> /dev/null || echo unknown)
    fi
    python3 -I "$GATE" probe-verdict --status "$status" --message "$message" --ratelimit-remaining "$ratelimit" \
        --accepted-permissions "$accepted" --request-id "$request_id" --can-approve "$can_approve" --pending-after "$pending_after"
fi

ids=""
for login in ${REVIEWER_LOGINS//,/ }; do
    [[ "$login" =~ ^[A-Za-z0-9-]+$ ]] || { echo "REVIEWER_LOOKUP_FAILED"; exit 1; }
    [ "$(api GET "/users/$login")" = 200 ] || { echo "REVIEWER_LOOKUP_FAILED $login"; exit 1; }
    id=$(jq -r .id < "$BODY")
    [[ "$id" =~ ^[0-9]+$ ]] || { echo "REVIEWER_LOOKUP_FAILED $login"; exit 1; }
    ids="${ids:+$ids,}$id"
done
[ -n "$ids" ] || { echo "REVIEWER_LOOKUP_FAILED no login resolved"; exit 1; }
rm -f "$BODY" "$HEADERS"
export TF_VAR_gated_apply_reviewer_user_ids="[$ids]"
export TF_VAR_github_token="$TOKEN"
export TF_IN_AUTOMATION=1 TF_INPUT=0

terraform_step init terraform -chdir="$TF_DIR" init -input=false -lockfile=readonly
terraform_step plan terraform -chdir="$TF_DIR" plan -input=false -no-color -out=plan.bin
"$QUIET" "$LOG" --stdout "$PLAN_JSON" -- terraform -chdir="$TF_DIR" show -json plan.bin || {
    echo "TF_GITHUB_APPLY_TERRAFORM_FAILED step=show"
    exit 1
}
checked=$(python3 -I "$GATE" check --plan-json "$PLAN_JSON" --reviewed "$REVIEWED" --gate-change "${GATE_CHANGE:-}" \
    --summary "$GITHUB_STEP_SUMMARY") || {
    printf '%s\n' "$checked"
    exit 1
}
printf '%s\n' "$checked"
if [ "$checked" = "TF_GITHUB_APPLY_NOTHING" ]; then
    exit 0
fi
terraform_step apply terraform -chdir="$TF_DIR" apply -input=false -no-color plan.bin
echo "TF_GITHUB_APPLY_APPLIED ${checked#TF_GITHUB_APPLY_CHECKED }"
set +e
"$QUIET" "$LOG" -- terraform -chdir="$TF_DIR" plan -input=false -no-color -detailed-exitcode
rc=$?
set -e
if [ "$rc" != 0 ]; then
    echo "TF_GITHUB_APPLY_NOT_CONVERGED exit=$rc"
    exit 1
fi
echo "TF_GITHUB_APPLY_CONVERGED"
