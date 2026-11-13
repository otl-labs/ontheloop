#!/usr/bin/env bash
# SessionStart hook: report the identity the proxy signs this session's AWS calls as.
#
# Credential model (Decision 255): the cloud-env "Setup script" field writes ~/.aws/config
# with placeholder credentials only (credential_process prints them); the environment's agent
# proxy holds the real agent-platform-session key as an AWS network secret and signs every call
# itself, so no session assumes a role. See the bin/setup-cloud-env.sh header for the exact field
# content and docs/contracts/deploy-paths.yaml admin_out_of_band.agent_session_identity.
#
# This hook does NOT create credentials. It runs every session start (including resume) and
# reports the identity via scripts.aws_identity, turning a silent AccessDenied into an obvious
# session-start message. Advisory only: always exits 0 so a missing or expired credential never
# blocks the session.
#
# Output carries the user name only, never the ARN, account ID or session name (rec-4656): the
# raw helper output is never echoed.

set -uo pipefail

log() { printf '[session_start_aws] %s\n' "$*"; }

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

# Profile resolution mirrors scripts/aws_profile.py: an explicit AWS_PROFILE wins
# (an environment may name another profile), otherwise default to agent_platform.
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

log "Verifying AWS profile '$PROFILE' (proxy-signed identity)..."
if out="$(cd "$REPO_ROOT" && "$REPO_ROOT/bin/venv-python" -m scripts.aws_identity caller-arn --profile "$PROFILE" 2>&1)"; then
    if printf '%s\n' "$out" | grep -qE ':user/agent-platform-session$'; then
        log "OK: user/agent-platform-session"
        log "  Note: if ops calls still fail with AccessDenied, a grant is missing -- a plan defect, never a local"
        log "  widening; see terraform/bootstrap/agent_session_user.tf."
    else
        log "WARNING: '$PROFILE' did not resolve to user/agent-platform-session -- see the bin/setup-cloud-env.sh header"
        log "  (setup field and network secret) and deploy-paths.yaml admin_out_of_band.agent_session_identity."
    fi
else
    diag="$(printf '%s\n' "$out" | grep -m1 '^caller-arn failed:')"
    log "WARNING: could not read the identity for '$PROFILE':"
    if [ -n "$diag" ]; then
        log "  $diag"
    else
        log "  helper exited with no diagnostic line; rerun: bin/venv-python -m scripts.aws_identity caller-arn --profile $PROFILE"
    fi
    log "  Check the environment's AWS network secret (agent-platform-session key and allowed hosts)."
fi

exit 0
