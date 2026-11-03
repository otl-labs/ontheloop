#!/usr/bin/env bash
# SessionStart hook: report the assumed-role identity for the AWS profile chain.
#
# Credential model (personal account, CC-web-primary): the cloud-env "Setup script"
# field writes ~/.aws/config -- an agent_static profile whose credential_process prints
# placeholders (the agent proxy holds the real key as an AWS network secret) plus an
# agent_platform (PlatformDev) / agent_platform_admin (PlatformAdmin) assume-role
# profile. See bin/setup-cloud-env.sh header for the exact field content.
#
# This hook does NOT create credentials. It runs every session start (including
# resume) and reports the identity via scripts.aws_identity (an unpinned
# `aws sts get-caller-identity` returns the IAM user under the proxy), turning a
# silent AccessDenied into an obvious session-start message. Advisory only: always
# exits 0 so a missing or expired credential never blocks the session.
#
# Output carries the role name only, never the ARN, account ID or session name (rec-4656): the
# raw helper output is never echoed.

set -uo pipefail

log() { printf '[session_start_aws] %s\n' "$*"; }

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

# Profile resolution mirrors scripts/aws_profile.py: an explicit AWS_PROFILE wins
# (the admin env sets agent_platform_admin), otherwise default to agent_platform.
PROFILE="${AWS_PROFILE:-agent_platform}"

if [ ! -f "$HOME/.aws/config" ]; then
    log "WARNING: ~/.aws not materialised -- AWS calls will fail."
    log "  Fix: the cloud-env Setup script must write ~/.aws/config (see bin/setup-cloud-env.sh header)."
    exit 0
fi

if [ ! -x "$REPO_ROOT/bin/venv-python" ] || ! "$REPO_ROOT/bin/venv-python" -c "import boto3" >/dev/null 2>&1; then
    log "venv not ready yet (setup-cloud-env.sh creates it) -- skipping credential check."
    exit 0
fi

log "Verifying AWS profile '$PROFILE' (assume-role chain)..."
if out="$(cd "$REPO_ROOT" && "$REPO_ROOT/bin/venv-python" -m scripts.aws_identity caller-arn --profile "$PROFILE" 2>&1)"; then
    role="$(printf '%s\n' "$out" | grep -m1 ':assumed-role/' | sed -n 's|.*assumed-role/\([^/]*\)/.*|\1|p')"
    if [[ "$role" =~ ^[A-Za-z0-9+=,.@_-]+$ ]]; then
        log "OK: assumed-role/$role"
        log "  Note: if ops calls still fail with AccessDenied, the role's runtime grant is missing --"
        log "  see terraform/bootstrap/platform_dev_policies.tf."
    else
        log "WARNING: '$PROFILE' did not resolve to an assumed role -- see bin/setup-cloud-env.sh header (proxy and profile chain)."
    fi
else
    diag="$(printf '%s\n' "$out" | grep -m1 '^caller-arn failed:')"
    log "WARNING: could not assume '$PROFILE':"
    if [ -n "$diag" ]; then
        log "  $diag"
    else
        log "  helper exited with no diagnostic line; rerun: bin/venv-python -m scripts.aws_identity caller-arn --profile $PROFILE"
    fi
    log "  Check the environment's AWS network secret (agent_static key, host sts.eu-west-2.amazonaws.com)"
    log "  and the external_id in ~/.aws/config."
fi

exit 0
